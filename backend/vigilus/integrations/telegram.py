"""Telegram adapter — Bot API long-poll over httpx.

No new dependency: httpx is already a core dep. Long-polling means we need no
public URL or webhook. Inline keyboards (Phase 3/4) are still possible by
sending ``reply_markup`` JSON in raw Bot API calls.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from vigilus.integrations.base import ChannelAdapter, InboundMessage, OnMessage
from vigilus.integrations.chunking import chunk_text

logger = structlog.get_logger(__name__)
_URL = "https://api.telegram.org/bot{token}/{method}"


def _extract_attachments(msg: dict) -> list[dict]:
    """Pull file metadata (photo/document) from a Telegram message."""
    out: list[dict] = []
    doc = msg.get("document")
    if doc:
        out.append({
            "file_id": doc.get("file_id"),
            "filename": doc.get("file_name"),
            "mime_type": doc.get("mime_type"),
            "size": doc.get("file_size"),
        })
    # Photos come as size variants; keep the largest.
    photos = msg.get("photo") or []
    if photos:
        biggest = max(photos, key=lambda p: p.get("file_size", 0))
        out.append({
            "file_id": biggest.get("file_id"),
            "filename": "photo.jpg",
            "mime_type": "image/jpeg",
            "size": biggest.get("file_size"),
        })
    return out


class TelegramAdapter(ChannelAdapter):
    platform = "telegram"

    def __init__(self, token: str, on_message: OnMessage):
        self._token = token
        self._on_message = on_message
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._offset = 0
        self._running = False
        self._bot_username: str | None = None
        self._jit_resolver = None
        # In-flight per-update handlers. Updates are dispatched concurrently so
        # the poll loop never blocks on a long turn — critical for JIT, where a
        # paused turn waits on an Approve/Deny callback that the same loop must
        # keep polling for (otherwise: deadlock + a button stuck "loading").
        self._handlers: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=70.0)
        me = await self._call("getMe")
        self._bot_username = (me.get("result") or {}).get("username")
        # This adapter is long-poll only; a leftover webhook would make every
        # getUpdates return 409 Conflict. Clear it best-effort before polling.
        try:
            await self._call("deleteWebhook")
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram.delete_webhook_failed", error=self._redact(str(e)))
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("telegram.started", bot=self._bot_username)
        # Best-effort: register the shared slash commands as native autocomplete.
        await self._register_commands()

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Cancel any in-flight update handlers so they don't outlive shutdown.
        for handler in list(self._handlers):
            handler.cancel()
        self._handlers.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                resp = await self._call("getUpdates", {
                    "offset": self._offset,
                    "timeout": 60,
                    "allowed_updates": ["message", "callback_query"],
                })
                for upd in resp.get("result", []):
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if msg and "text" in msg:
                        # Dispatch concurrently: a turn that pauses for JIT must
                        # not block the loop from polling for the approval tap.
                        self._dispatch(self._on_message(self._to_inbound(msg)))
                    cbq = upd.get("callback_query")
                    if cbq:
                        self._dispatch(self._handle_callback(cbq))
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("telegram.poll_error", error=self._redact(str(e)))
                await asyncio.sleep(3)

    def _dispatch(self, coro) -> None:
        """Run an update handler as a tracked background task (keeps the loop free)."""
        task = asyncio.create_task(self._run_handler(coro))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _run_handler(self, coro) -> None:
        try:
            await coro
        except Exception as e:  # noqa: BLE001 — one bad update must not kill the loop
            logger.warning("telegram.handler_error", error=str(e))

    async def send_jit_prompt(self, chat_id: str, text: str, request_id: str) -> None:
        """Send a JIT prompt with inline Approve/Deny buttons."""
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"jit:approve:{request_id}"},
                {"text": "⛔ Deny", "callback_data": f"jit:deny:{request_id}"},
            ]]
        }
        try:
            await self._call("sendMessage", {
                "chat_id": chat_id, "text": text, "reply_markup": keyboard,
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram.jit_prompt_failed", error=str(e))
            await self.send(chat_id, text)

    async def _handle_callback(self, cbq: dict) -> None:
        """Resolve an inline-keyboard Approve/Deny tap for a JIT request."""
        data = cbq.get("data", "")
        cbq_id = cbq.get("id", "")
        frm = cbq.get("from", {})
        approver = f"telegram:{frm.get('username') or frm.get('id')}"
        try:
            await self._call("answerCallbackQuery", {"callback_query_id": cbq_id})
        except Exception:  # noqa: BLE001
            pass
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "jit":
            return
        approved = parts[1] == "approve"
        request_id = parts[2]
        if self._jit_resolver:
            try:
                await self._jit_resolver(request_id, approved, approver)
                verdict = "✅ Approved" if approved else "⛔ Denied"
            except Exception as e:  # noqa: BLE001
                verdict = f"⚠️ {e}"
            # Edit the prompt message to show the outcome.
            message = cbq.get("message") or {}
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if chat_id and message_id:
                try:
                    await self._call("editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": verdict,
                    })
                except Exception:  # noqa: BLE001
                    pass

    async def _register_commands(self) -> None:
        """Map the shared command registry to Telegram's / autocomplete."""
        from vigilus.core.commands import get_command_specs

        try:
            cmds = [
                {"command": c.name[:32].lower(), "description": (c.summary or c.usage)[:256]}
                for c in get_command_specs()
                if c.execution != "client"
            ]
            if cmds:
                await self._call("setMyCommands", {"commands": cmds})
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram.register_commands_failed", error=str(e))

    def _to_inbound(self, msg: dict) -> InboundMessage:
        chat = msg["chat"]
        frm = msg.get("from", {})
        text = msg.get("text", "")
        is_group = chat.get("type") in ("group", "supergroup")
        mentioned = bool(self._bot_username) and f"@{self._bot_username}".lower() in text.lower()
        attachments = _extract_attachments(msg)
        # If the message has no text but does have a caption (photo/document),
        # use it so the turn isn't empty.
        if not text:
            text = msg.get("caption", "")
        return InboundMessage(
            platform="telegram",
            chat_id=str(chat["id"]),
            user_id=str(frm.get("id", "")),
            user_name=frm.get("username") or frm.get("first_name") or "",
            text=text,
            is_group=is_group,
            mentioned=mentioned,
            attachments=attachments,
        )

    async def send(self, chat_id: str, text: str) -> None:
        for chunk in chunk_text(text, 4096):
            await self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    async def send_typing(self, chat_id: str) -> None:
        try:
            await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:  # noqa: BLE001
            pass

    async def send_status(self, chat_id: str, text: str) -> str | None:
        try:
            r = await self._call("sendMessage", {"chat_id": chat_id, "text": text})
            return str((r.get("result") or {}).get("message_id", ""))
        except Exception:  # noqa: BLE001
            return None

    async def edit_status(self, chat_id: str, handle: str | None, text: str) -> None:
        if not handle:
            return
        try:
            await self._call("editMessageText", {
                "chat_id": chat_id, "message_id": int(handle), "text": text,
            })
        except Exception:  # noqa: BLE001
            pass

    async def delete_status(self, chat_id: str, handle: str | None) -> None:
        if not handle:
            return
        try:
            await self._call("deleteMessage", {"chat_id": chat_id, "message_id": int(handle)})
        except Exception:  # noqa: BLE001
            pass

    def _redact(self, text: str) -> str:
        """Strip the bot token from a string before it can reach logs."""
        return text.replace(self._token, "***") if self._token else text

    async def _call(self, method: str, params: dict | None = None) -> dict:
        assert self._client is not None
        r = await self._client.post(_URL.format(token=self._token, method=method), json=params or {})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # httpx embeds the full request URL — which contains the bot token —
            # in the exception message. Re-raise with it redacted so callers
            # that log str(e) never leak the token.
            raise httpx.HTTPStatusError(
                self._redact(str(e)), request=e.request, response=e.response
            ) from None
        return r.json()
