import asyncio
import hashlib
import os
import shutil
import structlog
from typing import Any, Awaitable, Callable, Dict, Optional

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from vigilus.db.base import get_session_factory
from vigilus.db.models import McpServer, McpServerStatus, Tool, ToolImplementationType, PermissionLevel
from vigilus.config import get_settings

logger = structlog.get_logger(__name__)

CLONE_TIMEOUT_SECONDS = 300
INSTALL_TIMEOUT_SECONDS = 900
# Marker written inside a cloned repo after a successful install. Stores a
# hash of the install command so editing the command triggers a re-install,
# and a failed install (no marker) is retried on the next start instead of
# being silently skipped because the clone already exists.
INSTALL_MARKER = ".vigilus-install-ok"


def mcp_repo_path(server_id: str) -> str:
    """Filesystem location of the managed clone for a GitHub-based server."""
    return os.path.join(get_settings().data_dir, "mcp_repos", server_id)


async def _run_logged(
    description: str,
    *,
    timeout: float,
    argv: Optional[list[str]] = None,
    shell_cmd: Optional[str] = None,
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> str:
    """Run a setup step, capturing combined stdout/stderr.

    Raises RuntimeError with the tail of the output on failure or timeout so
    the real cause (npm error, missing binary, …) ends up in `last_error`
    instead of just the command that failed.
    """
    kwargs: dict[str, Any] = dict(
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,  # never hang on an interactive prompt
    )
    if shell_cmd is not None:
        proc = await asyncio.create_subprocess_shell(shell_cmd, **kwargs)
    else:
        proc = await asyncio.create_subprocess_exec(*argv, **kwargs)

    try:
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{description} timed out after {int(timeout)}s")

    output = out_bytes.decode(errors="replace")
    if proc.returncode != 0:
        tail = output[-1500:].strip()
        detail = f":\n{tail}" if tail else ""
        raise RuntimeError(f"{description} failed (exit {proc.returncode}){detail}")
    return output


class McpConnection:
    def __init__(self, server_id: str, command: str, args: list[str], env: dict[str, str], github_url: str = None, install_command: str = None, working_dir: str = None):
        self.server_id = server_id
        self.command = command
        self.args = args
        self.env = env
        self.github_url = github_url
        self.install_command = install_command
        self.working_dir = working_dir
        self.session: Optional[ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._ready = asyncio.Event()  # set once the session is initialized
        self._clean_stop = False  # True when stop() was invoked intentionally
        # Called from _run()'s finally block so the manager can reconcile DB
        # status and drop the in-memory connection on EVERY exit path.
        self.on_exit: Optional[Callable[[str, bool, Optional[str]], Awaitable[None]]] = None

    def _install_marker_path(self, repo_path: str) -> str:
        return os.path.join(repo_path, INSTALL_MARKER)

    def _install_hash(self) -> str:
        return hashlib.sha256((self.install_command or "").encode()).hexdigest()

    def _install_up_to_date(self, repo_path: str) -> bool:
        try:
            with open(self._install_marker_path(repo_path)) as f:
                return f.read().strip() == self._install_hash()
        except OSError:
            return False

    async def _prepare_env(self) -> Optional[str]:
        if not self.github_url:
            return self.working_dir

        repo_path = mcp_repo_path(self.server_id)
        os.makedirs(os.path.dirname(repo_path), exist_ok=True)

        if not os.path.exists(repo_path):
            # Re-validate at the point of use: a row may have been created
            # before this check existed or via a path that skips the schema
            # (e.g. JSON import). Raises ValueError on an unsafe URL.
            from vigilus.schemas.mcp import validate_github_url

            clone_url = validate_github_url(self.github_url)
            logger.info("mcp.clone_repo", url=clone_url, dest=repo_path)
            # "--" stops git parsing later args as options; GIT_ALLOW_PROTOCOL
            # restricts transports so even a malformed URL can't reach git's
            # command-executing helpers (ext::, fd::, …).
            clone_env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:http:git:ssh"}
            try:
                await _run_logged(
                    f"git clone {clone_url}",
                    argv=["git", "clone", "--", clone_url, repo_path],
                    env=clone_env,
                    timeout=CLONE_TIMEOUT_SECONDS,
                )
            except BaseException:
                # A partial clone would make every future start skip the
                # clone step and fail confusingly further down.
                shutil.rmtree(repo_path, ignore_errors=True)
                raise

        if self.install_command and not self._install_up_to_date(repo_path):
            logger.info("mcp.run_install", cmd=self.install_command, cwd=repo_path)
            await _run_logged(
                f"Install command `{self.install_command}`",
                shell_cmd=self.install_command,
                cwd=repo_path,
                env=self.env,
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
            with open(self._install_marker_path(repo_path), "w") as f:
                f.write(self._install_hash())

        cwd = repo_path
        if self.working_dir:
            cwd = os.path.join(repo_path, self.working_dir)

        return cwd

    async def _run(self):
        crashed = False
        error_msg: Optional[str] = None
        try:
            cwd = await self._prepare_env()
            params = StdioServerParameters(command=self.command, args=self.args, env=self.env, cwd=cwd)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    self._ready.set()
                    logger.info("mcp.connected", server_id=self.server_id)

                    # Update DB status
                    async with get_session_factory()() as db:
                        srv = await db.get(McpServer, self.server_id)
                        if srv:
                            srv.status = McpServerStatus.running
                            srv.last_error = None
                            await db.commit()

                    # Sync tools
                    await self._sync_tools(session)

                    # Wait until stopped
                    await self._stop_event.wait()

        except asyncio.CancelledError:
            # Task cancelled (backend shutdown / restart). Reconcile unless
            # this was an intentional stop() — stop_server handles that path.
            if not self._clean_stop:
                crashed = True
                error_msg = "MCP server task was cancelled"
            logger.warning("mcp.connection_cancelled", server_id=self.server_id)
            raise
        except Exception as e:
            # anyio task groups (inside stdio_client) wrap the real failure in
            # an ExceptionGroup whose str() is just "unhandled errors in a
            # TaskGroup" — unwrap to the leaf so last_error is actionable.
            root: BaseException = e
            while isinstance(root, BaseExceptionGroup) and root.exceptions:
                root = root.exceptions[0]
            if not self._clean_stop:
                crashed = True
                error_msg = f"{type(root).__name__}: {root}"
            logger.exception("mcp.connection_failed", server_id=self.server_id, error=str(root))
        finally:
            self.session = None
            self._task = None
            # Always let the manager reconcile DB status + drop the in-memory
            # connection so the UI never shows "running" for a dead subprocess.
            if self.on_exit is not None:
                try:
                    await self.on_exit(self.server_id, crashed, error_msg)
                except Exception:  # noqa: BLE001
                    logger.exception("mcp.on_exit_failed", server_id=self.server_id)

    async def _sync_tools(self, session: ClientSession):
        tools_res = await session.list_tools()

        async with get_session_factory()() as db:
            from sqlalchemy import select
            from vigilus.db.models import Operator, OperatorTool

            created_tool_ids = []

            for mcp_tool in tools_res.tools:
                query = select(Tool).where(Tool.mcp_server_id == self.server_id, Tool.mcp_tool_name == mcp_tool.name)
                res = await db.execute(query)
                existing = res.scalar_one_or_none()

                input_schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else getattr(mcp_tool, "input_schema", {})

                if existing:
                    existing.description = mcp_tool.description or ""
                    existing.input_schema = input_schema
                    created_tool_ids.append(existing.id)
                else:
                    new_tool = Tool(
                        name=f"mcp_{self.server_id[:8]}_{mcp_tool.name}",
                        description=mcp_tool.description or "",
                        input_schema=input_schema,
                        implementation_type=ToolImplementationType.mcp,
                        required_permission=PermissionLevel.read,
                        mcp_server_id=self.server_id,
                        mcp_tool_name=mcp_tool.name,
                    )
                    db.add(new_tool)
                    await db.flush()
                    created_tool_ids.append(new_tool.id)

            # MCP tools are created as available in the DB but must be
            # manually assigned to operators via the UI. (Vigilus the
            # orchestrator is not an operator — it can't run tools.)
            await db.commit()
            logger.info("mcp.tools_synced", server_id=self.server_id, count=len(created_tool_ids))

    def start(self):
        self._stop_event.clear()
        self._ready.clear()
        self._clean_stop = False
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._clean_stop = True
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # external cancellation during shutdown is expected
            
    async def call_tool(self, name: str, arguments: dict) -> Any:
        if not self.session:
            raise RuntimeError("MCP server not connected")
        return await self.session.call_tool(name, arguments=arguments)


class McpManager:
    _instance: Optional['McpManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.connections = {}
            cls._instance._restart_locks: Dict[str, asyncio.Lock] = {}
        return cls._instance

    async def start_server(self, server: McpServer):
        if server.id in self.connections:
            await self.stop_server(server.id)
            
        env = dict(server.env_vars) if server.env_vars else {}
        # Ensure PATH is inherited so things like node/npx can be found
        full_env = dict(os.environ)

        # The installed systemd service runs as a home-less user on a mostly
        # read-only filesystem (ProtectSystem=strict), so npx has nowhere to
        # put its cache and dies before speaking MCP. Give npm a cache inside
        # data_dir, and a usable HOME if the inherited one isn't writable.
        settings = get_settings()
        npm_cache = os.path.join(settings.data_dir, "npm-cache")
        os.makedirs(npm_cache, exist_ok=True)
        full_env.setdefault("npm_config_cache", npm_cache)
        home = full_env.get("HOME")
        if not home or not os.path.isdir(home) or not os.access(home, os.W_OK):
            fallback_home = os.path.join(settings.data_dir, "home")
            os.makedirs(fallback_home, exist_ok=True)
            full_env["HOME"] = fallback_home

        full_env.update(env)
        
        conn = McpConnection(
            server_id=server.id, 
            command=server.command, 
            args=server.args or [], 
            env=full_env,
            github_url=server.github_url,
            install_command=server.install_command,
            working_dir=server.working_dir
        )
        self.connections[server.id] = conn
        conn.on_exit = self._on_connection_exit
        conn.start()

    async def stop_server(self, server_id: str):
        conn = self.connections.get(server_id)
        if conn is not None:
            await conn.stop()  # _run's finally reconciles DB + pops connection
        # Defensive cleanup in case the subprocess already exited on its own.
        self.connections.pop(server_id, None)
        try:
            async with get_session_factory()() as db:
                srv = await db.get(McpServer, server_id)
                if srv and srv.status == McpServerStatus.running:
                    srv.status = McpServerStatus.stopped
                    await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("mcp.stop_reconcile_failed", server_id=server_id)

    def remove_repo(self, server_id: str) -> None:
        """Delete the managed clone so the next start does a fresh
        clone + install. No-op if the server has no cloned repo."""
        path = mcp_repo_path(server_id)
        if os.path.isdir(path):
            logger.info("mcp.remove_repo", server_id=server_id, path=path)
            shutil.rmtree(path, ignore_errors=True)

    async def _on_connection_exit(
        self, server_id: str, crashed: bool, error_msg: Optional[str]
    ) -> None:
        """Reconcile state after a connection's task ends.

        Invoked from :meth:`McpConnection._run`'s ``finally`` block on every
        exit path (clean stop, crash, or cancellation) so the in-memory dict
        and DB status can never disagree about whether a server is running.
        """
        self.connections.pop(server_id, None)
        try:
            async with get_session_factory()() as db:
                srv = await db.get(McpServer, server_id)
                if srv is None:
                    return
                if crashed:
                    srv.status = McpServerStatus.error
                    if error_msg:
                        srv.last_error = error_msg
                elif srv.status == McpServerStatus.running:
                    # Exited without an explicit stop() (e.g. the subprocess
                    # closed its stdio pipe). Treat as stopped, not crashed.
                    srv.status = McpServerStatus.stopped
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("mcp.reconcile_failed", server_id=server_id)

    async def _try_restart(self, server_id: str) -> bool:
        """Best-effort single restart of a missing/dead MCP server.

        Returns True if the connection now exists (a session may still be
        initializing). Uses a per-server lock so concurrent callers don't
        race to spawn the subprocess.
        """
        lock = self._restart_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            if self.connections.get(server_id) is not None:
                return True  # another caller already restarted it
            try:
                async with get_session_factory()() as db:
                    srv = await db.get(McpServer, server_id)
                if srv is None:
                    return False  # server was deleted; nothing to restart
                logger.info("mcp.self_heal_restart", server_id=server_id)
                await self.start_server(srv)
                return self.connections.get(server_id) is not None
            except Exception:  # noqa: BLE001
                logger.exception("mcp.self_heal_failed", server_id=server_id)
                return False

    async def _await_ready(self, conn: McpConnection, timeout: float = 15.0) -> bool:
        """Wait for a freshly started connection to finish initializing."""
        try:
            await asyncio.wait_for(conn._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return conn.session is not None

    async def get_connection(self, server_id: str) -> Optional[McpConnection]:
        return self.connections.get(server_id)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        conn = self.connections.get(server_id)
        if conn is None or conn.session is None:
            # Connection missing or dead. If the server still exists, restart
            # it once so a transient crash doesn't surface as a misleading
            # "is not running" to the operator.
            if await self._try_restart(server_id):
                conn = self.connections.get(server_id)
                if conn is not None and not await self._await_ready(conn):
                    conn = None
        if conn is None or conn.session is None:
            raise RuntimeError(f"MCP server {server_id} is not running")
        return await conn.call_tool(tool_name, arguments)
