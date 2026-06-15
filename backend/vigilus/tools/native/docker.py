"""Docker native tool handlers – remote Docker operations via SSH."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from vigilus.tools.native.ssh import ssh_exec

logger = structlog.get_logger(__name__)


async def docker_list(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """List Docker containers on a remote server."""
    server_id = arguments.get("server_id")
    show_all = arguments.get("all", False)

    cmd = "docker ps --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}'"
    if show_all:
        cmd += " -a"

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 15},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("error", result.get("stderr", "Unknown error")), "server_id": server_id}

    containers = []
    for line in result.get("stdout", "").strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            containers.append({
                "id": parts[0].strip(),
                "name": parts[1].strip(),
                "status": parts[2].strip(),
                "image": parts[3].strip(),
                "ports": parts[4].strip() if len(parts) > 4 else "",
            })

    return {"containers": containers, "count": len(containers), "server_id": server_id}


async def docker_logs(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Fetch logs from a Docker container."""
    server_id = arguments.get("server_id")
    container = arguments.get("container")
    tail = arguments.get("tail", 100)
    since = arguments.get("since")

    if not container:
        return {"error": "container is required"}

    cmd = f"docker logs --tail {tail} {container}"
    if since:
        cmd += f" --since {since}"

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 30},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Failed to fetch logs")}

    return {
        "container": container,
        "server_id": server_id,
        "logs": result.get("stdout", "") + result.get("stderr", ""),
    }


async def docker_inspect(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Inspect a Docker container and return its configuration and state."""
    server_id = arguments.get("server_id")
    container = arguments.get("container")

    if not container:
        return {"error": "container is required"}

    cmd = f"docker inspect {container}"

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 15},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Failed to inspect container")}

    import json
    try:
        data = json.loads(result.get("stdout", "[]"))
        return {"container": container, "server_id": server_id, "inspect": data}
    except json.JSONDecodeError:
        return {"container": container, "server_id": server_id, "raw": result.get("stdout", "")}


async def docker_restart(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Restart a Docker container on a remote server."""
    server_id = arguments.get("server_id")
    container = arguments.get("container")
    timeout = arguments.get("timeout", 10)

    if not container:
        return {"error": "container is required"}

    cmd = f"docker restart -t {timeout} {container}"

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": timeout + 15},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Failed to restart container")}

    return {
        "status": "success",
        "message": f"Restarted container {container}",
        "server_id": server_id,
    }


async def docker_compose_up(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Run docker compose up for a project on a remote server."""
    server_id = arguments.get("server_id")
    project_dir = arguments.get("project_dir")
    services = arguments.get("services", [])
    detach = arguments.get("detach", True)

    if not project_dir:
        return {"error": "project_dir is required"}

    svc_str = " ".join(services) if services else ""
    cmd = f"cd {project_dir} && docker compose up"
    if detach:
        cmd += " -d"
    if svc_str:
        cmd += f" {svc_str}"

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 120},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Docker compose up failed")}

    return {
        "status": "success",
        "message": f"Docker compose up executed in {project_dir}",
        "output": result.get("stdout", ""),
        "server_id": server_id,
    }


async def docker_compose_pull(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Pull latest images for a docker compose project."""
    server_id = arguments.get("server_id")
    project_dir = arguments.get("project_dir")
    services = arguments.get("services", [])

    if not project_dir:
        return {"error": "project_dir is required"}

    svc_str = " ".join(services) if services else ""
    cmd = f"cd {project_dir} && docker compose pull {svc_str}".strip()

    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 120},
        operator=operator,
        db=db,
    )

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Docker compose pull failed")}

    return {
        "status": "success",
        "message": f"Docker compose pull executed in {project_dir}",
        "output": result.get("stdout", ""),
        "server_id": server_id,
    }


async def docker_deploy_stack(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Deploy a full Docker Compose stack with pull, build, and restart.
    Requires elevated permissions.
    """
    server_id = arguments.get("server_id")
    project_dir = arguments.get("project_dir")
    pull = arguments.get("pull", True)
    build = arguments.get("build", False)
    force_recreate = arguments.get("force_recreate", False)

    if not project_dir:
        return {"error": "project_dir is required"}

    steps = []

    if pull:
        pull_result = await docker_compose_pull(
            {"server_id": server_id, "project_dir": project_dir},
            operator=operator,
            db=db,
        )
        steps.append({"step": "pull", "result": pull_result})
        if pull_result.get("error"):
            return {"error": f"Pull failed: {pull_result['error']}", "steps": steps}

    build_cmd = ""
    if build:
        build_cmd = " --build"

    recreate_cmd = ""
    if force_recreate:
        recreate_cmd = " --force-recreate"

    cmd = f"cd {project_dir} && docker compose up -d{build_cmd}{recreate_cmd}"
    result = await ssh_exec(
        {"server_id": server_id, "command": cmd, "timeout": 180},
        operator=operator,
        db=db,
    )
    steps.append({"step": "deploy", "result": result})

    if result.get("exit_code", 1) != 0:
        return {"error": result.get("stderr", "Deploy failed"), "steps": steps}

    return {
        "status": "success",
        "message": f"Stack deployed in {project_dir}",
        "steps": steps,
        "server_id": server_id,
    }
