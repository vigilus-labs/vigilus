"""Discord adapter — gateway WebSocket via discord.py.

A new optional dependency (``pip install -e '.[channels]'``). Requires the
**Message Content Intent** to be enabled in the Discord Developer Portal, or
the bot receives empty ``content``.

DMs work without a guild; in guilds the bot only answers when @mentioned
(unless the platform config opts into ``respond_in_groups``).
"""

from __future__ import annotations

import asyncio

import structlog

from vigilus.integrations.base import ChannelAdapter, InboundMessage, OnMessage
from vigilus.integrations.chunking import chunk_text

logger = structlog.get_logger(__name__)


class DiscordAdapter(ChannelAdapter):
    platform = "discord"

    def __init__(self, token: str, on_message: OnMessage):
        import discord

        self._token = token
        self._on_message = on_message
        self._task: asyncio.Task | None = None
        self._jit_resolver = None

        intents = discord.Intents.default()
        intents.message_content = True  # PRIVILEGED: enable in Developer Portal
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            # Register native slash commands once the gateway is connected.
            try:
                await self._register_commands()
            except Exception as e:  # noqa: BLE001
                logger.warning("discord.register_commands_failed", error=str(e))

        @self._client.event
        async def on_message(message: "discord.Message"):
            if message.author.bot:
                return
            attachments = [
                {
                    "url": a.url,
                    "filename": a.filename,
                    "mime_type": a.content_type,
                    "size": a.size,
                }
                for a in message.attachments
            ]
            await self._on_message(
                InboundMessage(
                    platform="discord",
                    chat_id=str(message.channel.id),
                    user_id=str(message.author.id),
                    user_name=message.author.name,
                    text=message.clean_content,
                    is_group=message.guild is not None,
                    mentioned=self._client.user in message.mentions,
                    raw_channel=message.channel,
                    attachments=attachments,
                )
            )

    async def _get_channel(self, chat_id: str):
        channel = self._client.get_channel(int(chat_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(chat_id))
        return channel

    async def start(self) -> None:
        self._task = asyncio.create_task(self._client.start(self._token))
        logger.info("discord.starting")

    async def stop(self) -> None:
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001
            pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def send(self, chat_id: str, text: str) -> None:
        channel = await self._get_channel(chat_id)
        for chunk in chunk_text(text, 2000):
            await channel.send(chunk)

    async def send_typing(self, chat_id: str) -> None:
        try:
            channel = await self._get_channel(chat_id)
            await channel.trigger_typing()
        except Exception:  # noqa: BLE001
            pass

    async def send_status(self, chat_id: str, text: str) -> str | None:
        try:
            channel = await self._get_channel(chat_id)
            msg = await channel.send(text)
            return str(msg.id)
        except Exception:  # noqa: BLE001
            return None

    async def edit_status(self, chat_id: str, handle: str | None, text: str) -> None:
        if not handle:
            return
        try:
            channel = await self._get_channel(chat_id)
            msg = await channel.fetch_message(int(handle))
            await msg.edit(content=text)
        except Exception:  # noqa: BLE001
            pass

    async def delete_status(self, chat_id: str, handle: str | None) -> None:
        if not handle:
            return
        try:
            channel = await self._get_channel(chat_id)
            msg = await channel.fetch_message(int(handle))
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass

    async def send_jit_prompt(self, chat_id: str, text: str, request_id: str) -> None:
        """Send a JIT prompt with Approve/Deny buttons."""
        import discord
        from discord import ui

        adapter = self

        class _JitView(ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=None)

            @ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
            async def _approve(self, interaction: "discord.Interaction", button: ui.Button):
                await adapter._resolve_jit_button(interaction, request_id, True)

            @ui.button(label="⛔ Deny", style=discord.ButtonStyle.danger)
            async def _deny(self, interaction: "discord.Interaction", button: ui.Button):
                await adapter._resolve_jit_button(interaction, request_id, False)

        try:
            channel = await self._get_channel(chat_id)
            await channel.send(text, view=_JitView())
        except Exception as e:  # noqa: BLE001
            logger.warning("discord.jit_prompt_failed", error=str(e))
            await self.send(chat_id, text)

    async def _resolve_jit_button(self, interaction, request_id: str, approved: bool) -> None:
        approver = f"discord:{interaction.user.name}"
        try:
            if self._jit_resolver:
                await self._jit_resolver(request_id, approved, approver)
            verdict = "✅ Approved" if approved else "⛔ Denied"
        except Exception as e:  # noqa: BLE001
            verdict = f"⚠️ {e}"
        try:
            await interaction.response.edit_message(content=verdict, view=None)
        except Exception:  # noqa: BLE001
            try:
                await interaction.followup.edit_message(interaction.message.id, content=verdict)
            except Exception:  # noqa: BLE001
                pass

    async def _register_commands(self) -> None:
        """Register the shared command registry as Discord slash commands."""
        import discord
        from discord import app_commands

        from vigilus.core.commands import get_command_specs

        tree = app_commands.CommandTree(self._client)
        for spec in get_command_specs():
            if spec.execution == "client":
                continue

            # Capture spec by value in a closure factory.
            def _make_handler(captured_spec):
                async def _callback(interaction: "discord.Interaction") -> None:
                    await interaction.response.defer()
                    inbound = InboundMessage(
                        platform="discord",
                        chat_id=str(interaction.channel_id),
                        user_id=str(interaction.user.id),
                        user_name=interaction.user.name,
                        text=f"/{captured_spec.name}",
                        is_group=interaction.guild is not None,
                        raw_channel=interaction.channel,
                    )
                    await handle_inbound(inbound, self)
                    await interaction.followup.send("✓ done")

                return _callback

            try:
                tree.command(
                    name=spec.name[:32].lower(),
                    description=(spec.summary or spec.usage)[:100],
                )(_make_handler(spec))
            except Exception as e:  # noqa: BLE001
                logger.warning("discord.command_register_failed", name=spec.name, error=str(e))
        await tree.sync()
