"""Preflight checks for optional host capabilities.

Currently: whether nmap privileged scans (SYN/OS/UDP) will work, given the
process user. Used by the server lifespan (non-blocking warning) and the
``vigilus doctor`` CLI command.

Why this exists: the nmap MCP server auto-prefixes ``sudo`` for ``-sS``/``-O``/
``-sU`` scans, but Vigilus runs headless (no TTY, no stdin). Without either
passwordless sudo for nmap or the ``cap_net_raw`` capability on the binary,
those scans fail silently with ``sudo: a terminal is required to read the
password``. This module detects that state so it isn't a mystery.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class MigrationStatus:
    """Where the DB schema sits relative to the latest Alembic revision."""

    current: str | None  # revision the DB is stamped at (None = unstamped)
    heads: tuple[str, ...]  # latest revision id(s) in the migrations dir
    up_to_date: bool  # False only when stamped AND behind a known head
    stamped: bool  # whether alembic_version has a row
    detail: str  # human-readable guidance when behind


def _alembic_config():
    """Load alembic.ini if present, else None."""
    from alembic.config import Config

    ini = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
    return Config(ini) if os.path.exists(ini) else None


def alembic_heads() -> tuple[str, ...]:
    """Latest revision id(s) defined under migrations/versions. Empty on any error."""
    try:
        from alembic.script import ScriptDirectory

        cfg = _alembic_config()
        if cfg is None:
            return ()
        return tuple(ScriptDirectory.from_config(cfg).get_heads())
    except Exception:  # noqa: BLE001
        return ()


def _current_revision_sync(sync_connection) -> str | None:  # noqa: ANN001
    from alembic.runtime.migration import MigrationContext

    return MigrationContext.configure(sync_connection).get_current_revision()


async def check_migration_status(database_url: str | None = None) -> MigrationStatus:
    """Compare the DB's stamped revision to the latest migration head.

    Uses a throwaway async engine so it is safe to call both inside the running
    app (its own event loop) and from one-shot CLI ``asyncio.run`` contexts.
    Never raises — on any error it reports up_to_date=True (fail open: the check
    is advisory and must never block startup).
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from vigilus.config import get_settings

    heads = alembic_heads()
    url = database_url or get_settings().database_url
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            current = await conn.run_sync(_current_revision_sync)
    except Exception as e:  # noqa: BLE001
        return MigrationStatus(None, heads, True, False, f"Could not read DB revision: {e}")
    finally:
        await engine.dispose()

    stamped = current is not None
    # A fresh, unstamped DB (created by create_all) is treated as fine: its
    # schema matches the models even though no revision is recorded. We only
    # flag the dangerous case — a DB stamped at an OLD revision, where column
    # additions from later migrations never ran.
    up_to_date = (not heads) or (not stamped) or (current in heads)
    detail = ""
    if stamped and heads and current not in heads:
        detail = (
            f"Database schema is behind: stamped at {current}, latest is "
            f"{', '.join(heads)}. New columns/tables from pending migrations are "
            "NOT applied — queries referencing them will fail. Stop the server, "
            "run `vigilus init` (alembic upgrade head), then restart."
        )
    return MigrationStatus(current, heads, up_to_date, stamped, detail)


@dataclass
class NmapAccess:
    installed: bool
    path: str | None
    privileged_ok: bool  # can -sS/-O/-sU scans run?
    method: str  # "sudo-nopasswd" | "caps" | "root" | "none"
    detail: str  # human-readable explanation


def check_nmap_access() -> NmapAccess:
    """Detect whether nmap (and privileged nmap scans) will work.

    Never raises — returns a NmapAccess with installed=False on any error.
    Safe to call at startup; the heaviest operation is one ``sudo -n`` probe.
    """
    path = shutil.which("nmap")
    if not path:
        return NmapAccess(
            installed=False,
            path=None,
            privileged_ok=False,
            method="none",
            detail="nmap is not installed where Vigilus runs. Install it (e.g. "
            "`sudo apt install nmap`) — the nmap MCP server shells out to the "
            "`nmap` binary on its PATH.",
        )

    # Already root → everything works.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return NmapAccess(True, path, True, "root", "Running as root; privileged scans work.")

    # setcap on the binary (no sudo needed at all).
    if _has_raw_caps(path):
        return NmapAccess(
            True,
            path,
            True,
            "caps",
            "nmap has cap_net_raw — privileged scans work without sudo.",
        )

    # Passwordless sudo for nmap (what the MCP server relies on).
    if _sudo_nopasswd_for_nmap():
        return NmapAccess(
            True,
            path,
            True,
            "sudo-nopasswd",
            "Passwordless sudo for nmap is configured — privileged scans work.",
        )

    return NmapAccess(
        installed=True,
        path=path,
        privileged_ok=False,
        method="none",
        detail=(
            "nmap is installed but privileged scans (-sS SYN, -O OS detection, "
            "-sU UDP) will FAIL: Vigilus runs headless (no TTY) so `sudo` can't "
            "prompt for a password, and the nmap binary has neither cap_net_raw "
            "nor a NOPASSWD sudoers entry. Non-privileged scans (-sT, -sV, -sn) "
            "still work. Fix with EITHER: a sudoers fragment "
            "(`creimer ALL=(root) NOPASSWD: /usr/bin/nmap` + `Defaults:creimer "
            "!requiretty`) OR `sudo setcap cap_net_raw,cap_net_admin,cap_net_bind_service+eip "
            f"{path}`. Run `vigilus doctor` for details."
        ),
    )


def _sudo_nopasswd_for_nmap() -> bool:
    """True if `sudo -n nmap --version` succeeds (NOPASSWD sudoers entry)."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "-n", "nmap", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _has_raw_caps(nmap_path: str) -> bool:
    """True if the nmap binary carries cap_net_raw (via getcap)."""
    getcap = shutil.which("getcap")
    if not getcap:
        return False
    try:
        r = subprocess.run([getcap, nmap_path], capture_output=True, text=True, timeout=5)
        out = r.stdout or ""
        return "cap_net_raw" in out and "cap_net_raw+ep" in out.replace(" ", "")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# A ready-to-install sudoers fragment. Written to a temp file by the CLI's
# `doctor` command so the user can review + install it with visudo validation.
# Scoped to ONLY nmap (not blanket root) and with !requiretty (the subprocess
# has no TTY). {user} and {nmap_path} are filled in at runtime.
SUDOERS_FRAGMENT_TEMPLATE = """\
# Vigilus — allow passwordless nmap for privileged scans (SYN/OS/UDP).
# The nmap MCP server runs headless (no TTY), so a normal sudo prompt fails;
# this scoped entry lets it run `sudo nmap` without a password.
# Install with:  sudo install -m 440 <file> /etc/sudoers.d/vigilus-nmap
# (then verify with: sudo visudo -c)
Defaults:{user} !requiretty
{user} ALL=(root) NOPASSWD: {nmap_path}
"""


def render_sudoers_fragment() -> tuple[str, str]:
    """Return (fragment_text, suggested_install_command) for the current user.

    Uses the SUDO_USER if present (so `sudo vigilus doctor` still attributes the
    fragment to the real user), else the current user.
    """
    import pwd

    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "vigilus"
    try:
        pwd.getpwnam(user)
    except KeyError:
        user = "vigilus"
    nmap_path = shutil.which("nmap") or "/usr/bin/nmap"
    fragment = SUDOERS_FRAGMENT_TEMPLATE.format(user=user, nmap_path=nmap_path)
    return fragment, nmap_path
