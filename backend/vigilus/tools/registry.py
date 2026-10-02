"""Tool registry – resolves and executes tools for operators."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select

from vigilus.core.rbac import Permission, PolicyEngine
from vigilus.core.tasks import TaskCancelled, await_cancelled
from vigilus.db.base import get_session_factory
from vigilus.db.models import Action, ActionOutcome, Operator, Tool, ToolImplementationType

logger = structlog.get_logger(__name__)

# How long a cached approved-grant lookup may be reused. The cache is cleared
# on every ``jit.resolved`` event, so this TTL only bounds staleness if an
# invalidation event is somehow missed (e.g. a different process wrote the DB).
_GRANT_CACHE_TTL_SECONDS = 1.0


class _GrantCache:
    """Process-wide cache of approved-grant lookups, cleared on ``jit.resolved``.

    The event bus is a singleton and ToolRegistry instances are per-run, so
    the subscription lives on a single shared cache object instead of each
    registry subscribing (and never unsubscribing) itself.
    """

    def __init__(self) -> None:
        # (operator_id, resource, permission) -> (cached_at, token-or-None)
        self._entries: dict[tuple[str, str, Permission], tuple[float, Any]] = {}
        from vigilus.core.events import get_event_bus

        get_event_bus().subscribe("jit.resolved", self._on_jit_resolved)

    async def _on_jit_resolved(self, _payload: dict[str, Any]) -> None:
        self._entries.clear()

    def clear(self) -> None:
        self._entries.clear()

    def get(self, key: tuple[str, str, Permission]) -> tuple[bool, Any]:
        """Return ``(hit, token)``; a cached miss is a valid hit with token None."""
        entry = self._entries.get(key)
        if entry is None:
            return False, None
        cached_at, token = entry
        if time.monotonic() - cached_at >= _GRANT_CACHE_TTL_SECONDS:
            del self._entries[key]
            return False, None
        # A cached token can expire mid-run; treat that as a miss.
        if token is not None and not token.is_valid:
            del self._entries[key]
            return False, None
        return True, token

    def put(self, key: tuple[str, str, Permission], token) -> None:
        self._entries[key] = (time.monotonic(), token)


_grant_cache: _GrantCache | None = None


def _get_grant_cache() -> _GrantCache:
    global _grant_cache
    if _grant_cache is None:
        _grant_cache = _GrantCache()
    return _grant_cache


@dataclass
class ToolResult:
    """Result of executing a tool."""

    success: bool = True
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Central registry for listing and executing tools."""

    def __init__(self):
        self.policy_engine = PolicyEngine()
        from vigilus.core.rbac import WardenService

        self.warden = WardenService()
        from vigilus.core.events import get_event_bus

        self.event_bus = get_event_bus()
        self.session_factory = get_session_factory()
        # Per-run resolved-tool cache; grants use the shared _GrantCache.
        self._tool_cache: dict[str, Tool] = {}
        self._grant_cache = _get_grant_cache()

    async def _resolve_tool(self, db, tool_id_or_name: str) -> Tool | None:
        """Resolve a tool by id or name, caching the row for the rest of the run.

        The cached instance is detached from its session; only its already
        loaded column attributes are used downstream, so no lazy load can be
        triggered on a dead session.
        """
        cached = self._tool_cache.get(tool_id_or_name)
        if cached is not None:
            return cached

        query = select(Tool).where(
            (Tool.id == tool_id_or_name) | (Tool.name == tool_id_or_name)
        )
        result = await db.execute(query)
        tool = result.scalar_one_or_none()
        if tool is not None:
            db.expunge(tool)
            self._tool_cache[tool_id_or_name] = tool
            self._tool_cache.setdefault(tool.name, tool)
            self._tool_cache.setdefault(tool.id, tool)
        return tool

    async def execute(
        self,
        tool_id_or_name: str,
        arguments: dict[str, Any],
        *,
        operator: Operator,
        session_id: str | None = None,
        jit_token: str | None = None,
        jit_wait_seconds: int | None = None,
        unattended: bool = False,
        cancel_event: asyncio.Event | None = None,
        park: Any | None = None,
        tool_use_id: str | None = None,
        continuation: dict | None = None,
        operator_messages: list | None = None,
    ) -> ToolResult:
        """Execute a tool invocation.

        When a call is denied under strict trust, execution pauses for up to
        ``jit_wait_seconds`` (default from settings) while the user approves
        or denies the JIT request, then proceeds or aborts accordingly.

        ``unattended`` (scheduled-task runs) selects the longer JIT wait window
        so a human notified by the global banner has time to approve. It never
        widens or auto-grants the permission — only the wait timeout differs.
        """
        from vigilus.config import get_settings

        if cancel_event is not None and cancel_event.is_set():
            return ToolResult(success=False, error="Task cancelled before tool execution.")

        if jit_wait_seconds is None:
            settings = get_settings()
            jit_wait_seconds = (
                settings.jit_wait_seconds_unattended if unattended else settings.jit_wait_seconds
            )
        start_time = time.time()

        # LLMs pass JIT tokens inside the tool arguments (per the denial
        # message instructions). Extract it so it's honored by the policy
        # check and never leaks into the actual tool handler or audit args.
        token_in_args = arguments.pop("jit_token", None)
        if token_in_args and not jit_token:
            jit_token = token_in_args

        async with self.session_factory() as db:
            # 1. Resolve tool (cached per run — see _resolve_tool)
            tool = await self._resolve_tool(db, tool_id_or_name)

            if not tool:
                return ToolResult(success=False, error=f"Tool not found: {tool_id_or_name}")

            # 2. Extract the resource this call targets, so JIT grants can be
            #    scoped to it (e.g. one server) instead of a blanket "*".
            resource = self._extract_resource(arguments)

            # 3. Policy Check
            req_perm = Permission(tool.required_permission.value)

            # Resolve token if provided. is_token_active adds the DB
            # revocation check (#34): a stateless HMAC token alone cannot
            # see a revoke, the backing JitRequest status is the truth.
            token_obj = None
            if jit_token:
                token_obj = await self.warden.is_token_active(db, jit_token)

            is_allowed = await self.policy_engine.check_permission(
                operator=operator,
                tool_name=tool.name,
                required_permission=req_perm,
                resource_path=resource,
                jit_token=token_obj,
            )

            if not is_allowed and not token_obj:
                # The user may have approved a JIT request for this exact
                # access since the last attempt (e.g. inline in the chat).
                # Look for a still-valid approved grant and retry with it.
                approved = await self._find_approved_token(db, operator, resource, req_perm)
                if approved:
                    is_allowed = await self.policy_engine.check_permission(
                        operator=operator,
                        tool_name=tool.name,
                        required_permission=req_perm,
                        resource_path=resource,
                        jit_token=approved,
                    )
                    if is_allowed:
                        logger.info(
                            "rbac.approved_jit_reused",
                            operator=operator.name,
                            tool=tool.name,
                            resource=resource,
                        )

            if not is_allowed:
                # Write tool_denied Action
                action = Action(
                    event="tool_denied",
                    actor=operator.name,
                    operator_id=operator.id,
                    tool_id=tool.id,
                    tool_name=tool.name,
                    args=arguments,
                    outcome=ActionOutcome.denied,
                    error="Permission denied by PolicyEngine.",
                    session_id=session_id,
                )
                db.add(action)

                # Request JIT
                warden = self.warden
                task_description = (
                    f"Run {tool.name} with args {json.dumps(arguments, default=str)[:500]}"
                )
                req, token = await warden.request_jit(
                    db, operator, resource, req_perm, task_description
                )

                await db.commit()

                if token:
                    # Lenient trust: auto-approved — proceed with the grant
                    # immediately instead of bouncing back to the LLM.
                    token_obj = warden.validate_token(token)
                    self._grant_cache.clear()
                else:
                    # Strict trust: park the turn and release this coroutine
                    # when a park context is attached. Otherwise pause until
                    # the user approves or denies, or the wait times out.
                    from vigilus.config import get_settings

                    if park is not None and get_settings().jit_park_resume:
                        from vigilus.core.turn_park import park_for_approval

                        await park_for_approval(
                            db,
                            park,
                            req,
                            tool,
                            resource,
                            req_perm,
                            jit_wait_seconds,
                            arguments=arguments,
                            tool_use_id=tool_use_id,
                            continuation=continuation,
                            operator_messages=operator_messages,
                        )
                    if cancel_event is None:
                        outcome = await self._wait_for_jit_resolution(req.id, jit_wait_seconds)
                    else:
                        outcome = await self._wait_for_jit_resolution(
                            req.id, jit_wait_seconds, cancel_event=cancel_event
                        )
                    # Grant state changed while waiting — never let a cached
                    # lookup mask the resolution.
                    self._grant_cache.clear()
                    if outcome == "cancelled":
                        # A task that was stopped must never leave a live grant
                        # request behind. Resolve it fail-closed and notify all
                        # JIT surfaces through the normal event path.
                        await warden.deny_request(db, req.id, approver="task_cancelled")
                        return ToolResult(
                            success=False, error="Task cancelled while awaiting approval."
                        )
                    if outcome == "denied":
                        return ToolResult(
                            success=False,
                            error=(
                                "The user DENIED this action. Do not retry it. "
                                "Report what you were unable to do and continue with "
                                "anything that does not require this permission."
                            ),
                        )
                    if outcome is None:
                        return ToolResult(
                            success=False,
                            error=(
                                f"Approval request ({req.id}) is still pending — the user "
                                f"did not respond within the wait window. Tell the user "
                                f"the action is awaiting their approval on the JIT page; "
                                f"once approved, this exact tool call can be retried."
                            ),
                        )
                    token_obj = warden.validate_token(outcome)

                if token_obj:
                    is_allowed = await self.policy_engine.check_permission(
                        operator=operator,
                        tool_name=tool.name,
                        required_permission=req_perm,
                        resource_path=resource,
                        jit_token=token_obj,
                    )
                if not is_allowed:
                    return ToolResult(
                        success=False,
                        error="Permission denied — the JIT grant did not cover this action.",
                    )
                logger.info(
                    "rbac.jit_grant_applied",
                    operator=operator.name,
                    tool=tool.name,
                    resource=resource,
                )

            if cancel_event is not None and cancel_event.is_set():
                return ToolResult(success=False, error="Task cancelled before tool execution.")

            # 4. Write tool_call_start Action
            action = Action(
                event="tool_call_start",
                actor=operator.name,
                operator_id=operator.id,
                tool_id=tool.id,
                tool_name=tool.name,
                args=arguments,
                outcome=ActionOutcome.pending,
                session_id=session_id,
            )
            db.add(action)
            await db.commit()

            # 5. Dispatch
            result_obj = None
            try:
                if tool.implementation_type == ToolImplementationType.native:
                    handler_path = tool.native_handler
                    if not handler_path:
                        raise ValueError("Native handler path not configured.")

                    handler_func = self._get_native_handler(handler_path)

                    # Build call kwargs based on what the handler accepts
                    call_kwargs: dict[str, Any] = {"arguments": arguments, "operator": operator}

                    # Pass the DB session if the handler accepts it
                    try:
                        sig = inspect.signature(handler_func)
                        if "db" in sig.parameters:
                            call_kwargs["db"] = db
                    except (ValueError, TypeError):
                        pass

                    res = await await_cancelled(handler_func(**call_kwargs), cancel_event)
                    # Handlers should return a dict; serialize to string for LLM
                    if isinstance(res, dict):
                        result_obj = ToolResult(
                            success=True, output=json.dumps(res, default=str, indent=2)
                        )
                    else:
                        result_obj = ToolResult(success=True, output=str(res))

                elif tool.implementation_type == ToolImplementationType.http:
                    result_obj = await await_cancelled(
                        self._execute_http(tool, arguments), cancel_event
                    )

                elif tool.implementation_type == ToolImplementationType.mcp:
                    result_obj = await await_cancelled(
                        self._execute_mcp(tool, arguments), cancel_event
                    )

                else:
                    raise ValueError(f"Unknown implementation type: {tool.implementation_type}")

            except TaskCancelled:
                # The stopped handler may have left the shared session mid-query;
                # discard its uncommitted work before recording the outcome.
                logger.info("tool_execution_cancelled", tool=tool.name, session_id=session_id)
                await db.rollback()
                result_obj = ToolResult(
                    success=False, error="Task cancelled while the tool was running."
                )
            except Exception as e:
                logger.exception("tool_execution_failed", tool=tool.name, error=str(e))
                result_obj = ToolResult(success=False, error=str(e))

            # 6. Write tool_call_end / tool_error
            duration_ms = (time.time() - start_time) * 1000
            end_action = Action(
                event="tool_error" if not result_obj.success else "tool_call_end",
                actor=operator.name,
                operator_id=operator.id,
                tool_id=tool.id,
                tool_name=tool.name,
                args=arguments,
                outcome=ActionOutcome.error if not result_obj.success else ActionOutcome.success,
                error=result_obj.error,
                output=result_obj.output or None,
                duration_ms=duration_ms,
                session_id=session_id,
            )
            db.add(end_action)
            await db.commit()

            # Emit events
            await self.event_bus.publish(
                "action.completed",
                {
                    "action_id": end_action.id,
                    "event": end_action.event,
                    "outcome": end_action.outcome.value,
                    "tool": tool.name,
                },
            )

            # Auto-ingest nmap MCP scan results into Scope so the network map
            # populates without depending on the LLM calling scope_ingest.
            # Best-effort: never breaks the tool call.
            if (
                result_obj.success
                and tool.implementation_type == ToolImplementationType.mcp
                and "nmap" in tool.name.lower()
                and result_obj.output
            ):
                await self._maybe_ingest_scan(tool.name, result_obj.output, arguments, operator)

            return result_obj

    async def _wait_for_jit_resolution(
        self,
        request_id: str,
        wait_seconds: int,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> str | None:
        """Block until a JIT request resolves, is cancelled, or times out.

        Returns the token string when approved, ``"denied"`` when
        denied/revoked/expired, ``"cancelled"`` when its turn is stopped, or
        None on timeout. Each poll uses a fresh short-lived session so the
        approval write (from the API) is never blocked by a long-running read
        transaction.
        """
        from vigilus.db.models import JitRequest, JitStatus

        deadline = time.monotonic() + max(wait_seconds, 0)
        poll_interval = 1.0

        while True:
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled"

            async with self.session_factory() as poll_db:
                req = await poll_db.get(JitRequest, request_id)
                if not req:
                    return "denied"
                if req.status == JitStatus.approved and req.token_id:
                    return req.token_id
                if req.status in (JitStatus.denied, JitStatus.revoked, JitStatus.expired):
                    return "denied"

            if time.monotonic() >= deadline:
                return None

            delay = min(poll_interval, max(deadline - time.monotonic(), 0.05))
            if cancel_event is None:
                await asyncio.sleep(delay)
                continue
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                return "cancelled"
            except TimeoutError:
                pass

    @staticmethod
    def _extract_resource(arguments: dict[str, Any]) -> str:
        """Derive the resource a tool call targets, for JIT scoping.

        Filesystem tools carry a ``path``; ssh/docker tools carry a
        ``server_id`` (scoped as ``server:<id>`` so a grant can cover just that
        host). Multi-server and everything else fall back to the wildcard "*".
        """
        if arguments.get("resource"):
            return str(arguments["resource"])
        if arguments.get("path"):
            return str(arguments["path"])
        server_id = arguments.get("server_id")
        if server_id:
            return f"server:{server_id}"
        return "*"

    async def _find_approved_token(self, db, operator: Operator, resource: str, req_perm):
        """Find a still-valid token from an approved JIT request covering this call.

        Lets an operator retry a denied tool call after the user approves the
        JIT request (e.g. inline in the chat), without the LLM needing to know
        the token value. Single-use ("once") grants are deliberately excluded so
        they authorize only the command that triggered them — the next call
        re-prompts. A grant is used only when its resource covers this call, so
        a newer grant for a different host does not hide an older matching one.

        Results are cached process-wide — positive lookups only — and cleared
        on every ``jit.resolved`` event. A "no grant found" result is never
        cached so an approval written by any surface is visible on the very
        next call (the issue's "grant changes still take effect immediately").
        """
        from vigilus.core.rbac import _resource_covers
        from vigilus.db.models import JitRequest, JitStatus

        cache_key = (operator.id, resource, req_perm)
        hit, token = self._grant_cache.get(cache_key)
        if hit:
            return token

        result = await db.execute(
            select(JitRequest)
            .where(
                JitRequest.operator_id == operator.id,
                JitRequest.status == JitStatus.approved,
                JitRequest.token_id.isnot(None),
                JitRequest.scope_mode != "once",
            )
            .order_by(JitRequest.resolved_at.desc())
            .limit(10)
        )
        token = None
        for req in result.scalars().all():
            candidate = self.warden.validate_token(req.token_id)
            if (
                candidate
                and candidate.permission >= req_perm
                and _resource_covers(candidate.resource, resource)
            ):
                token = candidate
                break
        if token is not None:
            self._grant_cache.put(cache_key, token)
        return token

    def _get_native_handler(self, handler_path: str) -> Callable:
        """Resolve a native handler callable.

        Looks up by function name in the NATIVE_HANDLERS registry first,
        then falls back to dynamic import.
        """
        from vigilus.tools.native import NATIVE_HANDLERS

        # Try direct function name lookup first
        if handler_path in NATIVE_HANDLERS:
            return NATIVE_HANDLERS[handler_path]

        # Parse "module:func" or "module.func" format for dynamic import
        if ":" in handler_path:
            module_path, func_name = handler_path.rsplit(":", 1)
        elif "." in handler_path:
            module_path, func_name = handler_path.rsplit(".", 1)
        else:
            raise ValueError(f"Unknown native handler: {handler_path}")

        # If module_path is a full path like "vigilus.tools.native.ssh", use as-is
        # Otherwise, assume it's relative to vigilus.tools.native
        if module_path.startswith("vigilus."):
            import_module = module_path
        else:
            import_module = f"vigilus.tools.native.{module_path}"

        import importlib

        module = importlib.import_module(import_module)
        func = getattr(module, func_name)
        return func

    async def _execute_http(self, tool: Tool, arguments: dict[str, Any]) -> ToolResult:
        """Execute an HTTP-based tool."""
        import httpx

        config = tool.http_config or {}
        url = config.get("url", "")
        method = config.get("method", "GET")
        headers = config.get("headers", {})

        if not url:
            return ToolResult(success=False, error="HTTP tool has no URL configured")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method, url, json=arguments, headers=headers)
                return ToolResult(success=resp.is_success, output=resp.text)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _execute_mcp(self, tool: Tool, arguments: dict[str, Any]) -> ToolResult:
        """Execute an MCP-based tool."""
        from vigilus.mcp_host.manager import McpManager

        manager = McpManager()
        try:
            mcp_res = await manager.call_tool(tool.mcp_server_id, tool.mcp_tool_name, arguments)

            out_text = ""
            is_error = getattr(mcp_res, "isError", False)
            for content in getattr(mcp_res, "content", []):
                if hasattr(content, "type") and content.type == "text":
                    out_text += content.text + "\n"
                elif isinstance(content, dict) and content.get("type") == "text":
                    out_text += content.get("text", "") + "\n"

            return ToolResult(
                success=not is_error,
                output=out_text.strip() if not is_error else "",
                error=out_text.strip() if is_error else None,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _maybe_ingest_scan(
        self, tool_name: str, output: str, arguments: dict[str, Any], operator: Operator
    ) -> None:
        """Best-effort: parse an nmap scan result into Scope. Never raises."""
        try:
            from vigilus.core import scope as core_scope

            target = arguments.get("target") or arguments.get("host") or "unknown"
            summary = await core_scope.ingest(
                output, target=target, operator_id=getattr(operator, "id", None)
            )
            logger.info(
                "scope.auto_ingest",
                source_tool=tool_name,
                scan_target=target,
                **{
                    k: v
                    for k, v in summary.items()
                    if isinstance(v, (str, int, float)) and k != "target"
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("scope.auto_ingest_failed", source_tool=tool_name)
