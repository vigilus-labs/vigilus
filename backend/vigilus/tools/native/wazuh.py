"""Wazuh native tool handlers – interact with Wazuh Manager REST API.

Configuration via environment variables:
    WAZUH_API_URL      – e.g. https://wazuh-manager:55000
    WAZUH_API_USER     – API username
    WAZUH_API_PASS     – API password
    WAZUH_VERIFY_SSL   – "true" or "false" (default "true")
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


def _get_wazuh_config() -> dict[str, str]:
    """Read Wazuh connection details from environment variables."""
    return {
        "api_url": os.environ.get("WAZUH_API_URL", "https://localhost:55000"),
        "api_user": os.environ.get("WAZUH_API_USER", "wazuh"),
        "api_pass": os.environ.get("WAZUH_API_PASS", "wazuh"),
        "verify_ssl": os.environ.get("WAZUH_VERIFY_SSL", "false").lower() == "true",
    }


async def _wazuh_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
    body: dict | None = None,
) -> dict[str, Any]:
    """Make an authenticated request to the Wazuh Manager API."""
    config = _get_wazuh_config()
    url = f"{config['api_url'].rstrip('/')}{endpoint}"

    try:
        async with httpx.AsyncClient(verify=config["verify_ssl"], timeout=30.0) as client:
            # Get auth token
            auth_resp = await client.post(
                f"{config['api_url'].rstrip('/')}/security/user/authenticate",
                json={"username": config["api_user"], "password": config["api_pass"]},
            )
            if auth_resp.status_code != 200:
                return {"error": f"Wazuh auth failed: {auth_resp.status_code} {auth_resp.text}"}

            token_data = auth_resp.json()
            token = token_data.get("data", {}).get("token", "")
            if not token:
                return {"error": "Wazuh auth returned no token"}

            headers = {"Authorization": f"Bearer {token}"}

            # Make the actual request
            resp = await client.request(method, url, params=params, json=body, headers=headers)

            if resp.status_code != 200:
                return {"error": f"Wazuh API error: {resp.status_code} {resp.text}"}

            return resp.json()

    except httpx.ConnectError:
        return {"error": f"Cannot connect to Wazuh API at {config['api_url']}"}
    except httpx.TimeoutException:
        return {"error": "Wazuh API request timed out"}
    except Exception as e:
        return {"error": str(e)}


async def wazuh_get_alerts(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """Retrieve recent security alerts from Wazuh."""
    agent_id = arguments.get("agent_id")
    level_min = arguments.get("level_min", 7)
    limit = arguments.get("limit", 25)
    since = arguments.get("since")

    params: dict[str, Any] = {"limit": limit, "sort": "-timestamp"}

    query_parts = []
    if agent_id:
        query_parts.append(f"agent.id={agent_id}")
    if level_min:
        query_parts.append(f"rule.level>={level_min}")
    if since:
        query_parts.append(f"timestamp>{since}")
    if query_parts:
        params["q"] = ";".join(query_parts)

    result = await _wazuh_request("GET", "/manager/api/alerts", params=params)

    if result.get("error"):
        # Fallback: try the standard Wazuh API endpoint
        result = await _wazuh_request("GET", "/alerts", params=params)

    alerts = []
    for item in result.get("data", {}).get("affected_items", []):
        alerts.append(
            {
                "id": item.get("id", item.get("_id", "")),
                "rule": item.get("rule", {}).get("description", "Unknown"),
                "level": item.get("rule", {}).get("level", 0),
                "agent_id": item.get("agent", {}).get("id", ""),
                "timestamp": item.get("timestamp", ""),
            }
        )

    return {
        "alerts": alerts,
        "total": result.get("data", {}).get("total_affected_items", len(alerts)),
    }


async def wazuh_get_vulnerabilities(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """Retrieve vulnerability assessment results from Wazuh."""
    agent_id = arguments.get("agent_id")
    severity = arguments.get("severity")
    limit = arguments.get("limit", 25)

    params: dict[str, Any] = {"limit": limit, "sort": "-severity"}

    query_parts = []
    if agent_id:
        query_parts.append(f"agent.id={agent_id}")
    if severity:
        query_parts.append(f"severity={severity}")
    if query_parts:
        params["q"] = ";".join(query_parts)

    result = await _wazuh_request("GET", "/vulnerability", params=params)

    vulnerabilities = []
    for item in result.get("data", {}).get("affected_items", []):
        vulnerabilities.append(
            {
                "cve": item.get("cve", "Unknown"),
                "severity": item.get("severity", "Unknown"),
                "title": item.get("title", ""),
                "agent_id": item.get("agent_id", item.get("agent", {}).get("id", "")),
                "status": item.get("status", ""),
            }
        )

    return {
        "vulnerabilities": vulnerabilities,
        "total": result.get("data", {}).get("total_affected_items", len(vulnerabilities)),
    }


async def wazuh_get_agents(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """List Wazuh agents and their status."""
    status_filter = arguments.get("status")
    limit = arguments.get("limit", 50)

    params: dict[str, Any] = {"limit": limit}
    if status_filter:
        params["status"] = status_filter

    result = await _wazuh_request("GET", "/agents", params=params)

    agents = []
    for item in result.get("data", {}).get("affected_items", []):
        agents.append(
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "ip": item.get("ip", ""),
                "status": item.get("status", ""),
                "version": item.get("version", ""),
                "os": item.get("os", {}).get("name", ""),
                "last_keepalive": item.get("lastKeepAlive", ""),
            }
        )

    return {
        "agents": agents,
        "total": result.get("data", {}).get("total_affected_items", len(agents)),
    }


async def wazuh_get_fim(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """Retrieve File Integrity Monitoring events from Wazuh."""
    agent_id = arguments.get("agent_id")
    file_path = arguments.get("file_path")
    limit = arguments.get("limit", 25)

    params: dict[str, Any] = {"limit": limit, "sort": "-date"}

    query_parts = []
    if agent_id:
        query_parts.append(f"agent.id={agent_id}")
    if file_path:
        query_parts.append(f"file~{file_path}")
    if query_parts:
        params["q"] = ";".join(query_parts)

    # FIM events come through syscheck endpoint
    result = await _wazuh_request("GET", "/syscheck", params=params)

    fim_events = []
    for item in result.get("data", {}).get("affected_items", []):
        fim_events.append(
            {
                "file": item.get("file", ""),
                "date": item.get("date", ""),
                "size_after": item.get("size_after", 0),
                "perm_after": item.get("perm_after", ""),
                "md5_after": item.get("md5_after", ""),
                "sha1_after": item.get("sha1_after", ""),
            }
        )

    return {
        "fim": fim_events,
        "total": result.get("data", {}).get("total_affected_items", len(fim_events)),
    }


async def wazuh_search_logs(
    arguments: dict[str, Any], operator: Any = None, **kwargs
) -> dict[str, Any]:
    """Search Wazuh log entries with full-text query."""
    query = arguments.get("query")
    agent_id = arguments.get("agent_id")
    limit = arguments.get("limit", 25)

    if not query:
        return {"error": "query is required"}

    params: dict[str, Any] = {"limit": limit, "sort": "-timestamp"}

    query_parts = [f"full_log~{query}"]
    if agent_id:
        query_parts.append(f"agent.id={agent_id}")
    params["q"] = ";".join(query_parts)

    result = await _wazuh_request("GET", "/manager/logs", params=params)

    logs = []
    for item in result.get("data", {}).get("affected_items", []):
        logs.append(
            {
                "timestamp": item.get("timestamp", ""),
                "tag": item.get("tag", ""),
                "level": item.get("level", ""),
                "description": item.get("description", ""),
            }
        )

    return {
        "logs": logs,
        "total": result.get("data", {}).get("total_affected_items", len(logs)),
    }
