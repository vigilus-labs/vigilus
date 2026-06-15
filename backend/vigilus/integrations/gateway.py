"""Gateway manager — lifecycle for all channel adapters.

A single in-process gateway runs inside the FastAPI process (started from the
lifespan next to the scheduler), sharing the DB, event bus, and orchestrator.
``get_gateway()`` returns the process-wide singleton.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from vigilus.config import get_settings
from vigilus.core.crypto import decrypt
from vigilus.db.base import get_session_factory
from vigilus.db.models import ChannelConfig
from vigilus.integrations.base import ChannelAdapter, InboundMessage
from vigilus.integrations.router import handle_inbound

logger = structlog.get_logger(__name__)


class GatewayManager:
    """Owns all running channel adapters and their per-platform config flags."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        # platform -> {respond_in_groups: bool}, populated on start/reload
        self._flags: dict[str, dict] = {}

    async def start(self) -> None:
        if not get_settings().channels_enabled:
            logger.info("gateway.disabled")
            return

        configs = await self._load_configs()
        # Also honour env-fallback tokens when no DB config exists for a platform.
        tokens = self._resolve_tokens(configs)
        for platform, (token, cfg) in tokens.items():
            try:
                await self._start_one(platform, token)
                if cfg is not None:
                    self._flags[platform] = {
                        "respond_in_groups": bool(cfg.respond_in_groups),
                    }
                else:
                    self._flags[platform] = {"respond_in_groups": False}
            except Exception as e:  # noqa: BLE001
                logger.error("gateway.adapter_start_failed", platform=platform, error=str(e))
        logger.info("gateway.started", adapters=list(self._adapters))

    async def _load_configs(self) -> list[ChannelConfig]:
        factory = get_session_factory()
        async with factory() as db:
            return (
                await db.execute(
                    select(ChannelConfig).where(ChannelConfig.enabled == True)  # noqa: E712
                )
            ).scalars().all()

    def _resolve_tokens(
        self, configs: list[ChannelConfig]
    ) -> dict[str, tuple[str, ChannelConfig | None]]:
        """Build {platform: (decrypted_token, config)} from DB configs + env fallback."""
        settings = get_settings()
        out: dict[str, tuple[str, ChannelConfig | None]] = {}
        for cfg in configs:
            try:
                out[cfg.platform] = (decrypt(cfg.bot_token_enc), cfg)
            except Exception as e:  # noqa: BLE001
                logger.error("gateway.decrypt_failed", platform=cfg.platform, error=str(e))
        # Env fallback only fills in platforms the DB didn't configure.
        env = {
            "telegram": settings.telegram_bot_token,
            "discord": settings.discord_bot_token,
        }
        for platform, token in env.items():
            if token and platform not in out:
                out[platform] = (token, None)
        return out

    async def _start_one(self, platform: str, token: str) -> None:
        handler = self._handler_for(platform)
        if platform == "telegram":
            from vigilus.integrations.telegram import TelegramAdapter

            adapter: ChannelAdapter = TelegramAdapter(token, handler)
        elif platform == "discord":
            from vigilus.integrations.discord import DiscordAdapter

            adapter = DiscordAdapter(token, handler)
        else:
            logger.warning("gateway.unknown_platform", platform=platform)
            return
        self._adapters[platform] = adapter
        adapter.set_jit_resolver(self.resolve_jit)
        await adapter.start()

    def _handler_for(self, platform: str):
        async def _on(inbound: InboundMessage) -> None:
            adapter = self._adapters.get(platform)
            if adapter:
                await handle_inbound(inbound, adapter)

        return _on

    async def send(self, platform: str, chat_id: str, text: str) -> None:
        """Outbound send to a specific chat — used by scheduled deliveries."""
        adapter = self._adapters.get(platform)
        if adapter:
            await adapter.send(chat_id, text)

    async def resolve_jit(self, request_id: str, approved: bool, approver: str) -> None:
        """Resolve a JIT request from an in-channel Approve/Deny button.

        Reuses the existing WardenService so the resolution and token issuance
        are identical to the web UI path; the paused delegation picks it up."""
        from vigilus.core.rbac import WardenService

        factory = get_session_factory()
        async with factory() as db:
            warden = WardenService()
            if approved:
                await warden.approve_request(db, request_id, approver=approver)
            else:
                await warden.deny_request(db, request_id, approver=approver)
        logger.info("gateway.jit_resolved", request_id=request_id, approved=approved, approver=approver)

    def responds_in_groups(self, platform: str) -> bool:
        """Whether a platform is configured to answer in groups without a mention."""
        return self._flags.get(platform, {}).get("respond_in_groups", False)

    def is_running(self, platform: str) -> bool:
        return platform in self._adapters

    async def reload(self) -> None:
        """Stop all adapters and restart from current DB config."""
        await self.shutdown()
        await self.start()

    async def shutdown(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception:  # noqa: BLE001
                pass
        self._adapters.clear()
        self._flags.clear()


_gateway: GatewayManager | None = None


def get_gateway() -> GatewayManager:
    global _gateway
    if _gateway is None:
        _gateway = GatewayManager()
    return _gateway
