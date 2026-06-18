"""github_url validation: clone URLs must not reach git's command-executing
helpers (ext::/fd::), local file:// paths, or argument injection via a leading
dash. Covers the validator directly and through the create API."""

import pytest
from httpx import AsyncClient

from vigilus.schemas.mcp import validate_github_url

SAFE_URLS = [
    "https://github.com/owner/repo.git",
    "http://example.com/repo.git",
    "git://github.com/owner/repo.git",
    "ssh://git@github.com/owner/repo.git",
    "git@github.com:owner/repo.git",
]

UNSAFE_URLS = [
    "ext::sh -c 'touch /tmp/pwned'",
    "fd::17/foo",
    "file:///etc/passwd",
    "-oProxyCommand=touch /tmp/pwned",
    "--upload-pack=touch /tmp/pwned",
    "ftp://example.com/repo.git",
]


@pytest.mark.parametrize("url", SAFE_URLS)
def test_safe_urls_pass(url):
    assert validate_github_url(url) == url


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_github_url(url)


def test_empty_and_none_pass_through():
    assert validate_github_url(None) is None
    assert validate_github_url("   ") is None
    # surrounding whitespace is stripped
    assert validate_github_url("  https://github.com/o/r.git  ") == "https://github.com/o/r.git"


@pytest.mark.asyncio
async def test_create_api_rejects_unsafe_github_url(async_client: AsyncClient):
    res = await async_client.post(
        "/api/mcp-servers",
        json={"name": "evil", "command": "echo", "github_url": "ext::sh -c id"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_api_accepts_safe_github_url(async_client: AsyncClient):
    res = await async_client.post(
        "/api/mcp-servers",
        json={
            "name": "ok",
            "command": "echo",
            "github_url": "https://github.com/owner/repo.git",
        },
    )
    assert res.status_code == 200
    assert res.json()["github_url"] == "https://github.com/owner/repo.git"
