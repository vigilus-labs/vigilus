"""Shared slash-command registry and the /api/commands endpoints."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_commands_includes_client_and_server(async_client):
    resp = await async_client.get("/api/commands")
    assert resp.status_code == 200
    commands = {c["name"]: c for c in resp.json()}

    for name in ("help", "new", "sessions", "switch", "model", "provider", "stop"):
        assert name in commands
        assert commands[name]["execution"] == "server"

    for name in ("login", "clear", "logout", "quit"):
        assert name in commands
        assert commands[name]["execution"] == "client"


async def _run(client, command, args="", session_id=None):
    resp = await client.post(
        "/api/commands/run",
        json={"command": command, "args": args, "session_id": session_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_unknown_command(async_client):
    result = await _run(async_client, "bogus")
    assert result["kind"] == "error"
    assert "/bogus" in result["text"]


@pytest.mark.asyncio
async def test_client_command_rejected_server_side(async_client):
    result = await _run(async_client, "login")
    assert result["kind"] == "error"


@pytest.mark.asyncio
async def test_session_lifecycle_via_commands(async_client):
    # /new creates a session
    result = await _run(async_client, "new", "Test Chat")
    assert result["kind"] == "session_created"
    session = result["data"]["session"]
    assert session["title"] == "Test Chat"
    sid = session["id"]

    # /sessions lists it
    result = await _run(async_client, "sessions", session_id=sid)
    assert result["kind"] == "markdown"
    assert "Test Chat" in result["text"]
    assert any(s["id"] == sid for s in result["data"]["sessions"])

    # /switch by title prefix
    result = await _run(async_client, "switch", "test ch")
    assert result["kind"] == "session_switch"
    assert result["data"]["session"]["id"] == sid

    # /switch by number
    result = await _run(async_client, "switch", "1")
    assert result["kind"] == "session_switch"

    # /rename
    result = await _run(async_client, "rename", "Renamed Chat", session_id=sid)
    assert result["kind"] == "config_changed"
    assert result["data"]["session"]["title"] == "Renamed Chat"

    # /delete
    result = await _run(async_client, "delete", session_id=sid)
    assert result["kind"] == "session_deleted"
    assert result["data"]["session_id"] == sid

    result = await _run(async_client, "sessions")
    assert "Renamed Chat" not in result["text"]


@pytest.mark.asyncio
async def test_needs_session_enforced(async_client):
    result = await _run(async_client, "rename", "whatever")
    assert result["kind"] == "error"

    result = await _run(async_client, "rename", "x", session_id="nonexistent-id")
    assert result["kind"] == "error"


@pytest.mark.asyncio
async def test_model_show_and_set(async_client, tmp_path, monkeypatch):
    # Point orchestrator config at a temp dir so tests don't touch real data
    import vigilus.core.orchestrator as orch

    monkeypatch.setattr(orch, "_config_path", lambda: str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(orch, "_config_cache", None)

    result = await _run(async_client, "model")
    assert result["kind"] == "markdown"

    result = await _run(async_client, "model", "claude-opus-4-8")
    assert result["kind"] == "config_changed"
    assert "claude-opus-4-8" in result["text"]

    result = await _run(async_client, "model")
    assert "claude-opus-4-8" in result["text"]


@pytest.mark.asyncio
async def test_provider_not_found(async_client):
    result = await _run(async_client, "provider", "does-not-exist")
    assert result["kind"] == "error"


@pytest.mark.asyncio
async def test_provider_set(async_client, tmp_path, monkeypatch):
    import vigilus.core.orchestrator as orch

    monkeypatch.setattr(orch, "_config_path", lambda: str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(orch, "_config_cache", None)

    resp = await async_client.post(
        "/api/providers",
        json={
            "name": "Test Anthropic",
            "type": "anthropic",
            "api_key": "sk-ant-test",
            "default_model": "claude-opus-4-8",
        },
    )
    assert resp.status_code == 200, resp.text

    result = await _run(async_client, "provider", "test anthropic")
    assert result["kind"] == "config_changed"
    assert "Test Anthropic" in result["text"]

    result = await _run(async_client, "provider")
    assert "orchestrator" in result["text"]


@pytest.mark.asyncio
async def test_stop_with_nothing_running(async_client):
    result = await _run(async_client, "new", "Stoppable")
    sid = result["data"]["session"]["id"]
    result = await _run(async_client, "stop", session_id=sid)
    assert result["kind"] == "error"


@pytest.mark.asyncio
async def test_memory_add_list_rm(async_client):
    result = await _run(async_client, "memory", "add the firewall lives on host alpha")
    assert result["kind"] == "config_changed"

    result = await _run(async_client, "memory", "list")
    assert result["kind"] == "markdown"
    assert "firewall" in result["text"]

    # extract the id prefix from the listing: "- `xxxxxxxx` [scope] ..."
    prefix = result["text"].split("`")[1]
    result = await _run(async_client, "memory", f"rm {prefix}")
    assert result["kind"] == "config_changed"

    result = await _run(async_client, "memory", "list")
    assert "firewall" not in result["text"]


@pytest.mark.asyncio
async def test_help_lists_all_commands(async_client):
    result = await _run(async_client, "help")
    assert result["kind"] == "markdown"
    for name in ("/new", "/sessions", "/model", "/login", "/clear"):
        assert name in result["text"]


@pytest.mark.asyncio
async def test_provider_catalog_endpoint(async_client):
    resp = await async_client.get("/api/providers/catalog")
    assert resp.status_code == 200
    catalog = resp.json()["catalog"]
    ids = {entry["id"] for entry in catalog}
    assert {"anthropic", "openai", "openrouter", "google", "ollama", "custom"} <= ids
    anthropic = next(e for e in catalog if e["id"] == "anthropic")
    assert anthropic["needs_api_key"] is True
    assert anthropic["key_url"]
