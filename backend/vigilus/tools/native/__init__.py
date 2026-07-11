"""Native tool handlers – registry of all built-in handler functions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from vigilus.tools.native.docker import (
    docker_compose_pull,
    docker_compose_up,
    docker_deploy_stack,
    docker_inspect,
    docker_list,
    docker_logs,
    docker_restart,
)
from vigilus.tools.native.host import fs_list, fs_read, fs_write, shell_exec
from vigilus.tools.native.memory import memory_forget, memory_save
from vigilus.tools.native.scope import (
    scope_ingest,
    scope_list_hosts,
    scope_record_findings,
)
from vigilus.tools.native.search import web_fetch, web_search
from vigilus.tools.native.ssh import ssh_exec, ssh_exec_all
from vigilus.tools.native.wazuh import (
    wazuh_get_agents,
    wazuh_get_alerts,
    wazuh_get_fim,
    wazuh_get_vulnerabilities,
    wazuh_search_logs,
)

NATIVE_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "shell_exec": shell_exec,
    "fs_read": fs_read,
    "fs_write": fs_write,
    "fs_list": fs_list,
    "ssh_exec": ssh_exec,
    "ssh_exec_all": ssh_exec_all,
    "docker_list": docker_list,
    "docker_logs": docker_logs,
    "docker_inspect": docker_inspect,
    "docker_restart": docker_restart,
    "docker_compose_up": docker_compose_up,
    "docker_compose_pull": docker_compose_pull,
    "docker_deploy_stack": docker_deploy_stack,
    "wazuh_get_alerts": wazuh_get_alerts,
    "wazuh_get_vulnerabilities": wazuh_get_vulnerabilities,
    "wazuh_get_agents": wazuh_get_agents,
    "wazuh_get_fim": wazuh_get_fim,
    "wazuh_search_logs": wazuh_search_logs,
    "memory_save": memory_save,
    "memory_forget": memory_forget,
    "scope_ingest": scope_ingest,
    "scope_record_findings": scope_record_findings,
    "scope_list_hosts": scope_list_hosts,
    "web_search": web_search,
    "web_fetch": web_fetch,
}
