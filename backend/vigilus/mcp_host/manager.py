import asyncio
import os
import structlog
from typing import Any, Awaitable, Callable, Dict, Optional

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from vigilus.db.base import get_session_factory
from vigilus.db.models import McpServer, McpServerStatus, Tool, ToolImplementationType, PermissionLevel
from vigilus.config import get_settings

logger = structlog.get_logger(__name__)

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

    async def _prepare_env(self) -> Optional[str]:
        if not self.github_url:
            return self.working_dir
            
        settings = get_settings()
        repos_dir = os.path.join(settings.data_dir, "mcp_repos")
        os.makedirs(repos_dir, exist_ok=True)
        repo_path = os.path.join(repos_dir, self.server_id)
        
        if not os.path.exists(repo_path):
            logger.info("mcp.clone_repo", url=self.github_url, dest=repo_path)
            proc = await asyncio.create_subprocess_exec("git", "clone", self.github_url, repo_path)
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"Git clone failed for {self.github_url}")
                
            if self.install_command:
                logger.info("mcp.run_install", cmd=self.install_command, cwd=repo_path)
                proc2 = await asyncio.create_subprocess_shell(self.install_command, cwd=repo_path)
                await proc2.wait()
                if proc2.returncode != 0:
                    raise RuntimeError(f"Install command failed: {self.install_command}")
                    
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
            if not self._clean_stop:
                crashed = True
                error_msg = str(e)
            logger.exception("mcp.connection_failed", server_id=self.server_id, error=str(e))
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
        import os as _os
        full_env = dict(_os.environ)
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
