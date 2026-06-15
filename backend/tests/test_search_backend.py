"""Search backend parsing + registry tests (mocked httpx)."""

from __future__ import annotations

import pytest

from vigilus.search.base import SearchError
from vigilus.search.firecrawl import FirecrawlBackend
from vigilus.search.searxng import SearxngBackend


class _FakeResponse:
    def __init__(self, *, json_data=None, headers=None, status_code=200, raise_json=False):
        self._json = json_data
        self.headers = headers or {"content-type": "application/json"}
        self.status_code = status_code
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._json


class _FakeClient:
    """Stand-in for httpx.AsyncClient that returns a canned response."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        return self._response

    async def post(self, *args, **kwargs):
        return self._response


def _patch_httpx(monkeypatch, module, response: _FakeResponse):
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: _FakeClient(response))


async def test_searxng_parses_results(monkeypatch):
    from vigilus.search import searxng

    resp = _FakeResponse(
        json_data={
            "answers": [{"answer": "42", "url": "http://a"}],
            "results": [
                {"title": "T1", "url": "http://1", "content": "snippet one"},
                {"title": "T2", "url": "http://2", "content": "snippet two"},
            ],
        }
    )
    _patch_httpx(monkeypatch, searxng, resp)

    backend = SearxngBackend("http://searxng.lan:8080")
    results = await backend.search("hello", max_results=5)

    assert results[0].title == "Answer"
    assert any(r.url == "http://1" and r.snippet == "snippet one" for r in results)


async def test_searxng_html_response_raises_json_hint(monkeypatch):
    from vigilus.search import searxng

    resp = _FakeResponse(headers={"content-type": "text/html"}, json_data=None)
    _patch_httpx(monkeypatch, searxng, resp)

    backend = SearxngBackend("http://searxng.lan:8080")
    with pytest.raises(SearchError) as exc:
        await backend.search("hello")
    assert "json" in str(exc.value).lower()


async def test_searxng_requires_url():
    with pytest.raises(SearchError):
        SearxngBackend("")


async def test_firecrawl_search_parses(monkeypatch):
    from vigilus.search import firecrawl

    resp = _FakeResponse(
        json_data={
            "success": True,
            "data": {"web": [{"url": "http://x", "title": "X", "description": "desc"}]},
            "creditsUsed": 10,
        }
    )
    _patch_httpx(monkeypatch, firecrawl, resp)

    backend = FirecrawlBackend("fc-key")
    results = await backend.search("q", max_results=5)
    assert len(results) == 1
    assert results[0].snippet == "desc"


async def test_firecrawl_scrape_parses(monkeypatch):
    from vigilus.search import firecrawl

    resp = _FakeResponse(
        json_data={
            "success": True,
            "data": {
                "markdown": "# Title\nbody",
                "metadata": {"title": "Title", "sourceURL": "http://x", "statusCode": 200},
            },
        }
    )
    _patch_httpx(monkeypatch, firecrawl, resp)

    backend = FirecrawlBackend("fc-key")
    page = await backend.fetch("http://x")
    assert page.title == "Title"
    assert "body" in page.text
    assert page.status_code == 200


async def test_firecrawl_unauthorized(monkeypatch):
    from vigilus.search import firecrawl

    resp = _FakeResponse(status_code=401, json_data={"success": False})
    _patch_httpx(monkeypatch, firecrawl, resp)

    backend = FirecrawlBackend("bad-key")
    with pytest.raises(SearchError) as exc:
        await backend.search("q")
    assert "401" in str(exc.value)


async def test_firecrawl_requires_key():
    with pytest.raises(SearchError):
        FirecrawlBackend("")
