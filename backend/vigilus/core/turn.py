"""Shared orchestrator turn — the one place a turn runs.

Web chat (``api/chat.py``), the scheduler (``core/scheduler.py``) and the
channel gateway (``integrations/router.py``) all run turns through here:
build the prompt → save the user message → compress → orchestrate → persist.
Front doors own only their transport (HTTP response, SSE bridge, task
registration, channel replies).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.compressor import ContextCompressor, resolve_context_window
from vigilus.core.orchestrator import (
    load_orchestrator_config,
    resolve_orchestrator_provider,
)
from vigilus.core.orchestrator_loop import load_db_messages_as_llm, run_orchestrator
from vigilus.core.prompt_builder import PromptBuilder
from vigilus.db.models import Message, MessageRole, Session

logger = structlog.get_logger(__name__)


@dataclass
class TurnResult:
    """What a turn produced.

    ``text`` is the last plain-text assistant reply (what the scheduler and
    channels send on). ``assistant_message`` is the last assistant row
    persisted, which can be a delegation plan when the turn stopped early.
    ``user_message`` is the saved user row, or ``None`` when the caller asked
    not to save one.
    """

    text: str
    assistant_message: Message | None
    user_message: Message | None


def turn_title(session: Session, user_text: str) -> str | None:
    """The title *session* has once a turn for *user_text* auto-titles it.

    Untitled / "New Chat" sessions take the first line of the message,
    trimmed to 60 characters; anything else keeps its title.
    """
    if session.title and session.title != "New Chat":
        return session.title
    first = user_text.strip().splitlines()[0] if user_text.strip() else ""
    if not first:
        return session.title
    return (first[:57] + "…") if len(first) > 60 else first


async def execute_turn(
    db: AsyncSession,
    session: Session,
    user_text: str,
    *,
    bridge=None,
    cancel_event=None,
    system_extra: str | None = None,
    save_user_message: bool = True,
    auto_title: bool = True,
    unattended: bool = False,
) -> TurnResult:
    """Persist the user message, run the orchestrator to completion, persist
    the replies, and return what was produced.

    Raises ``OrchestratorNotConfigured`` (before saving anything) if no
    provider is set up.

    Args:
        db: Active async DB session (commits are handled inside).
        session: The ``Session`` row this turn belongs to.
        user_text: The text to feed the orchestrator as a user message.
        bridge: Optional ``StreamBridge`` for live SSE-style events.
        cancel_event: Optional ``asyncio.Event``; the loop stops when set.
        system_extra: Extra text appended to the rendered system prompt,
            including after a compression rebuild.
        save_user_message: Persist ``user_text`` as a ``Message`` row first.
        auto_title: Auto-title an untitled/"New Chat" session (see
            :func:`turn_title`). Callers that set a custom title (e.g. the
            scheduler) should pass ``False``.
        unattended: Scheduled run — operators use the longer JIT wait.
    """
    provider, provider_row, model = await resolve_orchestrator_provider(db)
    cfg = load_orchestrator_config()

    builder = PromptBuilder(db=db, custom_identity=cfg.custom_identity, soul=cfg.soul)
    prompt_obj = await builder.build(session_id=session.id)
    system_prompt = prompt_obj.render()
    if system_extra:
        system_prompt += "\n\n" + system_extra

    user_message: Message | None = None
    if save_user_message:
        user_message = Message(session_id=session.id, role=MessageRole.user, content=user_text)
        db.add(user_message)
        if auto_title:
            session.title = turn_title(session, user_text)
        await db.commit()

    rows = (
        (
            await db.execute(
                select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    llm_history = load_db_messages_as_llm(list(rows))

    compressor = ContextCompressor(
        provider=provider,
        model=model,
        max_tokens=resolve_context_window(provider_row, model),
    )
    llm_history, summary = await compressor.compress_if_needed(
        llm_history, system_tokens=len(system_prompt) // 4
    )
    if summary:
        logger.info("turn.compressed", session_id=session.id)
        prompt_obj = await builder.rebuild_volatile(
            prompt_obj,
            memory_context=("[Previous conversation was compressed. Summary:]\n" + summary),
        )
        system_prompt = prompt_obj.render()
        if system_extra:
            system_prompt += "\n\n" + system_extra

    new_msgs = await run_orchestrator(
        llm_history,
        provider,
        system_prompt,
        db=db,
        session_id=session.id,
        provider_id=provider_row.id,
        provider_type=provider_row.type.value,
        model=model,
        bridge=bridge,
        cancel_event=cancel_event,
        unattended=unattended,
    )

    final_text = ""
    assistant_message: Message | None = None
    for m in new_msgs:
        role = MessageRole(m["role"])
        row = Message(
            session_id=session.id,
            role=role,
            content=m["content"],
            operator_id=m.get("operator_id"),
        )
        db.add(row)
        if role == MessageRole.assistant:
            assistant_message = row
            if isinstance(m["content"], str):
                final_text = m["content"]
    await db.commit()
    if assistant_message is not None:
        await db.refresh(assistant_message)

    return TurnResult(
        text=final_text, assistant_message=assistant_message, user_message=user_message
    )


async def run_turn(db: AsyncSession, session: Session, user_text: str, **kwargs: Any) -> str:
    """Run a turn and return the final assistant text.

    Takes the same keyword arguments as :func:`execute_turn`. Used by the
    scheduler and the channel gateway, which only need the reply text.
    """
    result = await execute_turn(db, session, user_text, **kwargs)
    return result.text
