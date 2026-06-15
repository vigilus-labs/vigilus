"""Channel router — the policy core.

One entry point, :func:`handle_inbound`, runs for every inbound message from
any adapter. It enforces, in order:

1. **Allowlist (default-deny)** — only mapped, allowed external users proceed.
2. **Group/guild mention-gating** — in groups, the bot only responds when
   @mentioned (unless the config opts into ``respond_in_groups``).
3. **Session continuity** — each (platform, chat) maps to one Vigilus Session.
4. **Slash commands** vs. **orchestrator chat** — ``/cmd`` runs a server
   command; anything else runs a full orchestrator turn via :func:`run_turn`.

``send`` is an async callable bound to the originating adapter so replies land
in the same conversation.
"""

from __future__ import annotations

import re

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from vigilus.core.commands import execute_command
from vigilus.core.orchestrator import OrchestratorNotConfigured
from vigilus.core.turn import run_turn
from vigilus.db.base import get_session_factory
from vigilus.db.models import ChannelAccount, ChannelChat, Session
from vigilus.integrations.base import ChannelAdapter, InboundMessage

logger = structlog.get_logger(__name__)

_DENY_REPLY = "⛔ You are not authorized to use this assistant."


class _StatusController:
    """Manages a single editable 'delegating…' status message for a turn.

    Also relays the orchestrator's plain-text plan to the channel: the
    orchestrator publishes its user-facing prose (``text_delta``) immediately
    before each delegation, so the user sees *what* is about to happen instead
    of waiting in silence until the operator finishes.

    The bridge callback is **synchronous** — ``StreamBridge.publish`` calls it
    inline, so an ``async`` callback's coroutine would be discarded, never
    awaited. State (the pending plan) is therefore mutated synchronously and in
    order, while the actual network sends are spawned as tasks and awaited in
    ``cleanup`` before the turn's final reply goes out.

    Edits are throttled to ≤ once per second. The status message is deleted
    when the turn completes (``cleanup``) so the final reply stands alone.
    """

    _EDIT_INTERVAL = 1.0

    def __init__(self, adapter: ChannelAdapter, chat_id: str) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._handle: str | None = None
        self._last_edit = 0.0
        self._pending_plan: str | None = None
        self._tasks: list = []

    def bridge(self):
        from vigilus.api.sse import EVT_DELEGATION_START, EVT_TEXT_DELTA, StreamBridge

        def _on_event(event: str, data: dict) -> None:
            if event == EVT_TEXT_DELTA:
                # Buffer the latest prose (synchronously). If a delegation
                # follows, this is the plan and gets sent; if not (the final
                # answer), the router sends it instead, so we never double-post.
                self._pending_plan = ((data or {}).get("text") or "").strip() or None
            elif event == EVT_DELEGATION_START:
                plan = self._pending_plan
                self._pending_plan = None
                op = (data or {}).get("operator", "…")
                self._spawn(self._announce(plan, op))

        return StreamBridge(on_event=_on_event)

    def _spawn(self, coro) -> None:
        import asyncio

        try:
            self._tasks.append(asyncio.create_task(coro))
        except RuntimeError:  # no running loop — nothing to relay to
            coro.close()

    async def _announce(self, plan: str | None, operator: str) -> None:
        if plan:
            # Drop the editable status first so the plan reads cleanly, then
            # re-show "delegating…" beneath it.
            await self._delete_status()
            await self._adapter.send(self._chat_id, plan)
        await self._set(f"🤖 delegating to *{operator}*…")

    async def _set(self, text: str) -> None:
        import time

        now = time.monotonic()
        if self._handle is None:
            self._handle = await self._adapter.send_status(self._chat_id, text)
            self._last_edit = now
        elif now - self._last_edit >= self._EDIT_INTERVAL:
            await self._adapter.edit_status(self._chat_id, self._handle, text)
            self._last_edit = now

    async def _delete_status(self) -> None:
        if self._handle is not None:
            await self._adapter.delete_status(self._chat_id, self._handle)
            self._handle = None

    async def cleanup(self) -> None:
        # Let any in-flight plan/status sends finish before the final reply.
        if self._tasks:
            import asyncio

            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []
        await self._delete_status()


async def handle_inbound(inbound: InboundMessage, adapter: ChannelAdapter) -> None:
    """Process one inbound message end to end.

    Args:
        inbound: The normalized inbound message.
        adapter: The originating :class:`ChannelAdapter` (used for typing,
            status, and replies so they land in the same conversation).
    """
    factory = get_session_factory()
    async with factory() as db:
        # 1. Allowlist — DEFAULT DENY. Reply in DMs, stay silent in groups.
        acct = (
            await db.execute(
                select(ChannelAccount).where(
                    ChannelAccount.platform == inbound.platform,
                    ChannelAccount.external_user_id == inbound.user_id,
                )
            )
        ).scalar_one_or_none()
        if acct is None or not acct.allowed:
            logger.info(
                "gateway.denied",
                platform=inbound.platform,
                user=inbound.user_id,
                name=inbound.user_name,
            )
            if not inbound.is_group:
                await adapter.send(inbound.chat_id, _DENY_REPLY)
            return

        # 2. Group/guild mention-gating.
        if inbound.is_group and not inbound.mentioned and not _responds_in_groups(db, inbound):
            return

        # 3. Resolve (or create) the session for this chat.
        session = await _resolve_session(db, inbound)
        text = _strip_bot_mention(inbound.text).strip()
        # Surface inbound attachments to the orchestrator. Binary content isn't
        # piped into the context yet, but the operator is told what was shared
        # so it can reason about (or retrieve) it on follow-up.
        if inbound.attachments:
            names = ", ".join(
                a.get("filename") or a.get("url") or "file" for a in inbound.attachments
            )
            text = (text + "\n" if text else "") + f"[User attached: {names}]"
        if not text:
            return

        logger.info(
            "gateway.inbound",
            platform=inbound.platform,
            user=inbound.user_name,
            session_id=session.id,
            is_command=text.startswith("/"),
        )

        # 4. Slash command vs orchestrator chat.
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            result = await execute_command(name, args.strip(), session.id, db)
            await adapter.send(inbound.chat_id, result.text or "✓ done")
            return

        # 5. Run the orchestrator turn with a live status indicator.
        status = _StatusController(adapter, inbound.chat_id)
        bridge = status.bridge()

        from vigilus.core.events import get_event_bus

        async def _on_jit_requested(payload: dict) -> None:
            req_id = (payload or {}).get("id")
            if not req_id:
                return
            op = (payload or {}).get("operator_name", "an operator")
            perm = (payload or {}).get("permission", "?")
            resource = (payload or {}).get("resource", "?")
            task_desc = (payload or {}).get("task_description", "")
            text = (
                f"🔐 *{op}* is requesting *{perm}* access to `{resource}`."
                + (f"\n_{task_desc}_" if task_desc else "")
                + "\n\nApprove this action?"
            )
            await adapter.send_jit_prompt(inbound.chat_id, text, req_id)

        event_bus = get_event_bus()
        event_bus.subscribe("jit.requested", _on_jit_requested)
        try:
            await adapter.send_typing(inbound.chat_id)
            final = await run_turn(db, session, text, bridge=bridge)
        except OrchestratorNotConfigured:
            await adapter.send(
                inbound.chat_id,
                "⚠️ No AI provider is configured yet. Set one up in the Vigilus web UI.",
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("gateway.turn_failed", error=str(e))
            await adapter.send(inbound.chat_id, f"⚠️ Something went wrong: {e}")
            return
        finally:
            event_bus.unsubscribe("jit.requested", _on_jit_requested)
            await status.cleanup()
        await adapter.send(inbound.chat_id, final or "(no response)")


async def send_typing_safe(send_typing_target, chat_id: str) -> None:
    """Deprecated hook kept for import compatibility."""
    return None


def _responds_in_groups(db, inbound: InboundMessage) -> bool:
    """Whether the platform config has opted into un-mentioned group replies.

    Reads the cached config flag. Defaults to False (mention-gated) so the bot
    never speaks unprompted in a shared channel.
    """
    from vigilus.integrations.gateway import get_gateway

    return get_gateway().responds_in_groups(inbound.platform)


async def _resolve_session(db, inbound: InboundMessage) -> Session:
    """Find or create the Vigilus Session backing a (platform, chat).

    Two edge cases this guards against, both of which otherwise surface as a
    ``uq_channel_chat`` UNIQUE violation on the channel_chats INSERT:

    * **Stale link** — the user deleted the session a chat was bound to. SQLite
      doesn't enforce ``ON DELETE CASCADE`` (no ``PRAGMA foreign_keys``), so the
      channel_chats row is orphaned, pointing at a now-gone session. We repoint
      the existing link to a fresh session instead of inserting a duplicate.
    * **Create race** — two near-simultaneous first messages (or an update
      redelivered after a restart) both miss the link and both INSERT it. We
      catch the violation and reuse whichever row won.
    """
    link = await _get_link(db, inbound)
    if link is not None:
        session = await db.get(Session, link.session_id)
        if session is not None:
            return session
        # Stale link: its session was deleted. Give the chat a fresh session and
        # repoint the existing link (an UPDATE — no UNIQUE collision).
        session = Session(title="New Chat", origin=inbound.platform)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        link.session_id = session.id
        await db.commit()
        return session

    session = Session(title="New Chat", origin=inbound.platform)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    session_id = session.id  # capture before any rollback expires the instance
    db.add(
        ChannelChat(
            platform=inbound.platform,
            external_chat_id=inbound.chat_id,
            session_id=session_id,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent handler created the link first. Reuse it and drop our
        # now-orphaned session.
        await db.rollback()
        winner = await _resolve_session(db, inbound)
        try:
            orphan = await db.get(Session, session_id)
            if orphan is not None and orphan.id != winner.id:
                await db.delete(orphan)
                await db.commit()
        except Exception:  # noqa: BLE001 — orphan cleanup is best-effort
            await db.rollback()
        return winner
    return session


async def _get_link(db, inbound: InboundMessage) -> ChannelChat | None:
    """Return the ChannelChat row for this (platform, chat), if any."""
    return (
        await db.execute(
            select(ChannelChat).where(
                ChannelChat.platform == inbound.platform,
                ChannelChat.external_chat_id == inbound.chat_id,
            )
        )
    ).scalar_one_or_none()


def _strip_bot_mention(text: str) -> str:
    """Remove a leading @bot mention (Telegram leaves "@bot …"; Discord strips)."""
    if text.startswith("@"):
        return re.sub(r"@\w+\s*", "", text, count=1)
    return text
