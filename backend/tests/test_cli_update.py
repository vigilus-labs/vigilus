"""`vigilus update` — git-based self-update of an installed instance.

Runs cmd_update against a real temporary git clone (origin + install) with the
expensive steps (pip, npm, alembic, service restart) stubbed out, verifying
the git decision logic: up-to-date detection, applying updates, refusing to
clobber local changes, --check being read-only, and --force.
"""

import argparse
import os
import subprocess

import pytest

from vigilus import cli


def _git(cwd, *argv):
    res = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def _args(**overrides):
    defaults = dict(check=False, branch=None, force=False, no_restart=True)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """An 'origin' repo and an installed clone, with heavy steps stubbed."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    (origin / "backend").mkdir()
    (origin / "backend" / "vigilus").mkdir()
    (origin / "backend" / "vigilus" / "__init__.py").write_text('__version__ = "0.2.0"\n')
    (origin / "app.txt").write_text("v1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "initial")

    install = tmp_path / "install"
    _git(tmp_path, "clone", "--quiet", str(origin), str(install))

    monkeypatch.setattr(cli, "_install_root", lambda: install)

    calls: list[str] = []
    monkeypatch.setattr(cli, "_update_backend_deps", lambda backend_dir: calls.append("pip"))
    monkeypatch.setattr(cli, "_rebuild_frontend", lambda frontend_dir: calls.append("npm"))
    monkeypatch.setattr(cli, "_run_migrations", lambda backend_dir: calls.append("migrate"))
    monkeypatch.setattr(cli, "_restart_service", lambda: calls.append("restart") or True)
    return origin, install, calls


def _advance_origin(origin, message="update"):
    (origin / "app.txt").write_text(f"{message}\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", message)
    return _git(origin, "rev-parse", "HEAD")


def test_up_to_date_is_a_noop(repos, capsys):
    origin, install, calls = repos
    cli.cmd_update(_args())

    assert "Already up to date" in capsys.readouterr().out
    assert calls == []


def test_update_applies_and_runs_all_steps(repos):
    origin, install, calls = repos
    new_head = _advance_origin(origin)

    cli.cmd_update(_args())

    assert _git(install, "rev-parse", "HEAD") == new_head
    assert calls == ["pip", "npm", "migrate"]  # no_restart=True skips restart


def test_update_restarts_service_by_default(repos):
    origin, install, calls = repos
    _advance_origin(origin)

    cli.cmd_update(_args(no_restart=False))

    assert calls == ["pip", "npm", "migrate", "restart"]


def test_restart_failure_is_an_incomplete_update(repos, monkeypatch, capsys):
    """A failed restart must not report success: the running service is stale."""
    origin, install, calls = repos
    _advance_origin(origin)

    def failed_restart():
        calls.append("restart-failed")
        return False

    monkeypatch.setattr(cli, "_restart_service", failed_restart)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_update(_args(no_restart=False))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "still serving" in err
    assert "not fully updated" in err
    assert calls == ["pip", "npm", "migrate", "restart-failed"]


def test_check_reports_without_modifying(repos, capsys):
    origin, install, calls = repos
    old_head = _git(install, "rev-parse", "HEAD")
    _advance_origin(origin)

    cli.cmd_update(_args(check=True))

    assert "Update available" in capsys.readouterr().out
    assert _git(install, "rev-parse", "HEAD") == old_head
    assert calls == []


def test_dirty_tree_aborts(repos, capsys):
    origin, install, calls = repos
    _advance_origin(origin)
    (install / "app.txt").write_text("local edit\n")
    old_head = _git(install, "rev-parse", "HEAD")

    with pytest.raises(SystemExit):
        cli.cmd_update(_args())

    assert "local changes" in capsys.readouterr().err
    assert _git(install, "rev-parse", "HEAD") == old_head
    assert calls == []


def test_force_discards_local_changes(repos):
    origin, install, calls = repos
    new_head = _advance_origin(origin, "remote wins")
    (install / "app.txt").write_text("local edit\n")

    cli.cmd_update(_args(force=True))

    assert _git(install, "rev-parse", "HEAD") == new_head
    assert (install / "app.txt").read_text() == "remote wins\n"
    assert calls == ["pip", "npm", "migrate"]


def test_non_git_install_fails_with_guidance(tmp_path, monkeypatch, capsys):
    root = tmp_path / "not-a-repo"
    root.mkdir()
    monkeypatch.setattr(cli, "_install_root", lambda: root)

    with pytest.raises(SystemExit):
        cli.cmd_update(_args())

    assert "can't self-update" in capsys.readouterr().err


# ── service restart detection ────────────────────────────────────────────


def test_restart_plan_system_systemd():
    def exists(p):
        return p == "/etc/systemd/system/vigilus.service"

    description, argv, hint = cli._service_restart_plan("linux", exists=exists)
    assert argv == ["systemctl", "restart", "vigilus"]
    assert hint == "sudo systemctl restart vigilus"


def test_restart_plan_user_systemd():
    def exists(p):
        return p.endswith(".config/systemd/user/vigilus.service")

    description, argv, hint = cli._service_restart_plan("linux", exists=exists)
    assert argv == ["systemctl", "--user", "restart", "vigilus"]
    assert hint is None


def test_restart_plan_macos_launchd():
    def exists(p):
        return p.endswith("Library/LaunchAgents/dev.vigilus.plist")

    description, argv, hint = cli._service_restart_plan("darwin", exists=exists)
    assert argv[:3] == ["launchctl", "kickstart", "-k"]


def test_restart_plan_none_when_no_service():
    assert cli._service_restart_plan("linux", exists=lambda p: False) is None
    assert cli._service_restart_plan("darwin", exists=lambda p: False) is None


# ── restart escalation + staleness detection ─────────────────────────────


class _Res:
    def __init__(self, rc, err=""):
        self.returncode = rc
        self.stderr = err
        self.stdout = ""


def test_restart_escalates_to_sudo_for_system_units(monkeypatch, capsys):
    """Non-root + system unit: plain systemctl fails, retry via sudo."""
    monkeypatch.setattr(
        cli,
        "_service_restart_plan",
        lambda platform=None, exists=None: (
            "systemd service vigilus",
            ["systemctl", "restart", "vigilus"],
            "sudo systemctl restart vigilus",
        ),
    )
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    attempted = []

    def fake_run(argv, **kwargs):
        attempted.append(argv)
        return _Res(1, "Access denied") if argv[0] == "systemctl" else _Res(0)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert cli._restart_service() is True
    assert attempted == [
        ["systemctl", "restart", "vigilus"],
        ["sudo", "systemctl", "restart", "vigilus"],
    ]
    assert "retrying with sudo" in capsys.readouterr().out


def test_no_sudo_escalation_for_user_units(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_service_restart_plan",
        lambda platform=None, exists=None: (
            "user systemd service vigilus",
            ["systemctl", "--user", "restart", "vigilus"],
            None,
        ),
    )
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    attempted = []

    def fake_run(argv, **kwargs):
        attempted.append(argv)
        return _Res(0)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert cli._restart_service() is True
    assert attempted == [["systemctl", "--user", "restart", "vigilus"]]


def test_check_and_noop_warn_when_service_never_restarted(repos, monkeypatch, capsys):
    """After an update whose restart never happened, both the --check and the
    no-op path must say the running service is still the old version."""
    origin, install, calls = repos
    _advance_origin(origin)
    cli.cmd_update(_args())  # applies update with no_restart=True (writes stamp)
    capsys.readouterr()
    monkeypatch.setattr(cli, "_service_running_old_code", lambda root: True)

    cli.cmd_update(_args(check=True))
    assert "OLD version" in capsys.readouterr().out

    cli.cmd_update(_args())  # already-up-to-date no-op path
    out = capsys.readouterr().out
    assert "OLD version" in out
    assert "Already up to date" in out


def test_proc_start_epoch_parses_stat_with_spaced_comm(tmp_path):
    proc = tmp_path / "proc"
    pid_dir = proc / "42"
    pid_dir.mkdir(parents=True)
    ticks = 12_345
    # comm "uvicorn worke" contains a space; 19 fields between state and starttime
    (pid_dir / "stat").write_text(
        "42 (uvicorn worke) S 1 42 42 0 -1 4194560 0 0 0 0 " f"1 2 3 4 5 6 7 8 {ticks} 0 0 0\n"
    )
    (proc / "stat").write_text("cpu 0 0 0 0 0 0 0 0 0 0\nbtime 1700000000\n")

    hz = os.sysconf("SC_CLK_TCK")
    assert cli._proc_start_epoch(42, str(proc)) == 1700000000 + ticks / hz
    assert cli._proc_start_epoch(999, str(proc)) is None
