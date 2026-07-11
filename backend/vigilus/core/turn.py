"""Shared orchestrator turn — the one place a turn runs.

Extracts the build → orchestrate → persist sequence that was previously
inlined in ``api/chat.py`` (``send_message``) and ``core/scheduler.py``
(``execute_scheduled_task``) so the channel gateway reuses identical
behaviour. Three front doors, one code path.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.compressor import ContextCompressor
from vigilus.core.orchestrator import (
    load_orchestrator_config,
    resolve_orchestrator_provider,
)
from vigilus.core.prompt_builder import PromptBuilder
from vigilus.db.models import Message, MessageRole, Session

logger = structlog.get_logger(__name__)


async def run_turn(
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
) -> str:
    """Persist the user message, run the orchestrator to completion, persist the
    replies, and return the final assistant text.

    Shared by chat (optionally — see note), scheduler, and the channel gateway.
    Raises ``OrchestratorNotConfigured`` if no provider is set up.

    Args:
        db: Active async DB session (caller commits are handled inside).
        session: The ``Session`` row this turn belongs to.
        user_text: The text to feed the orchestrator as a user message.
        bridge: Optional ``StreamBridge`` for live SSE-style events.
        cancel_event: Optional ``asyncio.Event``; the loop stops when set.
        system_extra: Extra text appended to the rendered system prompt.
        save_user_message: Persist ``user_text`` as a ``Message`` row first.
        auto_title: Auto-title an untitled/"New Chat" session from the first
            line of ``user_text``. Callers that set a custom title (e.g. the
            scheduler) should pass ``False``.
    """
    # Imported here to avoid a circular import (api.chat imports core modules).
    from vigilus.api.chat import _load_db_messages_as_llm, _run_orchestrator

    provider, _row, model = await resolve_orchestrator_provider(db)
    cfg = load_orchestrator_config()

    builder = PromptBuilder(db=db, custom_identity=cfg.custom_identity, soul=cfg.soul)
    prompt_obj = await builder.build(session_id=session.id)
    system_prompt = prompt_obj.render()
    if system_extra:
        system_prompt += "\n\n" + system_extra

    if save_user_message:
        db.add(Message(session_id=session.id, role=MessageRole.user, content=user_text))
        if auto_title and (not session.title or session.title == "New Chat"):
            first = user_text.strip().splitlines()[0] if user_text.strip() else ""
            if first:
                session.title = (first[:57] + "…") if len(first) > 60 else first
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
    llm_history = _load_db_messages_as_llm(list(rows))

    compressor = ContextCompressor(provider=provider, model=model)
    llm_history, summary = await compressor.compress_if_needed(
        llm_history, system_tokens=len(system_prompt) // 4
    )
    if summary:
        prompt_obj = await builder.rebuild_volatile(
            prompt_obj,
            memory_context=("[Previous conversation was compressed. Summary:]\n" + summary),
        )
        system_prompt = prompt_obj.render()
        if system_extra:
            system_prompt += "\n\n" + system_extra

    new_msgs = await _run_orchestrator(
        llm_history,
        provider,
        system_prompt,
        db=db,
        session_id=session.id,
        bridge=bridge,
        cancel_event=cancel_event,
        unattended=unattended,
    )

    final_text = ""
    for m in new_msgs:
        role = MessageRole(m["role"])
        db.add(
            Message(
                session_id=session.id,
                role=role,
                content=m["content"],
                operator_id=m.get("operator_id"),
            )
        )
        if role == MessageRole.assistant and isinstance(m["content"], str):
            final_text = m["content"]
    await db.commit()
    return final_text
