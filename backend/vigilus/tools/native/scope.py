"""Native tool handlers for the Scope (network attack surface) feature.

These are the LLM-facing tools. The actual parsing + persistence lives in
``vigilus.core.scope`` (shared with the auto-ingest hook in the tool registry).

Handlers (signature matches the rest of tools/native/*):
    scope_ingest(arguments, operator=None, **kwargs) -> dict
    scope_record_findings(arguments, operator=None, **kwargs) -> dict
    scope_list_hosts(arguments, operator=None, **kwargs) -> dict

Note: scope_ingest is ALSO fed automatically by the registry after any nmap
MCP tool call (see tools/registry.py), so scans populate Scope without the LLM
needing to remember to ingest. This tool exists for manual / import use.
"""

from __future__ import annotations

from typing import Any

from vigilus.core import scope as core_scope


async def scope_ingest(arguments: dict[str, Any], operator: Any = None, **kwargs) -> dict[str, Any]:
    """Persist scan output into Scope. Accepts nmap text, nmap XML, or parsed hosts.

    Called by the Recon Operator for manual/imported scans. (Automatic scans via
    the nmap MCP tool are ingested without this.)
    """
    raw = arguments.get("xml") or arguments.get("text")
    host_list = arguments.get("hosts")

    if not raw and host_list:
        # Pre-parsed hosts: synthesise a minimal JSON the core parser accepts.
        import json
        raw = json.dumps({"hosts": host_list})

    if not raw:
        return {"error": "scope_ingest needs 'text' (nmap output), 'xml', or 'hosts'."}

    return await core_scope.ingest(
        raw,
        target=arguments.get("target", "unknown"),
        operator_id=getattr(operator, "id", None),
    )


async def scope_record_findings(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """Persist security findings onto host(s). Idempotent via fingerprint."""
    findings_in = arguments.get("findings") or []
    if not findings_in:
        return {"error": "scope_record_findings needs a 'findings' array."}
    return await core_scope.record_findings(findings_in)


async def scope_list_hosts(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """List what Scope currently knows: managed + discovered hosts (deduped)."""
    return await core_scope.list_hosts()
