"""SSRF protection tests for the builtin fetcher (security-critical, plan §7/§10).

Asserts the validator blocks loopback / RFC1918 / link-local / metadata /
non-http schemes, blocks hostnames that resolve to internal addresses, follows
the allow_private override, and fails closed on resolution errors.
"""

from __future__ import annotations

import socket

import pytest

from vigilus.search.base import SearchError
from vigilus.search.builtin_fetch import (
    BuiltinFetchBackend,
    html_to_text,
    validate_public_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",  # resolves to loopback
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://[fd00:ec2::254]/",  # IPv6 metadata
        "http://0.0.0.0/",
        "http://[::1]/",  # IPv6 loopback
    ],
)
def test_blocks_internal_addresses(url):
    with pytest.raises(SearchError):
        validate_public_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/", "file:///etc/passwd", "gopher://x/", "data:text/plain,hi"],
)
def test_blocks_non_http_schemes(url):
    with pytest.raises(SearchError):
        validate_public_url(url, allow_private=False)


def test_blocks_hostname_resolving_internal(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SearchError):
        validate_public_url("http://evil.example.com/", allow_private=False)


def test_allows_public_hostname(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_public_url("http://example.com/", allow_private=False) == "example.com"


def test_allow_private_override_permits_loopback():
    # The deliberate config flip (decision #4 default off) lets internal URLs through.
    assert validate_public_url("http://127.0.0.1/", allow_private=True) == "127.0.0.1"


def test_fail_closed_on_resolution_error(monkeypatch):
    def boom(host, port, *a, **k):
        raise socket.gaierror("no dns")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(SearchError):
        validate_public_url("http://unresolvable.example/", allow_private=False)


def test_url_with_no_host_blocked():
    with pytest.raises(SearchError):
        validate_public_url("http:///nopath", allow_private=False)


async def test_fetch_rejects_internal_url():
    backend = BuiltinFetchBackend(allow_private=False)
    with pytest.raises(SearchError):
        await backend.fetch("http://169.254.169.254/latest/meta-data/")


def test_html_to_text_strips_scripts():
    html = (
        "<html><head><title>Hi</title></head><body>"
        "<script>alert('x')</script><p>Hello world</p>"
        "<style>.a{}</style></body></html>"
    )
    title, text = html_to_text(html)
    assert title == "Hi"
    assert "Hello world" in text
    assert "alert" not in text
    assert ".a{}" not in text
