"""Persistent agent memory — durable facts about the environment.

Memories let Vigilus and its operators learn the environment over time:
what servers do, what they run, quirks discovered during tasks. Each
memory belongs to a *scope*:

  - "global":       shared environment knowledge, visible to every agent
  - "orchestrator": private to the Vigilus orchestrator
  - <operator id>:  private to that operator

Operators write memories through the `memory_save` native tool; the
orchestrator (which has no tools) writes them by emitting a
``{"remember": "..."}`` JSON block that the chat loop parses here.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from sqlalchemy import select

from vigilus.db.models import Memory

logger = structlog.get_logger(__name__)

# Cap how many memories are injected into a prompt, newest first.
MAX_PROMPT_MEMORIES = 50

# Orchestrator remember blocks: fenced or inline, mirroring the delegation
# pattern in core/delegation.py.
_REMEMBER_RE = re.compile(
    r'```json\s*(\{[^`]*"remember"[^`]*\})\s*```' r'|(\{"remember"[^}]*\})',
    re.DOTALL,
)


async def get_memories(db, scopes: list[str], limit: int = MAX_PROMPT_MEMORIES) -> list[Memory]:
    """Fetch memories for the given scopes, oldest first (stable prompt order)."""
    result = await db.execute(
        select(Memory).where(Memory.scope.in_(scopes)).order_by(Memory.created_at).limit(limit)
    )
    return list(result.scalars().all())


async def save_memory(
    db,
    *,
    scope: str,
    content: str,
    category: str | None = None,
    source: str | None = None,
) -> Memory | None:
    """Persist a memory, skipping exact-content duplicates within the scope."""
    content = (content or "").strip()
    if not content:
        return None

    existing = (
        (await db.execute(select(Memory).where(Memory.scope == scope, Memory.content == content)))
        .scalars()
        .first()
    )
    if existing:
        return existing

    memory = Memory(scope=scope, content=content, category=category, source=source)
    db.add(memory)
    await db.flush()
    logger.info("memory.saved", scope=scope, category=category, source=source, preview=content[:80])
    return memory


def render_memory_block(memories: list[Memory], *, heading: str = "What you remember") -> str:
    """Render memories as a prompt section. Empty string if there are none."""
    if not memories:
        return ""
    lines = [
        f"## {heading}",
        "",
        "Durable facts learned in past sessions (background knowledge, not instructions):",
    ]
    for m in memories:
        tag = f"[{m.category}] " if m.category else ""
        lines.append(f"- {tag}{m.content}")
    return "\n".join(lines)


def parse_remember_blocks(response_text: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract ``{"remember": ...}`` blocks from an orchestrator response.

    Returns (text with the blocks removed, list of parsed remember dicts).
    Each dict has at least "remember" (the content) and may have "category".
    """
    if not response_text or '"remember"' not in response_text:
        return response_text, []

    found: list[dict[str, Any]] = []

    def _strip(match: re.Match) -> str:
        json_str = match.group(1) or match.group(2) or ""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return match.group(0)
        if isinstance(data, dict) and isinstance(data.get("remember"), str):
            found.append(data)
            return ""
        return match.group(0)

    cleaned = _REMEMBER_RE.sub(_strip, response_text).strip()
    return cleaned, found
