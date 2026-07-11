"""Memory native tool handlers — let operators learn the environment.

`memory_save` persists a durable fact (what a server does, what it runs,
quirks discovered while working). `memory_forget` removes one that turned
out to be wrong. Saved memories are injected into the operator's system
prompt on every future run.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def memory_save(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Save a durable memory.

    Args (from tool schema):
        content: The fact to remember (one concise sentence or two).
        scope: "global" (default — visible to all agents) or "self"
               (private to this operator).
        category: Optional label, e.g. "server", "service", "preference".
    """
    from vigilus.core.memory import save_memory

    content = (arguments.get("content") or "").strip()
    if not content:
        return {"error": "content is required"}
    if db is None:
        return {"error": "memory store unavailable"}

    scope_arg = arguments.get("scope", "global")
    if scope_arg == "self" and operator is not None:
        scope = operator.id
    else:
        scope = "global"

    memory = await save_memory(
        db,
        scope=scope,
        content=content,
        category=arguments.get("category"),
        source=getattr(operator, "name", None),
    )
    await db.commit()
    return {
        "saved": True,
        "id": memory.id if memory else None,
        "scope": "self" if scope != "global" else "global",
        "content": content,
    }


async def memory_forget(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Delete a memory by ID, or by exact content match within reachable scopes."""
    from sqlalchemy import select

    from vigilus.db.models import Memory

    if db is None:
        return {"error": "memory store unavailable"}

    memory_id = arguments.get("memory_id")
    content = (arguments.get("content") or "").strip()
    if not memory_id and not content:
        return {"error": "memory_id or content is required"}

    reachable = ["global"]
    if operator is not None:
        reachable.append(operator.id)

    memory = None
    if memory_id:
        memory = await db.get(Memory, memory_id)
        if memory and memory.scope not in reachable:
            return {"error": "Memory not found in your scopes."}
    else:
        memory = (
            (
                await db.execute(
                    select(Memory).where(Memory.scope.in_(reachable), Memory.content == content)
                )
            )
            .scalars()
            .first()
        )

    if not memory:
        return {"error": "Memory not found."}

    deleted_content = memory.content
    await db.delete(memory)
    await db.commit()
    return {"forgotten": True, "content": deleted_content}
