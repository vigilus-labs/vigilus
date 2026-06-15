"""Base classes for third-party channel adapters.

A channel adapter bridges a messaging platform (Telegram, Discord, …) and the
Vigilus gateway. Each adapter owns its connection to the platform, converts
inbound platform events into a uniform :class:`InboundMessage`, and sends
outbound replies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """A normalized inbound message from any channel."""

    platform: str          # "telegram" | "discord"
    chat_id: str           # conversation id (telegram chat id / discord channel id)
    user_id: str           # external sender id (telegram id / discord snowflake)
    user_name: str
    text: str
    is_group: bool = False
    mentioned: bool = False
    attachments: list[dict] = field(default_factory=list)
    raw_channel: Any = None  # platform handle for replies (discord channel obj)


OnMessage = Callable[[InboundMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """Abstract base for a channel adapter."""

    platform: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, chat_id: str, text: str) -> None: ...

    async def send_typing(self, chat_id: str) -> None:
        """Optional 'typing…' indicator; no-op by default."""
        return None

    # ── Editable status messages (live progress) ────────────
    # Adapters that support editing return a handle from send_status; that
    # handle is passed back to edit_status/delete_status. Platforms that
    # can't edit return None from send_status and the router falls back to
    # sending the final reply only.

    async def send_status(self, chat_id: str, text: str) -> str | None:
        """Send an editable status message; return a handle or None if unsupported."""
        return None

    async def edit_status(self, chat_id: str, handle: str | None, text: str) -> None:
        """Edit a previously-sent status message (best-effort)."""
        return None

    async def delete_status(self, chat_id: str, handle: str | None) -> None:
        """Delete a status message when the turn completes (best-effort)."""
        return None

    # ── JIT approval prompts (Phase 4) ───────────────────────

    async def send_jit_prompt(self, chat_id: str, text: str, request_id: str) -> None:
        """Send a JIT approval prompt with inline Approve/Deny controls.

        Default: send the text only (no buttons). The user can still approve
        from the Vigilus web UI; adapters that support buttons override this.
        """
        await self.send(chat_id, text)

    def set_jit_resolver(self, resolver) -> None:
        """Store an async ``resolver(request_id, approved: bool, approver: str) -> None``.

        Called when a user taps Approve/Deny on a JIT prompt in-channel.
        """
        self._jit_resolver = resolver
