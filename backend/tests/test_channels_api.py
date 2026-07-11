"""Tests for the channels admin API."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_gateway_reload(monkeypatch):
    """The upsert/delete endpoints reload the in-process gateway (which would try
    to connect to Telegram). Stub reload/start so tests stay offline and fast."""
    from vigilus.integrations import gateway as gw_mod

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(gw_mod.GatewayManager, "reload", _noop)
    monkeypatch.setattr(gw_mod.GatewayManager, "start", _noop)
    monkeypatch.setattr(gw_mod.GatewayManager, "shutdown", _noop)


@pytest.mark.asyncio
async def test_list_configs_empty(async_client):
    resp = await async_client.get("/api/channels")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_upsert_requires_token(async_client):
    resp = await async_client.put("/api/channels/telegram", json={"enabled": True})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upsert_encrypts_token_and_hides_it(async_client):
    resp = await async_client.put(
        "/api/channels/telegram",
        json={"bot_token": "123:SECRET", "enabled": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform"] == "telegram"
    assert body["has_token"] is True
    # Token value must never leak through the API.
    assert "123:SECRET" not in resp.text

    # Listed back without the token.
    listing = (await async_client.get("/api/channels")).json()
    assert len(listing) == 1
    assert listing[0]["has_token"] is True


@pytest.mark.asyncio
async def test_unknown_platform_rejected(async_client):
    resp = await async_client.put("/api/channels/slack", json={"bot_token": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_config(async_client):
    await async_client.put("/api/channels/telegram", json={"bot_token": "tok"})
    resp = await async_client.delete("/api/channels/telegram")
    assert resp.status_code == 200
    assert (await async_client.get("/api/channels")).json() == []


@pytest.mark.asyncio
async def test_account_crud(async_client):
    # Create an allowed account.
    resp = await async_client.post(
        "/api/channels/accounts",
        json={
            "platform": "telegram",
            "external_user_id": "42",
            "allowed": True,
            "label": "me",
        },
    )
    assert resp.status_code == 200, resp.text
    acct = resp.json()
    assert acct["allowed"] is True
    assert acct["label"] == "me"
    acct_id = acct["id"]

    # List.
    listing = (await async_client.get("/api/channels/accounts")).json()
    assert len(listing) == 1

    # Revoke (upsert with allowed=False).
    revoked = (
        await async_client.post(
            "/api/channels/accounts",
            json={
                "platform": "telegram",
                "external_user_id": "42",
                "allowed": False,
            },
        )
    ).json()
    assert revoked["allowed"] is False

    # Delete.
    assert (await async_client.delete(f"/api/channels/accounts/{acct_id}")).status_code == 200
    assert (await async_client.get("/api/channels/accounts")).json() == []


@pytest.mark.asyncio
async def test_account_unknown_platform_rejected(async_client):
    resp = await async_client.post(
        "/api/channels/accounts",
        json={
            "platform": "signal",
            "external_user_id": "1",
            "allowed": True,
        },
    )
    assert resp.status_code == 400
