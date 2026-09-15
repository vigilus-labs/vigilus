"""In-app self-update (web UI → `vigilus update` subprocess)."""

import threading

import pytest

from vigilus import cli
from vigilus.core import selfupdate


@pytest.fixture(autouse=True)
def _reset_job():
    """Each test starts from a clean idle job (draining leftover threads)."""
    for _ in range(500):
        if selfupdate.get_job()["state"] != "running":
            break
        threading.Event().wait(0.01)
    with selfupdate._lock:
        selfupdate._job.update(
            state="idle", started_at=None, finished_at=None, exit_code=None, output=[]
        )
    yield


class FakePopen:
    """Stands in for subprocess.Popen in the update runner."""

    def __init__(self, lines, exit_code=0, gate=None):
        self._lines = lines
        self.exit_code = exit_code
        self.stdout = self._read(gate)

    def _read(self, gate):
        for line in self._lines:
            if gate is not None:
                gate.wait(timeout=10)
            yield line + "\n"

    def wait(self):
        return self.exit_code


def _make_git_root(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


async def test_can_self_update_requires_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_install_root", lambda: tmp_path)
    assert selfupdate.can_self_update() is False

    _make_git_root(tmp_path)
    assert selfupdate.can_self_update() is True


async def test_start_update_refuses_non_git_install(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_install_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="not a writable git checkout"):
        selfupdate.start_update()

    assert selfupdate.get_job()["state"] == "idle"


async def test_update_job_lifecycle(tmp_path, monkeypatch):
    root = _make_git_root(tmp_path)
    monkeypatch.setattr(cli, "_install_root", lambda: root)

    started = threading.Event()
    gate = threading.Event()

    def fake_popen(argv, **kwargs):
        started.set()
        return FakePopen(["Checking for updates...", "✓ updated"], gate=gate)

    monkeypatch.setattr(selfupdate.subprocess, "Popen", fake_popen)

    job = selfupdate.start_update()
    assert job["state"] == "running"
    assert job["can_self_update"] is True

    # While running, a second start is refused
    with pytest.raises(RuntimeError, match="already running"):
        selfupdate.start_update()

    gate.set()
    for _ in range(200):
        if selfupdate.get_job()["state"] != "running":
            break
        threading.Event().wait(0.01)

    job = selfupdate.get_job()
    assert job["state"] == "done"
    assert job["exit_code"] == 0
    assert job["finished_at"] is not None
    assert "✓ updated" in job["output"]
    # finished job can be re-run
    assert selfupdate.start_update()["state"] in ("running", "done")


async def test_update_job_failure_is_reported(tmp_path, monkeypatch):
    root = _make_git_root(tmp_path)
    monkeypatch.setattr(cli, "_install_root", lambda: root)

    def fake_popen(argv, **kwargs):
        return FakePopen(["fetching...", "ERROR: git reset failed"], exit_code=1)

    monkeypatch.setattr(selfupdate.subprocess, "Popen", fake_popen)

    selfupdate.start_update()
    for _ in range(200):
        if selfupdate.get_job()["state"] != "running":
            break
        threading.Event().wait(0.01)

    job = selfupdate.get_job()
    assert job["state"] == "error"
    assert job["exit_code"] == 1
    assert any("git reset failed" in line for line in job["output"])


async def test_update_runs_cli_module_as_subprocess(tmp_path, monkeypatch):
    root = _make_git_root(tmp_path)
    monkeypatch.setattr(cli, "_install_root", lambda: root)
    seen = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return FakePopen(["ok"])

    monkeypatch.setattr(selfupdate.subprocess, "Popen", fake_popen)

    selfupdate.start_update()
    for _ in range(200):
        if selfupdate.get_job()["state"] != "running":
            break
        threading.Event().wait(0.01)

    assert seen["argv"][-2:] == ["vigilus.cli", "update"]  # -m vigilus.cli update
    assert seen["cwd"] == str(root)


# ── API wiring ───────────────────────────────────────────────────────────


async def test_run_update_endpoint(async_client, monkeypatch, tmp_path):
    """POST /api/system/update/run starts a job and wires errors to codes."""
    root = _make_git_root(tmp_path)
    monkeypatch.setattr(cli, "_install_root", lambda: root)
    monkeypatch.setattr(selfupdate.subprocess, "Popen", lambda argv, **kw: FakePopen(["ok"]))

    res = await async_client.post("/api/system/update/run")
    assert res.status_code == 200
    assert res.json()["state"] in ("running", "done")

    # Job status endpoint reflects the job and the install capability
    res = await async_client.get("/api/system/update/job")
    assert res.status_code == 200
    body = res.json()
    assert body["can_self_update"] is True
    assert body["state"] in ("running", "done")


async def test_run_update_endpoint_rejects_docker_installs(async_client, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_install_root", lambda: tmp_path)  # no .git

    res = await async_client.post("/api/system/update/run")
    assert res.status_code == 400
    assert "not a writable git checkout" in res.json()["detail"]
