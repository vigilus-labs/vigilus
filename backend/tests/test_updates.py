"""Tests for the update-check service (core/updates.py)."""

from __future__ import annotations

import pytest

from vigilus import __version__
from vigilus.config import get_settings
from vigilus.core import updates


def test_parse_version_strips_prefix_and_metadata():
    assert updates._parse_version("v1.2.3") == (1, 2, 3)
    assert updates._parse_version("1.2.3-rc1") == (1, 2, 3)
    assert updates._parse_version("v0.10.0+build5") == (0, 10, 0)


def test_is_newer_compares_numerically():
    assert updates._is_newer("0.2.0", "0.1.0") is True
    assert updates._is_newer("v0.1.1", "0.1.0") is True
    assert updates._is_newer("0.10.0", "0.9.0") is True  # not lexical
    assert updates._is_newer("0.1.0", "0.1.0") is False
    assert updates._is_newer("0.1.0", "0.2.0") is False


@pytest.fixture(autouse=True)
def _reset_cache():
    updates._cache = None
    updates._cache_at = 0.0
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_disabled_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("VIGILUS_UPDATE_CHECK", "false")
    get_settings.cache_clear()

    async def _boom(*a, **k):
        raise AssertionError("network call made while disabled")

    monkeypatch.setattr(updates, "_fetch_latest", _boom)

    status = await updates.get_update_status()
    assert status["check_enabled"] is False
    assert status["update_available"] is False
    assert status["current_version"] == __version__


async def test_detects_available_update(monkeypatch):
    payload = {
        "tag_name": "v9.9.9",
        "name": "Vigilus 9.9.9",
        "html_url": "https://github.com/vigilus-labs/vigilus/releases/tag/v9.9.9",
        "published_at": "2026-06-20T00:00:00Z",
    }

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(updates.httpx, "AsyncClient", _Client)

    status = await updates.get_update_status(force=True)
    assert status["update_available"] is True
    assert status["latest_version"] == "9.9.9"
    assert status["release_url"].endswith("v9.9.9")
    assert status["error"] is None


async def test_network_failure_is_soft(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("dns boom")

    monkeypatch.setattr(updates.httpx, "AsyncClient", _Client)

    status = await updates.get_update_status(force=True)
    assert status["error"] is not None
    assert status["update_available"] is False
