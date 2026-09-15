"""In-app self-update: run the CLI updater as a background job from the web UI.

The CLI (``vigilus update``) already implements the whole update — fetch,
reset, deps, frontend build, migrations, service restart. This module wraps it
as a managed subprocess so the Settings page can trigger it and follow the
output.

Lifecycle notes:

- When the update succeeds, the final step restarts the service, which kills
  this process mid-job — the UI treats "the server went away" as the expected
  final phase of a *successful* update and polls until the new version is up.
  After a restart the job state is naturally back to ``idle`` (fresh process).
- When the update fails, the service stays up and the job ends in ``error``
  with the full output for the UI to display.
- Systemd installs ship a polkit rule (see install.sh) that lets the service
  user restart exactly its own unit; without it the restart step fails and the
  job reports it, leaving the code updated but the service stale.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

_job: dict = {
    "state": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "output": [],
}
_lock = threading.Lock()
_MAX_OUTPUT_LINES = 400


def _install_root() -> Path:
    """Root of the git-managed install (same notion as the CLI updater)."""
    from vigilus.cli import _install_root as cli_root

    return Path(cli_root())


def can_self_update() -> bool:
    """True when this install is a writable git checkout (CLI-updatable)."""
    try:
        root = _install_root()
    except Exception:  # noqa: BLE001 — best-effort capability probe
        return False
    return (root / ".git").exists() and os.access(root, os.W_OK)


def get_job() -> dict:
    """Snapshot of the current (or last) update job, plus self-update ability."""
    with _lock:
        job = dict(_job)
    job["can_self_update"] = can_self_update()
    return job


def start_update() -> dict:
    """Start ``vigilus update`` in a background thread.

    Raises ``RuntimeError`` when an update is already running or when this
    install can't self-update (e.g. Docker, read-only checkout).
    """
    with _lock:
        if _job["state"] == "running":
            raise RuntimeError("an update is already running")
        if not can_self_update():
            raise RuntimeError(
                "this install is not a writable git checkout — update it with "
                "'docker pull' (Docker) or by re-running install.sh (manual)"
            )
        _job.update(
            state="running",
            started_at=time.time(),
            finished_at=None,
            exit_code=None,
            output=[],
        )
        threading.Thread(target=_run_update, name="vigilus-selfupdate", daemon=True).start()
    return get_job()


def _run_update() -> None:
    lines: deque[str] = deque(maxlen=_MAX_OUTPUT_LINES)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "vigilus.cli", "update"],
            cwd=str(_install_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.append(raw.rstrip("\n"))
            with _lock:
                _job["output"] = list(lines)
        exit_code = proc.wait()
    except Exception as exc:  # noqa: BLE001 — surface any failure as job output
        lines.append(f"update could not run: {exc}")
        exit_code = -1
    with _lock:
        _job.update(
            state="done" if exit_code == 0 else "error",
            exit_code=exit_code,
            finished_at=time.time(),
            output=list(lines),
        )
