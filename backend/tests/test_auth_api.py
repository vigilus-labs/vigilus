"""Integration tests for the auth API endpoints."""

from __future__ import annotations

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_USER = {"username": "admin", "password": "strongpassword1"}


async def _setup(client):
    r = await client.post("/api/auth/setup", json=VALID_USER)
    assert r.status_code == 200
    return r


# ── Unauthenticated access ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_protected_route_requires_auth(unauthenticated_client):
    r = await unauthenticated_client.get("/api/providers")
    assert r.status_code == 401


# ── Setup flow ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_status_empty_db(unauthenticated_client):
    r = await unauthenticated_client.get("/api/auth/setup")
    assert r.status_code == 200
    assert r.json()["needs_setup"] is True


@pytest.mark.asyncio
async def test_setup_creates_user_and_sets_cookie(unauthenticated_client):
    r = await _setup(unauthenticated_client)
    assert r.json()["username"] == "admin"
    assert "vigilus_token" in unauthenticated_client.cookies


@pytest.mark.asyncio
async def test_setup_allows_authed_requests(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_setup_second_call_returns_409(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.post("/api/auth/setup", json=VALID_USER)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_setup_weak_password_returns_422(unauthenticated_client):
    r = await unauthenticated_client.post(
        "/api/auth/setup", json={"username": "admin", "password": "short"}
    )
    assert r.status_code == 422


# ── Login / logout ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrongpassword"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password."


@pytest.mark.asyncio
async def test_login_correct_password_returns_200(unauthenticated_client):
    await _setup(unauthenticated_client)
    # Clear cookie so we can test a fresh login
    unauthenticated_client.cookies.clear()
    r = await unauthenticated_client.post("/api/auth/login", json=VALID_USER)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert "vigilus_token" in unauthenticated_client.cookies


@pytest.mark.asyncio
async def test_login_lockout_after_failures(unauthenticated_client):
    await _setup(unauthenticated_client)
    unauthenticated_client.cookies.clear()
    for _ in range(5):
        await unauthenticated_client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong1234!"}
        )
    r = await unauthenticated_client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong1234!"}
    )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_logout_clears_cookie(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.post("/api/auth/logout")
    assert r.status_code == 204
    # After logout, /me should be 401
    r = await unauthenticated_client.get("/api/auth/me")
    assert r.status_code == 401


# ── Change password ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_wrong_current_returns_401(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.post(
        "/api/auth/change-password",
        json={"current_password": "wrongpassword", "new_password": "newpassword123"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_change_password_weak_new_password_returns_422(unauthenticated_client):
    await _setup(unauthenticated_client)
    r = await unauthenticated_client.post(
        "/api/auth/change-password",
        json={"current_password": "strongpassword1", "new_password": "short"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_change_password_invalidates_old_token(unauthenticated_client):
    await _setup(unauthenticated_client)
    # Capture token before change
    old_token = unauthenticated_client.cookies.get("vigilus_token")

    r = await unauthenticated_client.post(
        "/api/auth/change-password",
        json={"current_password": "strongpassword1", "new_password": "newpassword123"},
    )
    assert r.status_code == 204

    # Old token should be rejected
    r = await unauthenticated_client.get(
        "/api/auth/me", headers={"Cookie": f"vigilus_token={old_token}"}
    )
    assert r.status_code == 401
