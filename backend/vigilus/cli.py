"""Vigilus CLI – init, start, update, chat, and admin commands."""

from __future__ import annotations

import argparse
import os
import sys


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the Vigilus data directory and validate configuration."""
    # Validate secret key is set
    secret = os.environ.get("VIGILUS_SECRET", "")
    if not secret or secret.strip() in {"", "changeme", "secret", "default", "CHANGE_ME"}:
        print(
            "ERROR: VIGILUS_SECRET must be set to a strong, unique value.\n"
            "  export VIGILUS_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load settings to validate
    from vigilus.config import get_settings

    settings = get_settings()

    # Create data directory
    os.makedirs(settings.data_dir, exist_ok=True)
    print(f"✓ Data directory ready: {os.path.abspath(settings.data_dir)}")

    # Run alembic migrations if available
    alembic_ini = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
    if os.path.exists(alembic_ini):
        try:
            from alembic import command
            from alembic.config import Config

            alembic_cfg = Config(alembic_ini)
            command.upgrade(alembic_cfg, "head")
            print("✓ Database migrations applied")
        except Exception as exc:
            print(f"⚠ Alembic migrations skipped: {exc}", file=sys.stderr)
    else:
        # Fall back to creating tables directly
        import asyncio

        from vigilus.db.base import init_db

        asyncio.run(init_db())
        print("✓ Database tables created")

    print("✓ Vigilus initialized successfully")


def cmd_start(args: argparse.Namespace) -> None:
    """Start the Vigilus server."""
    # Validate configuration loads
    from vigilus.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"ERROR: Configuration invalid – {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting Vigilus v0.1.0 on {settings.host}:{settings.port}")
    print(f"  Database: {settings.database_url}")
    print(f"  Trust mode: {settings.default_trust_mode}")
    print(f"  Log level: {settings.log_level}")

    import uvicorn

    uvicorn.run(
        "vigilus.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=args.reload if hasattr(args, "reload") else False,
    )


def cmd_chat(args: argparse.Namespace) -> None:
    """Launch the Vigilus terminal UI."""
    try:
        from vigilus.tui.app import launch
    except ImportError:
        print(
            "ERROR: The TUI requires the 'tui' optional extra.\n"
            "  pip install 'vigilus[tui]'\n"
            "or if installing from source:\n"
            "  pip install -e '.[tui]'",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = getattr(args, "url", None)
    launch(base_url=base_url)


def cmd_user(args: argparse.Namespace) -> None:
    """User management subcommands (create, reset-password, list)."""
    import asyncio
    import getpass

    from sqlalchemy import select

    from vigilus.config import get_settings
    from vigilus.core.auth import hash_password
    from vigilus.db.base import get_session_factory, init_db
    from vigilus.db.models import User

    get_settings()

    async def _run() -> None:
        await init_db()
        factory = get_session_factory()

        if args.user_command == "create":
            username = args.username
            pw = getpass.getpass(f"Password for '{username}': ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("ERROR: Passwords do not match.", file=sys.stderr)
                sys.exit(1)
            if len(pw) < 10:
                print("ERROR: Password must be at least 10 characters.", file=sys.stderr)
                sys.exit(1)
            async with factory() as session:
                exists = await session.scalar(select(User).where(User.username == username))
                if exists:
                    print(f"ERROR: Username '{username}' already taken.", file=sys.stderr)
                    sys.exit(1)
                user = User(username=username, password_hash=hash_password(pw))
                session.add(user)
                await session.commit()
            print(f"✓ User '{username}' created.")

        elif args.user_command == "reset-password":
            username = args.username
            pw = getpass.getpass(f"New password for '{username}': ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("ERROR: Passwords do not match.", file=sys.stderr)
                sys.exit(1)
            if len(pw) < 10:
                print("ERROR: Password must be at least 10 characters.", file=sys.stderr)
                sys.exit(1)
            async with factory() as session:
                user = await session.scalar(select(User).where(User.username == username))
                if user is None:
                    print(f"ERROR: User '{username}' not found.", file=sys.stderr)
                    sys.exit(1)
                user.password_hash = hash_password(pw)
                user.token_version += 1
                await session.commit()
            print(f"✓ Password reset for '{username}'. All existing sessions invalidated.")

        elif args.user_command == "list":
            async with factory() as session:
                result = await session.execute(select(User).order_by(User.created_at))
                users = result.scalars().all()
            if not users:
                print("No users found.")
                return
            print(f"{'USERNAME':<20} {'CREATED':<22} {'LAST LOGIN':<22} {'ACTIVE'}")
            print("-" * 75)
            for u in users:
                created = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "-"
                last_login = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-"
                active = "yes" if u.is_active else "no"
                print(f"{u.username:<20} {created:<22} {last_login:<22} {active}")

    asyncio.run(_run())


def cmd_channels(args: argparse.Namespace) -> None:
    """Channel (Telegram/Discord) management subcommands.

    Lets you configure connected bots and the allowlist from the CLI, before
    the Settings UI exists or for headless setups.
    """
    import asyncio

    from sqlalchemy import select

    from vigilus.config import get_settings
    from vigilus.core.crypto import encrypt
    from vigilus.db.base import get_session_factory, init_db
    from vigilus.db.models import ChannelAccount, ChannelConfig

    get_settings()

    async def _run() -> None:
        await init_db()
        factory = get_session_factory()

        if args.channels_command == "set-token":
            platform = args.platform
            if platform not in ("telegram", "discord"):
                print(
                    f"ERROR: --platform must be telegram or discord, got {platform!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            async with factory() as session:
                cfg = (
                    await session.execute(
                        select(ChannelConfig).where(ChannelConfig.platform == platform)
                    )
                ).scalar_one_or_none()
                if cfg is None:
                    cfg = ChannelConfig(platform=platform, bot_token_enc=encrypt(args.token))
                    session.add(cfg)
                else:
                    cfg.bot_token_enc = encrypt(args.token)
                await session.commit()
            print(f"✓ {platform} bot token stored (encrypted). Restart Vigilus to connect.")

        elif args.channels_command == "allow":
            platform = args.platform
            async with factory() as session:
                acct = (
                    await session.execute(
                        select(ChannelAccount).where(
                            ChannelAccount.platform == platform,
                            ChannelAccount.external_user_id == args.user_id,
                        )
                    )
                ).scalar_one_or_none()
                if acct is None:
                    acct = ChannelAccount(
                        platform=platform,
                        external_user_id=args.user_id,
                        allowed=True,
                        label=args.label,
                    )
                    session.add(acct)
                else:
                    acct.allowed = True
                    if args.label:
                        acct.label = args.label
                await session.commit()
            print(
                f"✓ Allowed {platform} user {args.user_id}"
                + (f" ({args.label})" if args.label else "")
                + "."
            )

        elif args.channels_command == "revoke":
            platform = args.platform
            async with factory() as session:
                acct = (
                    await session.execute(
                        select(ChannelAccount).where(
                            ChannelAccount.platform == platform,
                            ChannelAccount.external_user_id == args.user_id,
                        )
                    )
                ).scalar_one_or_none()
                if acct is None:
                    print(
                        f"ERROR: no {platform} account with user-id {args.user_id}", file=sys.stderr
                    )
                    sys.exit(1)
                acct.allowed = False
                await session.commit()
            print(f"✓ Revoked {platform} user {args.user_id}.")

        elif args.channels_command == "list":
            async with factory() as session:
                configs = (
                    (await session.execute(select(ChannelConfig).order_by(ChannelConfig.platform)))
                    .scalars()
                    .all()
                )
                accounts = (
                    (
                        await session.execute(
                            select(ChannelAccount).order_by(
                                ChannelAccount.platform, ChannelAccount.created_at
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

            print("── Configured bots ──")
            if not configs:
                print("  (none)")
            for c in configs:
                state = "enabled" if c.enabled else "disabled"
                print(f"  {c.platform}: {c.bot_username or '?'} [{state}]")

            print("\n── Allowlist ──")
            if not accounts:
                print("  (none)")
            for a in accounts:
                flag = "✓" if a.allowed else "✗"
                label = f" — {a.label}" if a.label else ""
                print(f"  {flag} {a.platform}:{a.external_user_id}{label}")

    asyncio.run(_run())


def _install_root():
    """Root of the git-managed install (the directory containing backend/)."""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent


def _service_restart_plan(platform: str | None = None, exists=os.path.exists):
    """How to restart the Vigilus service on this host, mirroring what
    install.sh sets up. Returns (description, argv, fallback_hint) or None
    when no managed service is found.
    """
    platform = platform or sys.platform
    home = os.path.expanduser("~")
    if platform == "darwin":
        if exists(os.path.join(home, "Library/LaunchAgents/dev.vigilus.plist")):
            return (
                "launchd service dev.vigilus",
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/dev.vigilus"],
                None,
            )
        return None
    if exists("/etc/systemd/system/vigilus.service"):
        # The CLI wrapper may be running as the unprivileged service user,
        # which can't restart system units — hence the sudo fallback hint.
        return (
            "systemd service vigilus",
            ["systemctl", "restart", "vigilus"],
            "sudo systemctl restart vigilus",
        )
    if exists(os.path.join(home, ".config/systemd/user/vigilus.service")):
        return (
            "user systemd service vigilus",
            ["systemctl", "--user", "restart", "vigilus"],
            None,
        )
    return None


def _update_backend_deps(backend_dir) -> None:
    import subprocess

    print("Installing backend dependencies...")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(backend_dir)]
    )
    if res.returncode != 0:
        print("ERROR: backend dependency install failed (see output above).", file=sys.stderr)
        sys.exit(1)


def _rebuild_frontend(frontend_dir) -> None:
    import shutil
    import subprocess

    if not (frontend_dir / "package.json").exists():
        return
    npm = shutil.which("npm")
    if npm is None:
        print(
            "⚠ npm not found — skipping the frontend rebuild. The web UI will keep "
            "serving the previous build until you run "
            f"'npm --prefix {frontend_dir} install && npm --prefix {frontend_dir} run build'.",
            file=sys.stderr,
        )
        return
    print("Building frontend...")
    for step in (["install", "--silent"], ["run", "build", "--silent"]):
        res = subprocess.run([npm, "--prefix", str(frontend_dir), *step])
        if res.returncode != 0:
            print("ERROR: frontend build failed (see output above).", file=sys.stderr)
            sys.exit(1)


def _run_migrations(backend_dir) -> None:
    import subprocess

    print("Applying database migrations...")
    # Fresh interpreter so the just-updated code (models, migration scripts)
    # is what runs — this process still has the old version imported.
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_dir / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(backend_dir),
    )
    if res.returncode != 0:
        print("ERROR: database migration failed (see output above).", file=sys.stderr)
        sys.exit(1)


def _restart_service() -> None:
    import subprocess

    plan = _service_restart_plan()
    if plan is None:
        print("✓ Update complete. Restart Vigilus to run the new version.")
        return
    description, argv, fallback_hint = plan
    res = subprocess.run(argv, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"✓ Restarted {description}.")
        return
    detail = (res.stderr or res.stdout).strip()
    print(f"⚠ Could not restart {description}: {detail}", file=sys.stderr)
    if fallback_hint:
        print(f"  Restart it yourself with: {fallback_hint}", file=sys.stderr)


def cmd_update(args: argparse.Namespace) -> None:
    """Update a git-managed install to the latest version from GitHub."""
    import re
    import subprocess

    root = _install_root()
    backend_dir = root / "backend"

    if not (root / ".git").exists():
        print(
            "ERROR: this Vigilus install is not managed by git, so it can't self-update.\n"
            "  - Docker: pull the newer image and recreate the container.\n"
            "  - Manual checkout: git pull, then re-run install.sh.",
            file=sys.stderr,
        )
        sys.exit(1)

    def git(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True)

    branch = args.branch
    if not branch:
        head = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        branch = head if head and head != "HEAD" else "main"

    print(f"Checking for updates on '{branch}'...")
    fetch = git("fetch", "--quiet", "origin", branch)
    if fetch.returncode != 0:
        print(f"ERROR: could not fetch from origin:\n{fetch.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    local = git("rev-parse", "HEAD").stdout.strip()
    remote = git("rev-parse", "FETCH_HEAD").stdout.strip()
    up_to_date = local == remote

    incoming = git("log", "--oneline", f"{local}..{remote}").stdout.strip()
    if incoming:
        lines = incoming.splitlines()
        print(f"\n{len(lines)} new commit{'s' if len(lines) != 1 else ''}:")
        for line in lines[:15]:
            print(f"  {line}")
        if len(lines) > 15:
            print(f"  … and {len(lines) - 15} more")
        print()

    if args.check:
        if up_to_date:
            print(f"✓ Already up to date ({local[:7]} on {branch}).")
        else:
            print(f"Update available: {local[:7]} → {remote[:7]}. Run 'vigilus update' to apply.")
        return

    if up_to_date and not args.force:
        print(f"✓ Already up to date ({local[:7]} on {branch}).")
        return

    # A hard reset clobbers tracked-file changes; refuse unless told not to.
    dirty = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty and not args.force:
        print(
            "ERROR: local changes would be overwritten by the update:\n"
            f"{dirty}\n"
            "Commit or stash them, or re-run with --force to discard them.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Updating {local[:7]} → {remote[:7]}...")
    reset = git("reset", "--hard", "--quiet", remote)
    if reset.returncode != 0:
        print(f"ERROR: git reset failed:\n{reset.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    _update_backend_deps(backend_dir)
    _rebuild_frontend(root / "frontend")
    _run_migrations(backend_dir)

    new_version = ""
    try:
        init_src = (backend_dir / "vigilus" / "__init__.py").read_text()
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init_src)
        if match:
            new_version = f"v{match.group(1)} "
    except OSError:
        pass
    print(f"✓ Vigilus updated to {new_version}({remote[:7]}).")

    if args.no_restart:
        print("Restart skipped (--no-restart). The running server is still on the old version.")
        return
    _restart_service()


def cmd_doctor(args: argparse.Namespace) -> None:
    """Check host capabilities and print actionable guidance."""
    from vigilus.core.preflight import check_nmap_access, render_sudoers_fragment

    print("\n=== nmap / privileged-scan access ===")
    nmap = check_nmap_access()
    print(f"  installed       : {nmap.installed}")
    if nmap.path:
        print(f"  path            : {nmap.path}")
    print(f"  privileged scans: {'OK' if nmap.privileged_ok else 'WILL FAIL (-sS/-O/-sU)'}")
    print(f"  method          : {nmap.method}")
    if not nmap.privileged_ok:
        print(f"  -> {nmap.detail}")
        print(
            "     Tip: `vigilus doctor --write-sudoers /tmp/vigilus-nmap.sudoers` "
            "writes a scoped fragment you can review and install."
        )

    if args.write_sudoers:
        fragment, _ = render_sudoers_fragment()
        with open(args.write_sudoers, "w") as f:
            f.write(fragment)
        os.chmod(args.write_sudoers, 0o440)
        print(f"\n✓ Wrote sudoers fragment to {args.write_sudoers}")
        print("  Review it, then install with:")
        print(f"    sudo install -m 440 {args.write_sudoers} /etc/sudoers.d/vigilus-nmap")
        print("    sudo visudo -c   # validate (syntax errors here lock you out of sudo!)")
        print("  Then re-run: vigilus doctor")

    # ── Database migrations ────────────────────────────────
    print("=== database migrations ===")
    try:
        import asyncio

        from vigilus.core.preflight import check_migration_status

        mig = asyncio.run(check_migration_status())
        print(f"  current revision: {mig.current or '(unstamped)'}")
        print(f"  latest revision : {', '.join(mig.heads) or '(unknown)'}")
        print(f"  up to date      : {'YES' if mig.up_to_date else 'NO'}")
        if mig.detail:
            print(f"  -> {mig.detail}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not check migrations: {exc})")
    print()

    # ── Search / research backend ──────────────────────────
    print("=== web search / research ===")
    try:
        import asyncio

        from vigilus.db.base import get_session_factory
        from vigilus.search.registry import probe_search_config

        async def _probe() -> dict:
            factory = get_session_factory()
            async with factory() as session:
                return await probe_search_config(session)

        res = asyncio.run(_probe())
        print(f"  enabled         : {res['enabled']}")
        print(f"  search backend  : {res['search_backend']}")
        print(f"  fetch backend   : {res['fetch_backend']}")
        if res["search_backend"] == "firecrawl" or res["fetch_backend"] == "firecrawl":
            print(
                f"  firecrawl key   : {'configured' if res['firecrawl_key_configured'] else 'MISSING'}"
            )
        print(f"  reachable       : {'OK' if res['ok'] else 'NO'}")
        if res["detail"]:
            print(f"  -> {res['detail']}")
        if res.get("hint"):
            print(f"  -> {res['hint']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not probe search backend: {exc})")
    print()


def main() -> None:
    """Entry point for the ``vigilus`` CLI."""
    parser = argparse.ArgumentParser(
        prog="vigilus",
        description="Vigilus – AI-powered infrastructure management",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── init ────────────────────────────────────────────────
    subparsers.add_parser("init", help="Initialize data directory and database")

    # ── start ───────────────────────────────────────────────
    start_parser = subparsers.add_parser("start", help="Start the Vigilus server")
    start_parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload for development",
    )

    # ── chat ────────────────────────────────────────────────
    chat_parser = subparsers.add_parser(
        "chat", help="Start the Vigilus terminal UI (requires vigilus[tui])"
    )
    chat_parser.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help="Vigilus server URL (default: http://localhost:8000 or saved config)",
    )

    # ── user ────────────────────────────────────────────────
    user_parser = subparsers.add_parser(
        "user", help="User management (create, reset-password, list)"
    )
    user_subparsers = user_parser.add_subparsers(dest="user_command", help="User commands")

    user_create = user_subparsers.add_parser("create", help="Create a new user")
    user_create.add_argument("username", help="Username")

    user_reset = user_subparsers.add_parser("reset-password", help="Reset a user's password")
    user_reset.add_argument("username", help="Username")

    user_subparsers.add_parser("list", help="List all users")

    # ── channels ─────────────────────────────────────────────
    channels_parser = subparsers.add_parser(
        "channels", help="Manage third-party channels (Telegram/Discord)"
    )
    channels_subparsers = channels_parser.add_subparsers(
        dest="channels_command", help="Channel commands"
    )

    ct_set = channels_subparsers.add_parser(
        "set-token", help="Store (encrypted) a bot token for a platform"
    )
    ct_set.add_argument("--platform", required=True, help="telegram | discord")
    ct_set.add_argument(
        "--token", required=True, help="Bot token from BotFather / Developer Portal"
    )

    ct_allow = channels_subparsers.add_parser(
        "allow", help="Allow a platform user to talk to Vigilus"
    )
    ct_allow.add_argument("--platform", required=True, help="telegram | discord")
    ct_allow.add_argument("--user-id", required=True, help="External user id on that platform")
    ct_allow.add_argument("--label", default=None, help="Optional human-readable label")

    ct_revoke = channels_subparsers.add_parser("revoke", help="Revoke a platform user")
    ct_revoke.add_argument("--platform", required=True, help="telegram | discord")
    ct_revoke.add_argument("--user-id", required=True, help="External user id on that platform")

    channels_subparsers.add_parser("list", help="List configured bots and the allowlist")

    # ── update ──────────────────────────────────────────────
    update_parser = subparsers.add_parser(
        "update", help="Update Vigilus to the latest version from GitHub"
    )
    update_parser.add_argument(
        "--check", action="store_true", help="Only report whether an update is available"
    )
    update_parser.add_argument(
        "--branch",
        default=None,
        metavar="BRANCH",
        help="Branch to update from (default: the currently checked-out branch)",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="Discard local changes and update even when already up to date",
    )
    update_parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Don't restart the Vigilus service after updating",
    )

    # ── doctor ────────────────────────────────────────────
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check host capabilities (nmap/sudo, network exposure) and print guidance",
    )
    doctor_parser.add_argument(
        "--write-sudoers",
        metavar="PATH",
        default=None,
        help="Write a ready-to-install nmap sudoers fragment to PATH (review it, then: sudo install -m 440 PATH /etc/sudoers.d/vigilus-nmap && sudo visudo -c)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    match args.command:
        case "init":
            cmd_init(args)
        case "start":
            cmd_start(args)
        case "chat":
            cmd_chat(args)
        case "user":
            if not hasattr(args, "user_command") or args.user_command is None:
                user_parser.print_help()
                sys.exit(0)
            cmd_user(args)
        case "channels":
            if not hasattr(args, "channels_command") or args.channels_command is None:
                channels_parser.print_help()
                sys.exit(0)
            cmd_channels(args)
        case "update":
            cmd_update(args)
        case "doctor":
            cmd_doctor(args)
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
