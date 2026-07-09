"""Update checking against the published GitHub releases.

Vigilus ships as a GHCR Docker image tagged with semver releases (vX.Y.Z).
This module compares the running ``__version__`` against the latest GitHub
release and reports whether a newer version is available.

The check is a single outbound, unauthenticated GET to api.github.com. It is
opt-out (``VIGILUS_UPDATE_CHECK=false``) and the result is cached in-process so
we never hammer GitHub's rate limit. Applying the update is out of scope here:
git-managed installs run ``vigilus update`` (see ``cli.cmd_update``), Docker
installs pull the newer image.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

import httpx
import structlog

from vigilus import __version__
from vigilus.config import get_settings

logger = structlog.get_logger(__name__)

# In-process cache of the last successful/attempted check. Keyed by nothing —
# there is only ever one running version. Refreshed lazily once it ages past the
# configured interval (or on an explicit forced check).
_cache: dict | None = None
_cache_at: float = 0.0


def _parse_version(value: str) -> tuple[int, ...]:
    """Best-effort parse of a semver-ish string into a comparable int tuple.

    Strips a leading ``v`` and any pre-release/build metadata so ``v1.2.3-rc1``
    and ``1.2.3`` compare on their numeric core. Unparseable segments become 0
    rather than raising, so a malformed tag never crashes the check.
    """
    cleaned = value.strip().lstrip("vV")
    core = re.split(r"[-+]", cleaned, maxsplit=1)[0]
    parts: list[int] = []
    for segment in core.split("."):
        match = re.match(r"\d+", segment)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly greater version than ``current``."""
    a, b = _parse_version(latest), _parse_version(current)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a > b


def _image_base() -> str:
    settings = get_settings()
    return f"ghcr.io/{settings.update_repo}"


def _base_status() -> dict:
    """The fields that are always present, independent of the network call."""
    return {
        "current_version": __version__,
        "latest_version": None,
        "update_available": False,
        "release_url": None,
        "release_name": None,
        "published_at": None,
        "checked_at": None,
        "check_enabled": get_settings().update_check_enabled,
        "image": _image_base(),
        "error": None,
    }


async def _fetch_latest() -> dict:
    """Fetch + interpret the latest GitHub release. Never raises."""
    settings = get_settings()
    status = _base_status()
    url = f"https://api.github.com/repos/{settings.update_repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"vigilus/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            # No published releases yet — not an error, just nothing to report.
            status["checked_at"] = datetime.now(UTC).isoformat()
            return status
        resp.raise_for_status()
        data = resp.json()
        latest = (data.get("tag_name") or data.get("name") or "").strip()
        status["latest_version"] = latest.lstrip("vV") or None
        status["release_url"] = data.get("html_url")
        status["release_name"] = data.get("name") or latest or None
        status["published_at"] = data.get("published_at")
        status["update_available"] = bool(latest) and _is_newer(latest, __version__)
    except Exception as exc:  # noqa: BLE001 — best-effort, surface as a soft error
        logger.warning("updates.check_failed", error=str(exc))
        status["error"] = "Could not reach the update server."
    status["checked_at"] = datetime.now(UTC).isoformat()
    return status


async def get_update_status(force: bool = False) -> dict:
    """Return the cached update status, refreshing it if stale or forced.

    When update checking is disabled, returns the base status with no network
    call and ``check_enabled=False`` so the UI can explain why it's empty.
    """
    global _cache, _cache_at

    settings = get_settings()
    if not settings.update_check_enabled:
        return _base_status()

    age = time.monotonic() - _cache_at
    ttl = max(settings.update_check_interval_hours, 1) * 3600
    if not force and _cache is not None and age < ttl:
        return _cache

    _cache = await _fetch_latest()
    _cache_at = time.monotonic()
    return _cache
