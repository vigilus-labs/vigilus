"""Agent-to-agent delegation engine.

Called directly by the chat orchestrator loop — NOT exposed as a tool handler.
The Vigilus orchestrator outputs structured delegation JSON in its text
response, which the chat loop parses and dispatches here.
"""

from __future__ import annotations

import json
import re
import structlog
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from vigilus.providers.base import LLMMessage
from vigilus.db.models import Operator, OperatorTool
from vigilus.core.operator_runtime import OperatorRuntime

logger = structlog.get_logger(__name__)

# Pattern to detect delegation JSON in orchestrator responses.
# Group 1: fenced ```json ... ``` block; Group 2: inline {...} block.
_DELEGATE_RE = re.compile(
    r'```json\s*(\{[^`]*"delegate"[^`]*\})\s*```'
    r'|(\{"delegate"[^}]*\})',
    re.DOTALL,
)


def parse_delegation(response_text: str) -> dict[str, Any] | None:
    """Parse a delegation block from the orchestrator's text response.

    Looks for:
      ```json
      {"delegate": "Operator Name", "task": "...", "context": "..."}
      ```

    Or an inline JSON object with a "delegate" key.
    Returns the parsed dict, or None if no delegation found.
    """
    if not response_text or '"delegate"' not in response_text:
        return None

    for m in _DELEGATE_RE.finditer(response_text):
        # Group 1 = fenced, Group 2 = inline; pick whichever matched
        json_str = m.group(1) or m.group(2) or ""
        if not json_str:
            continue
        try:
            data = json.loads(json_str)
            if "delegate" in data and "task" in data:
                return data
        except json.JSONDecodeError:
            continue

    return None


def strip_delegation(response_text: str) -> str:
    """Remove delegation JSON blocks from *response_text*, leaving the prose.

    The orchestrator leads a delegation with a short plain-text plan and then the
    control JSON. The JSON is machine-only; this strips it (and any leftover empty
    ```json fences) so the user sees just the plan.
    """
    if not response_text or '"delegate"' not in response_text:
        return response_text
    cleaned = _DELEGATE_RE.sub("", response_text)
    # Drop now-empty fenced code blocks left behind by inline-match removal.
    cleaned = re.sub(r"```json\s*```", "", cleaned)
    return cleaned.strip()


async def _build_server_inventory(db) -> str:
    """Render the server inventory for an operator's task prompt."""
    from vigilus.db.models import Server

    servers = (await db.execute(select(Server))).scalars().all()
    if not servers:
        return ""

    lines = [
        "\nSERVER INVENTORY — pass the server *name* (or id) as `server_id` to "
        "ssh_exec / docker tools. Stored credentials are attached automatically; "
        "NEVER pass usernames, passwords, or user@host strings, and never run "
        "raw `ssh` through shell_exec:"
    ]
    for s in servers:
        cred = "credential attached" if s.credential_id else "⚠ NO credential attached"
        os_label = " ".join(p for p in (s.os, s.os_version) if p)
        os_part = f", {os_label}" if os_label else ""
        lines.append(f"- {s.name} ({s.hostname}:{s.port}{os_part}) — {cred}")
    return "\n".join(lines)


async def execute_delegation(
    delegate_request: dict[str, Any],
    *,
    db,
    session_id: str | None = None,
    bridge: Any | None = None,  # StreamBridge from api.sse
    cancel_event: Any | None = None,  # asyncio.Event — stop when set
    unattended: bool = False,  # scheduled run — use longer JIT wait
) -> dict[str, Any]:
    """Execute a delegation to a specialist operator.

    Args:
        delegate_request: Parsed delegation dict with keys:
            - operator: target operator name
            - task: what to do
            - context: (optional) additional info
        db: async SQLAlchemy session
        session_id: chat session ID for audit logs

    Returns a result dict with status, response, and tool history.
    """
    target_name = delegate_request.get("delegate") or delegate_request.get("operator")
    task = delegate_request.get("task", "")
    context = delegate_request.get("context", "")

    if not target_name:
        return {"status": "error", "error": "No operator name specified in delegation."}
    if not task:
        return {"status": "error", "error": "No task specified for delegation."}

    logger.info("delegation.start", target=target_name, task=task[:120])

    # Lookup operator
    result = await db.execute(
        select(Operator)
        .options(
            selectinload(Operator.operator_tools).selectinload(OperatorTool.tool),
            selectinload(Operator.provider),
        )
        .where(Operator.name == target_name, Operator.enabled == True)  # noqa: E712
    )
    target_op = result.scalar_one_or_none()

    if not target_op:
        available = (await db.execute(
            select(Operator.name).where(
                Operator.enabled == True, Operator.delegatable == True  # noqa: E712
            )
        )).scalars().all()
        return {
            "status": "error",
            "error": (
                f"Operator '{target_name}' not found or disabled. "
                f"Available: {', '.join(available) or 'none configured'}"
            ),
        }

    if not target_op.delegatable:
        # The hidden Vigilus research principal (and any other non-delegatable
        # row) must never receive delegated work.
        return {
            "status": "error",
            "operator": target_name,
            "error": f"Operator '{target_name}' is not delegatable.",
        }

    fallback_provider = None
    if not target_op.provider:
        from vigilus.db.models import Provider

        fallback_provider = (await db.execute(
            select(Provider).where(
                Provider.is_default == True, Provider.enabled == True  # noqa: E712
            )
        )).scalar_one_or_none()
        if not fallback_provider:
            fallback_provider = (await db.execute(
                select(Provider).where(Provider.enabled == True)  # noqa: E712
            )).scalars().first()
        if not fallback_provider:
            return {
                "status": "error",
                "operator": target_name,
                "error": (
                    f"Operator '{target_name}' has no LLM provider, and no default "
                    "provider is configured. Add a provider in Settings."
                ),
            }

    # Build the prompt for the sub-agent
    parts = [f"TASK: {task}"]
    if context:
        parts.append(f"\nCONTEXT:\n{context}")

    # Server inventory: operators otherwise have no way to know what servers
    # exist or how to reference them in ssh/docker tools.
    inventory = await _build_server_inventory(db)
    if inventory:
        parts.append(inventory)

    parts.append(
        "\nThis task was delegated to you by the Vigilus orchestrator. "
        "Use your available tools to complete it and provide a detailed report."
    )
    prompt = "\n".join(parts)

    try:
        runtime = OperatorRuntime(target_op, fallback_provider=fallback_provider)
        messages = [LLMMessage(role="user", content=prompt)]
        final_msgs, tool_history = await runtime.run(
            messages, session_id=session_id, max_iterations=10,
            bridge=bridge, cancel_event=cancel_event, unattended=unattended,
        )

        # Extract the final response text
        final_text = ""
        for msg in final_msgs:
            if msg.role == "assistant":
                text = str(msg.content) if msg.content else ""
                if text:
                    final_text = text

        logger.info(
            "delegation.complete",
            target=target_name,
            tool_calls=len(tool_history),
            response_preview=final_text[:120],
        )

        return {
            "status": "success",
            "operator": target_name,
            "response": final_text,
            "tool_calls": tool_history,
        }

    except Exception as e:
        logger.exception("delegation.failed", target=target_name, error=str(e))
        return {
            "status": "error",
            "operator": target_name,
            "error": f"Operator '{target_name}' failed: {e}",
        }
