"""SSH native tool handlers – remote command execution via paramiko."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import paramiko
import structlog

logger = structlog.get_logger(__name__)


async def _resolve_server(db, server_ref: str) -> dict[str, Any] | None:
    """Look up a server and its credentials from the DB.

    *server_ref* may be the server's ID, its name, or its hostname — LLMs
    usually know servers by name ("arcane"), not by UUID.
    Returns dict with hostname, port, username, secret, passphrase, ssh_auth_method.
    """
    from sqlalchemy import func, select

    from vigilus.core.crypto import decrypt
    from vigilus.db.models import Credential, Server

    server = await db.get(Server, server_ref)
    if not server:
        ref = server_ref.strip()
        server = (await db.execute(
            select(Server).where(
                (func.lower(Server.name) == ref.lower()) | (Server.hostname == ref)
            )
        )).scalars().first()
    if not server:
        return None

    result = {
        "hostname": server.hostname,
        "port": server.port,
        "server_name": server.name,
    }

    if server.credential_id:
        cred = await db.get(Credential, server.credential_id)
        if cred:
            result["username"] = cred.username
            result["ssh_auth_method"] = (
                cred.ssh_auth_method.value if cred.ssh_auth_method else "password"
            )
            try:
                result["secret"] = decrypt(cred.secret) if cred.secret else None
            except Exception:
                result["secret"] = cred.secret
            try:
                result["passphrase"] = (
                    decrypt(cred.passphrase) if cred.passphrase else None
                )
            except Exception:
                result["passphrase"] = cred.passphrase

    return result


def _known_hosts_path() -> str:
    """Vigilus-managed known_hosts file inside the data directory."""
    import os

    from vigilus.config import get_settings

    settings = get_settings()
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, "known_hosts")
    if not os.path.exists(path):
        open(path, "a").close()
    return path


class _TofuHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Trust-on-first-use host key policy.

    Unknown hosts are accepted on first connect and their key persisted to
    the Vigilus known_hosts file. On later connects, paramiko raises
    BadHostKeyException if the key has changed (possible MITM), which
    surfaces as a tool error instead of silently reconnecting.
    """

    def __init__(self, path: str):
        self._path = path

    def missing_host_key(self, client, hostname, key):  # noqa: ANN001
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(self._path)
        logger.info(
            "ssh.host_key_trusted_first_use",
            hostname=hostname,
            key_type=key.get_name(),
            fingerprint=key.get_fingerprint().hex(),
        )


async def ssh_exec(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Execute a command on a remote server via SSH.

    Args (from tool schema):
        server_id: Target server ID (resolved via DB for hostname/credentials).
        command: Shell command to execute.
        timeout: Command timeout in seconds (default 30).
    """
    server_id = arguments.get("server_id")
    command = arguments.get("command")
    timeout = arguments.get("timeout", 30)

    if not command:
        return {"error": "command is required", "exit_code": 1}

    hostname = arguments.get("host")
    port = arguments.get("port", 22)
    username = arguments.get("username")
    secret = arguments.get("password") or arguments.get("secret")
    passphrase = None

    # Resolve server from DB if server_id provided (accepts ID, name, or hostname)
    if server_id and db:
        server_info = await _resolve_server(db, server_id)
        if not server_info:
            from sqlalchemy import select

            from vigilus.db.models import Server

            available = (await db.execute(select(Server))).scalars().all()
            inventory = (
                ", ".join(f"'{s.name}' ({s.hostname})" for s in available)
                or "none — ask the user to add the server on the Servers page"
            )
            return {
                "error": (
                    f"Server not found: '{server_id}'. Pass the server's name, "
                    f"hostname, or ID from the inventory. Available servers: {inventory}. "
                    f"Credentials are attached automatically — do not pass usernames "
                    f"or user@host strings."
                ),
                "exit_code": 1,
            }
        if not server_info.get("secret") or not server_info.get("username"):
            missing = []
            if not server_info.get("secret"):
                missing.append("a secret (private key or password)")
            if not server_info.get("username"):
                missing.append("a username")
            return {
                "error": (
                    f"Server '{server_info.get('server_name', server_id)}' credential is "
                    f"incomplete — missing {' and '.join(missing)}. "
                    f"Edit the credential on the Settings → Credentials page to include "
                    f"a username and private key, then make sure it is linked to this "
                    f"server on the Servers page."
                ),
                "exit_code": 1,
            }
        hostname = server_info["hostname"]
        port = server_info["port"]
        username = server_info.get("username")
        secret = server_info.get("secret")
        passphrase = server_info.get("passphrase")
        auth_method = server_info.get("ssh_auth_method", "password")
    else:
        auth_method = arguments.get("auth_method", "password")

    if not hostname:
        return {"error": "server_id or host is required", "exit_code": 1}

    logger.info("ssh_exec", hostname=hostname, command=command[:80])

    def _run():
        client = None
        try:
            client = _ssh_connect_sync(
                hostname, port, username, secret,
                auth_method=auth_method, timeout=10, passphrase=passphrase,
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            return {
                "server": server_id or hostname,
                "command": command,
                "stdout": stdout.read().decode("utf-8", errors="replace"),
                "stderr": stderr.read().decode("utf-8", errors="replace"),
                "exit_code": exit_code,
            }
        except Exception as e:
            return {"error": str(e), "exit_code": -1, "server": server_id or hostname}
        finally:
            if client:
                client.close()

    return await asyncio.to_thread(_run)


def _ssh_connect_sync(
    hostname, port, username, secret,
    auth_method="password", timeout=10, passphrase=None,
):
    """Synchronous SSH connect for use with asyncio.to_thread.

    Verifies host keys trust-on-first-use against the Vigilus known_hosts
    file; a changed host key raises BadHostKeyException.
    """
    known_hosts = _known_hosts_path()
    client = paramiko.SSHClient()
    client.load_host_keys(known_hosts)
    client.set_missing_host_key_policy(_TofuHostKeyPolicy(known_hosts))
    kw: dict[str, Any] = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if auth_method == "key" and secret:
        key_file = io.StringIO(secret)
        try:
            pkey = paramiko.RSAKey.from_private_key(key_file, password=passphrase)
        except Exception:
            key_file.seek(0)
            try:
                pkey = paramiko.Ed25519Key.from_private_key(key_file, password=passphrase)
            except Exception:
                key_file.seek(0)
                pkey = paramiko.ECDSAKey.from_private_key(key_file, password=passphrase)
        kw["pkey"] = pkey
    else:
        kw["password"] = secret
    client.connect(**kw)
    return client


async def ssh_exec_all(
    arguments: dict[str, Any], operator: Any = None, db=None, **kwargs
) -> dict[str, Any]:
    """Execute a command across multiple servers via SSH in parallel.

    Args (from tool schema):
        server_ids: List of target server IDs.
        command: Shell command to execute.
        timeout: Command timeout in seconds (default 30).
    """
    server_ids = arguments.get("server_ids", [])
    command = arguments.get("command")
    timeout = arguments.get("timeout", 30)

    if not server_ids:
        return {"error": "server_ids is required", "results": {}}
    if not command:
        return {"error": "command is required", "results": {}}

    logger.info("ssh_exec_all", server_count=len(server_ids), command=command[:80])

    # Run all SSH commands concurrently
    tasks = []
    for sid in server_ids:
        tasks.append(
            ssh_exec(
                {"server_id": sid, "command": command, "timeout": timeout},
                operator=operator,
                db=db,
            )
        )

    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for sid, res in zip(server_ids, results_list):
        if isinstance(res, Exception):
            results[sid] = {"error": str(res), "exit_code": -1}
        else:
            results[sid] = res

    return {"results": results}
