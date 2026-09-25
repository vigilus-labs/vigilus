"""Integration tests for the auth API endpoints."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from vigilus.api.auth import auth_cookie_needs_exposure_warning, auth_cookie_secure_for_request


def _request(scheme: str = "http", proto: str | None = None) -> Request:
    headers = []
    if proto is not None:
        headers.append((b"x-forwarded-proto", proto.encode()))
    scope = {
        "type": "http",
        "scheme": scheme,
        "server": ("testserver", 443 if scheme == "https" else 80),
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)


def test_auth_cookie_secure_follows_scheme_and_forwarded_proto():
    assert auth_cookie_secure_for_request(_request()) is False
    assert auth_cookie_secure_for_request(_request(scheme="https")) is True
    assert auth_cookie_secure_for_request(_request(proto="https, http")) is True
    assert auth_cookie_secure_for_request(_request(proto="http")) is False


def test_auth_cookie_warning_only_for_non_loopback_without_force():
    assert auth_cookie_needs_exposure_warning("0.0.0.0", forced_secure=False) is True
    assert auth_cookie_needs_exposure_warning("127.0.0.1", forced_secure=False) is False
    assert auth_cookie_needs_exposure_warning("::1", forced_secure=False) is False
    assert auth_cookie_needs_exposure_warning("localhost", forced_secure=False) is False
    assert auth_cookie_needs_exposure_warning("0.0.0.0", forced_secure=True) is False


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


def _set_cookie(response) -> str:
    return "\n".join(response.headers.get_list("set-cookie"))


@pytest.mark.asyncio
async def test_auth_cookie_is_not_secure_on_plain_http(unauthenticated_client):
    r = await _setup(unauthenticated_client)
    cookie = _set_cookie(r)
    assert "vigilus_token=" in cookie
    assert "Secure" not in cookie


@pytest.mark.asyncio
async def test_auth_cookie_is_secure_behind_https_proxy(unauthenticated_client):
    r = await unauthenticated_client.post(
        "/api/auth/setup",
        json=VALID_USER,
        headers={"X-Forwarded-Proto": "https, http"},
    )
    assert r.status_code == 200
    cookie = _set_cookie(r)
    assert "Secure" in cookie

    # httpx drops Secure cookies on an http:// base URL, so replay the value.
    token = cookie.split("vigilus_token=", 1)[1].split(";", 1)[0]
    logout = await unauthenticated_client.post(
        "/api/auth/logout",
        headers={"X-Forwarded-Proto": "https", "Cookie": f"vigilus_token={token}"},
    )
    assert logout.status_code == 204
    assert "Secure" in _set_cookie(logout)


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
