"""Bearer-token auth: POST /auth/token + Authorization header acceptance."""

from __future__ import annotations

import pytest


async def _create_user(client, username="tui-user", password="a-strong-password"):
    resp = await client.post("/api/auth/setup", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return username, password


@pytest.mark.asyncio
async def test_token_mint_and_bearer_access(unauthenticated_client):
    username, password = await _create_user(unauthenticated_client)

    resp = await unauthenticated_client.post(
        "/api/auth/token", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == username
    assert body["token"]
    assert body["expires_at"]

    # Bearer header grants access to a protected route (no cookie involved)
    unauthenticated_client.cookies.clear()
    resp = await unauthenticated_client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert resp.status_code == 200

    # /auth/me works too
    resp = await unauthenticated_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == username


@pytest.mark.asyncio
async def test_token_bad_password_rejected(unauthenticated_client):
    username, _ = await _create_user(unauthenticated_client)
    resp = await unauthenticated_client.post(
        "/api/auth/token", json={"username": username, "password": "wrong-password"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_garbage_bearer_rejected(unauthenticated_client):
    await _create_user(unauthenticated_client)
    unauthenticated_client.cookies.clear()
    resp = await unauthenticated_client.get(
        "/api/sessions", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cookie_auth_still_works(unauthenticated_client):
    # /auth/setup sets the cookie on the client
    await _create_user(unauthenticated_client)
    resp = await unauthenticated_client.get("/api/auth/me")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stale_token_version_rejected(unauthenticated_client, db_session):
    from sqlalchemy import select

    from vigilus.db.models import User

    username, password = await _create_user(unauthenticated_client)
    resp = await unauthenticated_client.post(
        "/api/auth/token", json={"username": username, "password": password}
    )
    token = resp.json()["token"]

    user = (await db_session.execute(select(User).where(User.username == username))).scalar_one()
    user.token_version += 1
    await db_session.commit()

    unauthenticated_client.cookies.clear()
    resp = await unauthenticated_client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401
