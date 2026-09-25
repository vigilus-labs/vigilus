"""SSH native tool handlers – remote command execution via paramiko."""

from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Any

import paramiko
import structlog

logger = structlog.get_logger(__name__)

# Cap parallel SSH connections so a large inventory cannot open unbounded sockets.
_SSH_FANOUT_LIMIT = 8


def _credential_fields(cred) -> dict[str, Any]:
    """Username and decrypted secret/passphrase for a credential row."""
    if cred is None:
        return {}
    from vigilus.core.crypto import decrypt

    fields: dict[str, Any] = {
        "username": cred.username,
        "ssh_auth_method": cred.ssh_auth_method.value if cred.ssh_auth_method else "password",
    }
    try:
        fields["secret"] = decrypt(cred.secret) if cred.secret else None
    except Exception:
        fields["secret"] = cred.secret
    try:
        fields["passphrase"] = decrypt(cred.passphrase) if cred.passphrase else None
    except Exception:
        fields["passphrase"] = cred.passphrase
    return fields


async def _resolve_server(db, server_ref: str) -> dict[str, Any] | None:
    """Look up a server and its credentials from the DB.

    *server_ref* may be the server's ID, its name, or its hostname — LLMs
    usually know servers by name ("arcane"), not by UUID.
    Returns dict with hostname, port, username, secret, passphrase, ssh_auth_method.
    """
    from sqlalchemy import func, select

    from vigilus.db.models import Credential, Server

    server = await db.get(Server, server_ref)
    if not server:
        ref = server_ref.strip()
        server = (
            (
                await db.execute(
                    select(Server).where(
                        (func.lower(Server.name) == ref.lower()) | (Server.hostname == ref)
                    )
                )
            )
            .scalars()
            .first()
        )
    if not server:
        return None

    result = {
        "hostname": server.hostname,
        "port": server.port,
        "server_name": server.name,
    }

    if server.credential_id:
        cred = await db.get(Credential, server.credential_id)
        result.update(_credential_fields(cred))

    return result


async def _load_servers(db) -> list:
    """Load every server and its credential in a single query."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from vigilus.db.models import Server

    result = await db.execute(select(Server).options(joinedload(Server.credential)))
    return list(result.unique().scalars().all())


def _match_server(servers, server_ref: str):
    """Match a ref the same way ``_resolve_server`` does: id, then name or hostname."""
    for server in servers:
        if server.id == server_ref:
            return server
    ref = server_ref.strip()
    ref_lower = ref.lower()
    for server in servers:
        if server.name.lower() == ref_lower or server.hostname == ref:
            return server
    return None


def _info_from_server(server) -> dict[str, Any]:
    """Connection fields from a server whose credential relationship is already loaded."""
    result = {
        "hostname": server.hostname,
        "port": server.port,
        "server_name": server.name,
    }
    # Read the relationship from the instance dict so an unloaded attribute
    # cannot trigger a lazy IO in this async context.
    cred = server.__dict__.get("credential")
    if cred is not None:
        result.update(_credential_fields(cred))
    return result


def _missing_server_error(server_id: str, servers) -> dict[str, Any]:
    inventory = (
        ", ".join(f"'{s.name}' ({s.hostname})" for s in servers)
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


def _incomplete_credential_error(
    server_info: dict[str, Any], server_id: str
) -> dict[str, Any] | None:
    if server_info.get("secret") and server_info.get("username"):
        return None
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
            return _missing_server_error(server_id, available)
        incomplete = _incomplete_credential_error(server_info, server_id)
        if incomplete:
            return incomplete
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

    # Set when the awaiting turn is cancelled so the worker thread drops the
    # connection instead of running on detached until the timeout.
    stop = threading.Event()
    try:
        return await asyncio.to_thread(
            _run_ssh,
            hostname=hostname,
            port=port,
            username=username,
            secret=secret,
            auth_method=auth_method,
            passphrase=passphrase,
            command=command,
            timeout=timeout,
            server_label=server_id or hostname,
            stop=stop,
        )
    except asyncio.CancelledError:
        stop.set()
        raise


def _collect_output(stdout, stderr, timeout, stop: threading.Event) -> tuple[int, bytes, bytes]:
    """Wait for a remote command to exit, draining its output as it arrives.

    ``recv_exit_status()`` blocks with no timeout, so a command that never
    exits (``tail -f``, a password prompt) would hang the call forever. Output
    is drained while polling so a chatty command cannot stall on a full SSH
    window either. Raises TimeoutError past *timeout* seconds.
    """
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    out, err = bytearray(), bytearray()
    while not channel.exit_status_ready():
        while channel.recv_ready():
            out += channel.recv(32768)
        while channel.recv_stderr_ready():
            err += channel.recv_stderr(32768)
        if stop.is_set():
            raise RuntimeError("Command cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Command timed out after {timeout}s")
        stop.wait(0.05)
    out += stdout.read()
    err += stderr.read()
    return channel.recv_exit_status(), bytes(out), bytes(err)


def _ssh_connect_sync(
    hostname,
    port,
    username,
    secret,
    auth_method="password",
    timeout=10,
    passphrase=None,
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


def _run_ssh(
    *,
    hostname,
    port,
    username,
    secret,
    auth_method,
    passphrase,
    command,
    timeout,
    server_label,
    stop: threading.Event,
) -> dict[str, Any]:
    """Blocking SSH exec. Must not touch a database session."""
    client = None
    try:
        client = _ssh_connect_sync(
            hostname,
            port,
            username,
            secret,
            auth_method=auth_method,
            timeout=10,
            passphrase=passphrase,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code, out, err = _collect_output(stdout, stderr, timeout, stop)
        return {
            "server": server_label,
            "command": command,
            "stdout": out.decode("utf-8", errors="replace"),
            "stderr": err.decode("utf-8", errors="replace"),
            "exit_code": exit_code,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": -1, "server": server_label}
    finally:
        if client:
            client.close()


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

    # Resolve every target on this session before opening a connection.
    # AsyncSession is not safe for concurrent use, so the fan-out below
    # runs only the blocking SSH work and never touches `db`.
    slots: list[dict[str, Any] | None] = []
    pending: list[tuple[int, dict[str, Any]]] = []
    if db is not None:
        servers = await _load_servers(db)
        for sid in server_ids:
            server = _match_server(servers, sid)
            if server is None:
                slots.append(_missing_server_error(sid, servers))
                continue
            info = _info_from_server(server)
            incomplete = _incomplete_credential_error(info, sid)
            if incomplete:
                slots.append(incomplete)
                continue
            pending.append(
                (
                    len(slots),
                    {
                        "hostname": info["hostname"],
                        "port": info["port"],
                        "username": info.get("username"),
                        "secret": info.get("secret"),
                        "auth_method": info.get("ssh_auth_method", "password"),
                        "passphrase": info.get("passphrase"),
                        "command": command,
                        "timeout": timeout,
                        "server_label": sid,
                    },
                )
            )
            slots.append(None)
    else:
        # No session to share. ssh_exec only needs explicit host credentials,
        # which this tool does not accept, so each call reports that itself.
        tasks = [
            ssh_exec(
                {"server_id": sid, "command": command, "timeout": timeout},
                operator=operator,
                db=None,
            )
            for sid in server_ids
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        for sid, res in zip(server_ids, results_list):
            if isinstance(res, Exception):
                results[sid] = {"error": str(res), "exit_code": -1}
            else:
                results[sid] = res
        return {"results": results}

    sem = asyncio.Semaphore(_SSH_FANOUT_LIMIT)

    async def _one(params: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            stop = threading.Event()
            try:
                return await asyncio.to_thread(_run_ssh, stop=stop, **params)
            except asyncio.CancelledError:
                stop.set()
                raise

    gathered = await asyncio.gather(
        *[_one(params) for _idx, params in pending],
        return_exceptions=True,
    )
    for (idx, _params), res in zip(pending, gathered):
        if isinstance(res, Exception):
            slots[idx] = {"error": str(res), "exit_code": -1}
        else:
            slots[idx] = res

    return {"results": {sid: res for sid, res in zip(server_ids, slots)}}
