"""SPA history-fallback behavior of the production static mount.

A hard browser refresh on a client-side route (/chat, /servers, …) must serve
index.html so the React router can take over — not FastAPI's JSON 404.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from vigilus.main import SpaStaticFiles

INDEX_HTML = "<html><body>vigilus spa</body></html>"


@pytest.fixture
def spa_client(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (assets / "app-abc123.js").write_text("console.log('app');")

    app = FastAPI()

    @app.get("/api/known")
    async def known():
        return {"ok": True}

    app.mount("/", SpaStaticFiles(directory=str(dist), html=True), name="frontend")

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_root_serves_index(spa_client):
    async with spa_client as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.text == INDEX_HTML


async def test_client_route_refresh_serves_index(spa_client):
    async with spa_client as client:
        for path in ("/chat", "/servers", "/chat/some-session-id"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert resp.text == INDEX_HTML, path


async def test_real_asset_still_served(spa_client):
    async with spa_client as client:
        resp = await client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


async def test_missing_asset_404s_instead_of_index(spa_client):
    # A stale hashed bundle must NOT come back as index.html masquerading as JS.
    async with spa_client as client:
        resp = await client.get("/assets/app-stale999.js")
    assert resp.status_code == 404


async def test_unknown_api_path_stays_json_404(spa_client):
    async with spa_client as client:
        known = await client.get("/api/known")
        unknown = await client.get("/api/does-not-exist")
    assert known.status_code == 200
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Not Found"}
