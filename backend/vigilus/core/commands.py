"""Shared slash-command registry and handlers.

One registry serves both clients: the web chat and the TUI fetch the same
command list (GET /api/commands) for autocomplete and dispatch server-side
commands to the same endpoint (POST /api/commands/run). Adding a command here
makes it appear in both clients automatically.

Handlers reuse the same service/DB code paths as the REST routes — they are a
second front door, not a parallel implementation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.models import Memory, Operator, Provider, Session
from vigilus.schemas.command import CommandArg, CommandResult, CommandSpec

Handler = Callable[[str, "Session | None", AsyncSession], Awaitable[CommandResult]]


@dataclass
class Command:
    spec: CommandSpec
    handler: Handler | None = None  # None for client-executed commands


_REGISTRY: dict[str, Command] = {}


def _register(spec: CommandSpec, handler: Handler | None = None) -> None:
    _REGISTRY[spec.name] = Command(spec=spec, handler=handler)


def get_command_specs() -> list[CommandSpec]:
    return [cmd.spec for cmd in _REGISTRY.values()]


# ── Helpers ─────────────────────────────────────────────────


def _session_dict(s: Session) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "operator_id": s.operator_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
    }


def _error(text: str) -> CommandResult:
    return CommandResult(kind="error", text=text)


def _relative_age(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def _list_sessions(db: AsyncSession) -> list[Session]:
    result = await db.execute(select(Session).order_by(Session.last_active_at.desc()))
    return list(result.scalars().all())


# ── Handlers ────────────────────────────────────────────────


async def _cmd_help(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    lines = ["| Command | Description |", "| --- | --- |"]
    for cmd in _REGISTRY.values():
        lines.append(f"| `{cmd.spec.usage}` | {cmd.spec.summary} |")
    return CommandResult(kind="markdown", text="\n".join(lines))


async def _cmd_status(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus import __version__
    from vigilus.core.orchestrator import load_orchestrator_config
    from vigilus.core.tasks import get_task_registry

    cfg = load_orchestrator_config()
    provider_name = "not configured"
    model = cfg.model or "provider default"
    if cfg.provider_id:
        provider = await db.get(Provider, cfg.provider_id)
        if provider:
            provider_name = provider.name
            if not cfg.model:
                model = provider.default_model or "provider default"
        else:
            provider_name = "missing (reconfigure)"

    operators = (
        (await db.execute(select(Operator).where(Operator.enabled.is_(True)))).scalars().all()
    )
    running = get_task_registry().list_running()

    text = (
        f"**Vigilus** v{__version__}\n\n"
        f"- Orchestrator provider: **{provider_name}**\n"
        f"- Model: **{model}**\n"
        f"- Enabled operators: **{len(operators)}**\n"
        f"- Running tasks: **{len(running)}**"
    )
    return CommandResult(kind="markdown", text=text)


async def _cmd_new(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    new_session = Session(title=args.strip() or "New Chat", origin="web")
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return CommandResult(
        kind="session_created",
        text=f"Started new chat: **{new_session.title}**",
        data={"session": _session_dict(new_session)},
    )


async def _cmd_sessions(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    sessions = await _list_sessions(db)
    if not sessions:
        return CommandResult(kind="markdown", text="No sessions yet — `/new` starts one.")
    lines = []
    for i, s in enumerate(sessions, start=1):
        marker = " ← current" if session and s.id == session.id else ""
        lines.append(
            f"{i}. **{s.title or 'Chat Session'}** — {_relative_age(s.last_active_at)}{marker}"
        )
    lines.append("\nSwitch with `/switch <number|title>`.")
    return CommandResult(
        kind="markdown",
        text="\n".join(lines),
        data={"sessions": [_session_dict(s) for s in sessions]},
    )


async def _cmd_switch(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    query = args.strip()
    if not query:
        return _error("Usage: `/switch <number|title>` — see `/sessions` for the list.")

    sessions = await _list_sessions(db)
    target: Session | None = None

    if query.isdigit():
        idx = int(query)
        if 1 <= idx <= len(sessions):
            target = sessions[idx - 1]
        else:
            return _error(f"No session #{idx} — `/sessions` lists {len(sessions)}.")
    else:
        matches = [s for s in sessions if (s.title or "").lower().startswith(query.lower())]
        if len(matches) == 1:
            target = matches[0]
        elif len(matches) > 1:
            names = ", ".join(f"**{s.title}**" for s in matches[:5])
            return _error(f"Ambiguous — matches {names}. Be more specific or use the number.")
        else:
            return _error(f"No session titled like “{query}”. See `/sessions`.")

    return CommandResult(
        kind="session_switch",
        text=f"Switched to **{target.title or 'Chat Session'}**",
        data={"session": _session_dict(target)},
    )


async def _cmd_rename(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    title = args.strip()
    if not title:
        return _error("Usage: `/rename <new title>`")
    assert session is not None
    session.title = title
    await db.commit()
    await db.refresh(session)
    return CommandResult(
        kind="config_changed",
        text=f"Renamed chat to **{title}**",
        data={"session": _session_dict(session)},
    )


async def _cmd_delete(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    assert session is not None
    title = session.title or "Chat Session"
    session_id = session.id
    await db.delete(session)
    await db.commit()
    return CommandResult(
        kind="session_deleted",
        text=f"Deleted **{title}**",
        data={"session_id": session_id},
    )


async def _cmd_model(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.orchestrator import load_orchestrator_config, save_orchestrator_config

    cfg = load_orchestrator_config()
    model = args.strip()

    if not model:
        provider_name = "not configured"
        if cfg.provider_id:
            provider = await db.get(Provider, cfg.provider_id)
            provider_name = provider.name if provider else "missing"
        current = cfg.model or "provider default"
        return CommandResult(
            kind="markdown",
            text=f"Provider: **{provider_name}** · Model: **{current}**\n\nSet with `/model <name>`.",
        )

    cfg.model = model
    save_orchestrator_config(cfg)
    return CommandResult(kind="config_changed", text=f"Orchestrator model set to **{model}**")


async def _cmd_provider(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.orchestrator import load_orchestrator_config, save_orchestrator_config

    cfg = load_orchestrator_config()
    providers = (await db.execute(select(Provider).order_by(Provider.name))).scalars().all()
    query = args.strip()

    if not query:
        if not providers:
            return CommandResult(
                kind="markdown",
                text="No providers configured — use `/login` to add one.",
            )
        lines = []
        for p in providers:
            tags = []
            if p.id == cfg.provider_id:
                tags.append("orchestrator")
            if p.is_default:
                tags.append("default")
            if not p.enabled:
                tags.append("disabled")
            suffix = f" _({', '.join(tags)})_" if tags else ""
            lines.append(f"- **{p.name}** — {p.type.value}{suffix}")
        lines.append("\nSwitch with `/provider <name>`.")
        return CommandResult(kind="markdown", text="\n".join(lines))

    enabled = [p for p in providers if p.enabled]
    matches = [p for p in enabled if p.name.lower() == query.lower()]
    if not matches:
        matches = [p for p in enabled if p.name.lower().startswith(query.lower())]
    if not matches:
        return _error(f"No enabled provider named “{query}”. `/provider` lists them.")
    if len(matches) > 1:
        names = ", ".join(f"**{p.name}**" for p in matches)
        return _error(f"Ambiguous — matches {names}.")

    target = matches[0]
    cfg.provider_id = target.id
    # The previous model belongs to the old provider — fall back to the new
    # provider's default rather than sending a mismatched model name.
    cfg.model = None
    save_orchestrator_config(cfg)
    model = target.default_model or "provider default"
    return CommandResult(
        kind="config_changed",
        text=f"Orchestrator provider set to **{target.name}** (model: {model}). Override with `/model <name>`.",
    )


async def _cmd_operators(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    operators = (
        (
            await db.execute(
                select(Operator)
                .where(Operator.enabled.is_(True))
                .order_by(Operator.name)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    if not operators:
        return CommandResult(kind="markdown", text="No enabled operators.")
    lines = [
        f"- **{op.name}** ({op.permission_level.value}) — {op.description}" for op in operators
    ]
    lines.append("\nTag one in a message with `@OperatorName` to delegate directly.")
    return CommandResult(kind="markdown", text="\n".join(lines))


async def _cmd_memory(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.memory import save_memory

    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        memories = (
            (await db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(30)))
            .scalars()
            .all()
        )
        if not memories:
            return CommandResult(
                kind="markdown", text="No memories saved yet — `/memory add <text>`."
            )
        lines = []
        for m in memories:
            content = m.content if len(m.content) <= 100 else m.content[:97] + "…"
            lines.append(f"- `{m.id[:8]}` [{m.scope}] {content}")
        return CommandResult(kind="markdown", text="\n".join(lines))

    if sub == "add":
        if not rest.strip():
            return _error("Usage: `/memory add <text>`")
        await save_memory(db, scope="global", content=rest.strip(), source="user")
        await db.commit()
        return CommandResult(kind="config_changed", text="Memory saved.")

    if sub == "rm":
        prefix = rest.strip()
        if not prefix:
            return _error("Usage: `/memory rm <id-prefix>` — ids are shown by `/memory list`.")
        memories = (await db.execute(select(Memory))).scalars().all()
        matches = [m for m in memories if m.id.startswith(prefix)]
        if not matches:
            return _error(f"No memory with id starting “{prefix}”.")
        if len(matches) > 1:
            return _error(
                f"Prefix “{prefix}” matches {len(matches)} memories — use more characters."
            )
        await db.delete(matches[0])
        await db.commit()
        return CommandResult(kind="config_changed", text="Memory deleted.")

    return _error("Usage: `/memory [list | add <text> | rm <id-prefix>]`")


async def _cmd_tasks(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.tasks import get_task_registry

    tasks = get_task_registry().list_running()
    if not tasks:
        return CommandResult(kind="markdown", text="Nothing running right now.")
    lines = []
    for t in tasks:
        step = "stopping…" if t["cancelling"] else t["current_step"]
        lines.append(f"- **{t['title']}** — {step} ({round(t['elapsed_seconds'])}s)")
    lines.append("\nStop the current chat's task with `/stop`.")
    return CommandResult(kind="markdown", text="\n".join(lines))


async def _cmd_stop(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.tasks import get_task_registry

    assert session is not None
    if get_task_registry().cancel(session.id):
        return CommandResult(kind="stopped", text="Stopping the running task…")
    return _error("Nothing is running in this chat.")


async def _cmd_soul(args: str, session: Session | None, db: AsyncSession) -> CommandResult:
    from vigilus.core.orchestrator import load_orchestrator_config, save_orchestrator_config

    cfg = load_orchestrator_config()
    text = args.strip()
    if not text:
        current = cfg.soul or "_not set_"
        return CommandResult(
            kind="markdown",
            text=f"**Soul** (Vigilus's persona):\n\n{current}\n\nSet with `/soul <text>`.",
        )
    cfg.soul = text
    save_orchestrator_config(cfg)
    return CommandResult(kind="config_changed", text="Soul updated.")


# ── Registry ────────────────────────────────────────────────

_register(
    CommandSpec(
        name="help",
        summary="List available commands",
        usage="/help",
    ),
    _cmd_help,
)
_register(
    CommandSpec(
        name="status",
        summary="Backend version, provider, model, and running tasks",
        usage="/status",
    ),
    _cmd_status,
)
_register(
    CommandSpec(
        name="new",
        summary="Start a new chat",
        usage="/new [title]",
        args=[CommandArg(name="title", description="Optional title for the chat")],
    ),
    _cmd_new,
)
_register(
    CommandSpec(
        name="sessions",
        summary="List chat sessions",
        usage="/sessions",
    ),
    _cmd_sessions,
)
_register(
    CommandSpec(
        name="switch",
        summary="Switch to another chat",
        usage="/switch <number|title>",
        args=[
            CommandArg(name="target", required=True, description="Session number or title prefix")
        ],
    ),
    _cmd_switch,
)
_register(
    CommandSpec(
        name="rename",
        summary="Rename the current chat",
        usage="/rename <title>",
        args=[CommandArg(name="title", required=True, description="New title")],
        needs_session=True,
    ),
    _cmd_rename,
)
_register(
    CommandSpec(
        name="delete",
        summary="Delete the current chat",
        usage="/delete",
        needs_session=True,
    ),
    _cmd_delete,
)
_register(
    CommandSpec(
        name="model",
        summary="Show or set the orchestrator model",
        usage="/model [name]",
        args=[CommandArg(name="name", description="Model name (omit to show current)")],
    ),
    _cmd_model,
)
_register(
    CommandSpec(
        name="provider",
        summary="Show or set the orchestrator provider",
        usage="/provider [name]",
        args=[CommandArg(name="name", description="Provider name (omit to list)")],
    ),
    _cmd_provider,
)
_register(
    CommandSpec(
        name="operators",
        summary="List enabled operators",
        usage="/operators",
    ),
    _cmd_operators,
)
_register(
    CommandSpec(
        name="memory",
        summary="List, add, or remove memories",
        usage="/memory [list|add <text>|rm <id>]",
        args=[
            CommandArg(
                name="subcommand", description="list (default), add <text>, or rm <id-prefix>"
            )
        ],
    ),
    _cmd_memory,
)
_register(
    CommandSpec(
        name="tasks",
        summary="List running tasks",
        usage="/tasks",
    ),
    _cmd_tasks,
)
_register(
    CommandSpec(
        name="stop",
        summary="Stop the running task in this chat",
        usage="/stop",
        needs_session=True,
    ),
    _cmd_stop,
)
_register(
    CommandSpec(
        name="soul",
        summary="Show or set Vigilus's persona",
        usage="/soul [text]",
        args=[CommandArg(name="text", description="Persona text (omit to show current)")],
    ),
    _cmd_soul,
)

# Client-executed commands — declared here so both clients build identical
# autocomplete menus, but handled inside each client.
_register(
    CommandSpec(
        name="login",
        summary="Add or update an LLM provider (guided)",
        usage="/login",
        execution="client",
    )
)
_register(
    CommandSpec(
        name="clear",
        summary="Clear the visible transcript (history is kept)",
        usage="/clear",
        execution="client",
    )
)
_register(
    CommandSpec(
        name="logout",
        summary="Sign out",
        usage="/logout",
        execution="client",
    )
)
_register(
    CommandSpec(
        name="quit",
        summary="Exit the TUI",
        usage="/quit",
        execution="client",
    )
)


# ── Dispatch ────────────────────────────────────────────────


async def execute_command(
    name: str,
    args: str,
    session_id: str | None,
    db: AsyncSession,
) -> CommandResult:
    """Look up and run a server-side command. Errors come back as results."""
    name = name.lstrip("/").lower()
    cmd = _REGISTRY.get(name)
    if cmd is None:
        return _error(f"Unknown command `/{name}` — try `/help`.")
    if cmd.spec.execution == "client" or cmd.handler is None:
        return _error(f"`/{name}` is handled by your client, not the server.")

    session: Session | None = None
    if session_id:
        session = await db.get(Session, session_id)
    if cmd.spec.needs_session and session is None:
        return _error(f"`/{name}` needs an active chat session.")

    return await cmd.handler(args, session, db)
