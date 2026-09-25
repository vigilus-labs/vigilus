"""ssh_exec_all must resolve servers before fanning out SSH.

SQLAlchemy async sessions are not safe for concurrent use. The fan-out may
overlap SSH connections, but it must not overlap queries on the shared session.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from vigilus.core.crypto import encrypt
from vigilus.db.models import Credential, CredentialType, Server, SshAuthMethod


class _Channel:
    def recv_ready(self):
        return False

    def recv_stderr_ready(self):
        return False

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return 0


class _Stream:
    def __init__(self, data: bytes):
        self.channel = _Channel()
        self._data = data

    def read(self):
        return self._data


class _FakeClient:
    def exec_command(self, command, timeout=None):
        return None, _Stream(b"ok"), _Stream(b"")

    def close(self):
        pass


class _Guard:
    def __init__(self):
        self._lock = threading.Lock()
        self.ssh_active = 0
        self.max_ssh = 0
        self.db_tasks: set[asyncio.Task] = set()
        self.max_db_tasks = 0
        self.db_during_ssh = False
        self.connects: list[dict] = []

    def ssh_enter(self) -> None:
        with self._lock:
            self.ssh_active += 1
            self.max_ssh = max(self.max_ssh, self.ssh_active)

    def ssh_exit(self) -> None:
        with self._lock:
            self.ssh_active -= 1

    def db_enter(self) -> None:
        with self._lock:
            self.db_tasks.add(asyncio.current_task())
            self.max_db_tasks = max(self.max_db_tasks, len(self.db_tasks))
            if self.ssh_active:
                self.db_during_ssh = True

    def db_exit(self) -> None:
        with self._lock:
            self.db_tasks.discard(asyncio.current_task())


def _watch_session(db_session, guard: _Guard, monkeypatch):
    """Fail the overlap check if two tasks use the session at once."""
    real_execute = db_session.execute
    real_get = db_session.get

    async def tracking_execute(*args, **kwargs):
        guard.db_enter()
        try:
            await asyncio.sleep(0.05)
            return await real_execute(*args, **kwargs)
        finally:
            guard.db_exit()

    async def tracking_get(*args, **kwargs):
        guard.db_enter()
        try:
            await asyncio.sleep(0.05)
            return await real_get(*args, **kwargs)
        finally:
            guard.db_exit()

    monkeypatch.setattr(db_session, "execute", tracking_execute)
    monkeypatch.setattr(db_session, "get", tracking_get)


async def _add_server(
    db_session, name: str, hostname: str, *, username: str | None, secret: str | None
):
    cred = None
    if username is not None and secret is not None:
        cred = Credential(
            name=f"{name}-login",
            type=CredentialType.password,
            ssh_auth_method=SshAuthMethod.password,
            username=username,
            secret=encrypt(secret),
        )
        db_session.add(cred)
        await db_session.flush()
    server = Server(
        name=name,
        hostname=hostname,
        port=22,
        credential_id=cred.id if cred else None,
    )
    db_session.add(server)
    await db_session.commit()
    return server


@pytest.mark.asyncio
async def test_ssh_exec_all_resolves_before_parallel_ssh(db_session, monkeypatch):
    """Several servers share one session; SSH overlaps, queries do not."""
    from vigilus.tools.native import ssh

    await _add_server(db_session, "alpha", "10.0.0.1", username="root", secret="alpha-secret")
    await _add_server(db_session, "beta", "10.0.0.2", username="ops", secret="beta-secret")
    gamma = await _add_server(
        db_session, "gamma", "10.0.0.3", username="deploy", secret="gamma-secret"
    )

    guard = _Guard()
    _watch_session(db_session, guard, monkeypatch)

    def fake_connect(
        hostname, port, username, secret, auth_method="password", timeout=10, passphrase=None
    ):
        guard.ssh_enter()
        guard.connects.append(
            {
                "hostname": hostname,
                "port": port,
                "username": username,
                "secret": secret,
                "passphrase": passphrase,
            }
        )
        try:
            time_hold = threading.Event()
            # Overlap the connects so a sequential fan-out would fail max_ssh.
            threading.Timer(0.2, time_hold.set).start()
            time_hold.wait()
            return _FakeClient()
        finally:
            guard.ssh_exit()

    monkeypatch.setattr(ssh, "_ssh_connect_sync", fake_connect)

    result = await ssh.ssh_exec_all(
        {
            "server_ids": ["alpha", "10.0.0.2", gamma.id],
            "command": "uptime",
            "timeout": 5,
        },
        db=db_session,
    )

    assert guard.max_db_tasks == 1
    assert guard.db_during_ssh is False
    assert guard.max_ssh == 3

    by_host = {item["hostname"]: item for item in guard.connects}
    assert by_host["10.0.0.1"]["username"] == "root"
    assert by_host["10.0.0.1"]["secret"] == "alpha-secret"
    assert by_host["10.0.0.2"]["username"] == "ops"
    assert by_host["10.0.0.2"]["secret"] == "beta-secret"
    assert by_host["10.0.0.3"]["username"] == "deploy"
    assert by_host["10.0.0.3"]["secret"] == "gamma-secret"

    assert set(result["results"]) == {"alpha", "10.0.0.2", gamma.id}
    for res in result["results"].values():
        assert res["stdout"] == "ok"
        assert res["exit_code"] == 0
        assert res["command"] == "uptime"


@pytest.mark.asyncio
async def test_ssh_exec_all_bounds_fanout(db_session, monkeypatch):
    from vigilus.tools.native import ssh

    for i in range(3):
        await _add_server(
            db_session,
            f"host{i}",
            f"10.1.0.{i}",
            username="root",
            secret="pw",
        )

    guard = _Guard()
    monkeypatch.setattr(ssh, "_SSH_FANOUT_LIMIT", 1)

    def fake_connect(*args, **kwargs):
        guard.ssh_enter()
        try:
            done = threading.Event()
            threading.Timer(0.05, done.set).start()
            done.wait()
            return _FakeClient()
        finally:
            guard.ssh_exit()

    monkeypatch.setattr(ssh, "_ssh_connect_sync", fake_connect)

    result = await ssh.ssh_exec_all(
        {"server_ids": ["host0", "host1", "host2"], "command": "true"},
        db=db_session,
    )

    assert guard.max_ssh == 1
    assert all(res["exit_code"] == 0 for res in result["results"].values())


@pytest.mark.asyncio
async def test_ssh_exec_all_reports_missing_servers_without_connecting(db_session, monkeypatch):
    from vigilus.tools.native import ssh

    await _add_server(db_session, "alpha", "10.0.0.1", username="root", secret="pw")
    await _add_server(db_session, "bare", "10.0.0.9", username=None, secret=None)

    connects = []
    monkeypatch.setattr(
        ssh, "_ssh_connect_sync", lambda *a, **k: connects.append(1) or _FakeClient()
    )

    result = await ssh.ssh_exec_all(
        {"server_ids": ["alpha", "missing", "bare"], "command": "uptime"},
        db=db_session,
    )

    assert result["results"]["alpha"]["stdout"] == "ok"
    assert "Available servers" in result["results"]["missing"]["error"]
    assert "alpha" in result["results"]["missing"]["error"]
    assert "credential is incomplete" in result["results"]["bare"]["error"]
    assert len(connects) == 1
