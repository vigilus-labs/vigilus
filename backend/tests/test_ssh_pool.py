"""SSH connection pooling (#46).

Consecutive commands to one host reuse a transport; credential edits,
errors, timeouts, cancellations, and idle timeouts evict pooled connections.
Explicit (non-DB) credentials are never pooled.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from vigilus.core.crypto import encrypt
from vigilus.db.models import Credential, CredentialType, Server, SshAuthMethod
from vigilus.tools.native import ssh


class _Transport:
    def __init__(self):
        self.active = True

    def is_active(self):
        return self.active


class _Channel:
    def __init__(self, hold: threading.Event | None = None):
        self.hold = hold
        self.closed = False

    def recv_ready(self):
        return False

    def recv_stderr_ready(self):
        return False

    def exit_status_ready(self):
        return self.hold is None or self.hold.is_set()

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True


class _Stream:
    def __init__(self, channel: _Channel, data: bytes = b"ok"):
        self.channel = channel
        self._data = data

    def read(self):
        return self._data


class _FakeClient:
    def __init__(self, factory: _Factory, behavior: str = "ok"):
        self.factory = factory
        self.behavior = behavior
        self.transport = _Transport()
        self.channel = _Channel()
        self.exec_count = 0
        self.closed = False
        factory.clients.append(self)

    def get_transport(self):
        return self.transport

    def exec_command(self, command, timeout=None):
        self.exec_count += 1
        if self.behavior == "raise":
            raise RuntimeError("channel broke")
        return None, _Stream(self.channel), _Stream(self.channel, b"")

    def close(self):
        self.closed = True
        self.transport.active = False
        self.channel.closed = True


class _Factory:
    """Records every _ssh_connect_sync call; hands out fake clients."""

    def __init__(self):
        self.clients: list[_FakeClient] = []
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return _FakeClient(self)

    @property
    def last(self) -> _FakeClient:
        return self.clients[-1]


@pytest.fixture(autouse=True)
def _isolated_pool():
    ssh._SSH_POOL.clear()
    yield
    ssh._SSH_POOL.clear()


async def _add_server(db_session, name: str, secret: str = "pw") -> Server:
    cred = Credential(
        name=f"{name}-login",
        type=CredentialType.password,
        ssh_auth_method=SshAuthMethod.password,
        username="root",
        secret=encrypt(secret),
    )
    db_session.add(cred)
    await db_session.flush()
    server = Server(name=name, hostname=f"{name}.test", port=22, credential_id=cred.id)
    db_session.add(server)
    await db_session.commit()
    return server


@pytest.mark.asyncio
async def test_consecutive_execs_reuse_connection(db_session, monkeypatch):
    await _add_server(db_session, "alpha")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    for _ in range(3):
        res = await ssh.ssh_exec({"server_id": "alpha", "command": "uptime"}, db=db_session)
        assert res["exit_code"] == 0

    assert factory.calls == 1
    assert factory.last.exec_count == 3
    assert len(ssh._SSH_POOL) == 1


@pytest.mark.asyncio
async def test_credential_edit_invalidates_pooled_connection(db_session, monkeypatch):
    server = await _add_server(db_session, "alpha", secret="old-secret")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "uptime"}, db=db_session))[
        "exit_code"
    ] == 0

    cred = await db_session.get(Credential, server.credential_id)
    cred.secret = encrypt("new-secret")
    await db_session.commit()

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "uptime"}, db=db_session))[
        "exit_code"
    ] == 0

    assert factory.calls == 2, "edited credential must force a fresh connection"
    assert factory.clients[1].exec_count == 1
    assert len(ssh._SSH_POOL) == 2, "old-key entry idles out rather than being reused"


@pytest.mark.asyncio
async def test_error_evicts_connection(db_session, monkeypatch):
    await _add_server(db_session, "alpha")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0

    # The pooled connection breaks on its next use.
    factory.last.behavior = "raise"
    broken = await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session)
    assert "channel broke" in broken["error"]
    assert factory.clients[0].closed, "connection that errored must be evicted and closed"
    assert len(ssh._SSH_POOL) == 0

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_timeout_evicts_connection(db_session, monkeypatch):
    """A command that never exits must honour timeout and leave no pooled
    connection behind; its channel is closed via the eviction."""
    await _add_server(db_session, "alpha")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    factory.last.channel.hold = threading.Event()  # command never exits now

    result = await asyncio.wait_for(
        ssh.ssh_exec({"server_id": "alpha", "command": "tail -f", "timeout": 0.5}, db=db_session),
        timeout=10,
    )
    assert "timed out" in result["error"].lower()
    assert factory.last.closed
    assert len(ssh._SSH_POOL) == 0


def test_cancelled_command_evicts_connection(monkeypatch):
    """A stop-event cancellation must evict the pooled connection (its close
    also kills the remote channel)."""
    factory = _Factory()

    def fake_connect(*args, **kwargs):
        return _FakeClient(factory)

    monkeypatch.setattr(ssh, "_ssh_connect_sync", fake_connect)

    stop = threading.Event()

    def first_ok_then_cancelled():
        first = ssh._run_ssh(
            hostname="h",
            port=22,
            username="u",
            secret="s",
            auth_method="password",
            passphrase=None,
            command="ok",
            timeout=5,
            server_label="h",
            stop=threading.Event(),
            pooled=True,
        )
        assert first["exit_code"] == 0

        # Second command hangs until the stop event fires.
        factory.last.channel.hold = threading.Event()
        second = ssh._run_ssh(
            hostname="h",
            port=22,
            username="u",
            secret="s",
            auth_method="password",
            passphrase=None,
            command="hang",
            timeout=30,
            server_label="h",
            stop=stop,
            pooled=True,
        )
        assert "cancelled" in second["error"].lower()

    thread = threading.Thread(target=first_ok_then_cancelled)
    thread.start()
    time.sleep(0.1)
    stop.set()
    thread.join(10)
    assert not thread.is_alive()

    assert factory.clients[0].closed
    assert len(ssh._SSH_POOL) == 0


@pytest.mark.asyncio
async def test_idle_connection_is_reconnected(db_session, monkeypatch):
    await _add_server(db_session, "alpha")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    for conn in ssh._SSH_POOL.values():
        conn.last_used = time.monotonic() - (ssh._SSH_POOL_IDLE_SECONDS + 1)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    assert factory.calls == 2
    assert factory.clients[0].closed


@pytest.mark.asyncio
async def test_dead_transport_is_reconnected(db_session, monkeypatch):
    await _add_server(db_session, "alpha")
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    factory.last.transport.active = False  # transport died server-side

    assert (await ssh.ssh_exec({"server_id": "alpha", "command": "true"}, db=db_session))[
        "exit_code"
    ] == 0
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_explicit_host_credentials_are_never_pooled(monkeypatch):
    factory = _Factory()
    monkeypatch.setattr(ssh, "_ssh_connect_sync", factory)

    result = await ssh.ssh_exec(
        {"host": "adhoc.test", "command": "uptime", "username": "root", "password": "pw"}
    )
    assert result["exit_code"] == 0
    assert factory.calls == 1
    assert factory.last.closed, "non-pooled call keeps connect-per-command behaviour"
    assert len(ssh._SSH_POOL) == 0


def test_pool_key_fingerprints_the_credential():
    """Editing a credential must change the pool key (credential version)."""
    base = ssh._pool_key("h", 22, "u", "password", "secret-a")
    rotated = ssh._pool_key("h", 22, "u", "password", "secret-b")
    same = ssh._pool_key("h", 22, "u", "password", "secret-a")
    other_host = ssh._pool_key("h2", 22, "u", "password", "secret-a")

    assert base != rotated
    assert base == same
    assert base != other_host
    assert "secret-a" not in str(base), "raw secret must never appear in the key"
