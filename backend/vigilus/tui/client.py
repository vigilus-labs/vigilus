"""Async HTTP client for the Vigilus API with bearer-token auth."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from vigilus.tui.config import get_base_url, get_token


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class VIgilusClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self._base = (base_url or get_base_url()).rstrip("/") + "/api"
        self._token = token or get_token()

    @property
    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def set_token(self, token: str) -> None:
        self._token = token

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=30.0) as c:
            try:
                resp = await c.request(
                    method, f"{self._base}{path}", headers=self._headers, **kwargs
                )
            except httpx.ConnectError as exc:
                raise ApiError(f"Cannot reach server at {self._base}: {exc}") from exc
            if not resp.is_success:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise ApiError(detail, resp.status_code)
            if resp.status_code == 204:
                return None
            return resp.json()

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as c:
            try:
                resp = await c.post(
                    f"{self._base}/auth/token",
                    json={"username": username, "password": password},
                )
            except httpx.ConnectError as exc:
                raise ApiError(f"Cannot reach server: {exc}") from exc
            if not resp.is_success:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise ApiError(detail, resp.status_code)
            return resp.json()

    async def get_me(self) -> dict:
        return await self._req("GET", "/auth/me")

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def list_sessions(self) -> list[dict]:
        return await self._req("GET", "/sessions")

    async def create_session(self, title: str = "") -> dict:
        return await self._req("POST", "/sessions", json={"title": title})

    async def delete_session(self, session_id: str) -> None:
        await self._req("DELETE", f"/sessions/{session_id}")

    async def update_session(self, session_id: str, data: dict) -> dict:
        return await self._req("PATCH", f"/sessions/{session_id}", json=data)

    async def list_messages(self, session_id: str) -> list[dict]:
        return await self._req("GET", f"/sessions/{session_id}/messages")

    async def send_message(self, session_id: str, content: str) -> dict:
        return await self._req(
            "POST", f"/sessions/{session_id}/messages", json={"content": content}
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    async def list_commands(self) -> list[dict]:
        return await self._req("GET", "/commands")

    async def run_command(
        self, command: str, args: str = "", session_id: str | None = None
    ) -> dict:
        return await self._req(
            "POST",
            "/commands/run",
            json={"command": command, "args": args, "session_id": session_id},
        )

    # ── Providers ─────────────────────────────────────────────────────────────

    async def list_providers(self) -> list[dict]:
        return await self._req("GET", "/providers")

    async def get_provider_catalog(self) -> list[dict]:
        data = await self._req("GET", "/providers/catalog")
        return data.get("catalog", [])

    async def create_provider(self, data: dict) -> dict:
        return await self._req("POST", "/providers", json=data)

    async def test_provider(self, provider_id: str) -> dict:
        return await self._req("POST", f"/providers/{provider_id}/test")

    async def update_provider(self, provider_id: str, data: dict) -> dict:
        return await self._req("PATCH", f"/providers/{provider_id}", json=data)

    async def delete_provider(self, provider_id: str) -> None:
        await self._req("DELETE", f"/providers/{provider_id}")

    # ── Orchestrator ──────────────────────────────────────────────────────────

    async def get_orchestrator_config(self) -> dict:
        return await self._req("GET", "/orchestrator")

    async def update_orchestrator_config(self, data: dict) -> dict:
        return await self._req("PATCH", "/orchestrator", json=data)

    # ── SSE stream ────────────────────────────────────────────────────────────

    async def stream_session(self, session_id: str) -> AsyncIterator[dict]:
        """Yield parsed SSE event dicts from the session activity stream."""
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream(
                "GET",
                f"{self._base}/sessions/{session_id}/stream",
                headers=self._headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass
