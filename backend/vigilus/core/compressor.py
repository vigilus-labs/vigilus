"""Context compression for long conversations.

When the conversation history grows too long for the LLM's context window,
this module compresses older messages into a compact summary while preserving
key context (delegation results, tool calls, decisions).

Hermes-inspired approach: proactive compression before the API call,
preserving recent turns verbatim and summarizing older ones.

Usage::

    compressor = ContextCompressor(provider=provider, model=model)
    compressed, summary = await compressor.compress_if_needed(
        messages=llm_history,
        max_tokens=80000,
    )
    if summary:
        # Inject summary into the system prompt's volatile tier
        system_prompt = rebuild_with_summary(summary)
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from vigilus.providers.base import LLMMessage

logger = structlog.get_logger(__name__)

# Approximate characters per token (rough heuristic for mixed content)
_CHARS_PER_TOKEN = 4

# Minimum messages to keep uncompressed (most recent)
_MIN_RECENT_MESSAGES = 6

# Maximum messages to keep uncompressed
_MAX_RECENT_MESSAGES = 20

# Default context window (tokens) — conservative default
_DEFAULT_MAX_TOKENS = 100_000

# Target compression ratio (keep this fraction of original tokens)
_COMPRESSION_TARGET = 0.3


def estimate_tokens(messages: list[LLMMessage]) -> int:
    """Estimate total token count for a list of messages.

    Uses a simple heuristic: 1 token per ~4 characters.
    This is intentionally conservative (overestimates).
    """
    total_chars = 0
    for msg in messages:
        if isinstance(msg.content, str):
            total_chars += len(msg.content)
        elif isinstance(msg.content, list):
            total_chars += len(json.dumps(msg.content))
        else:
            total_chars += len(str(msg.content))
        # Add overhead for role, metadata, etc.
        total_chars += 20

    return total_chars // _CHARS_PER_TOKEN


def _split_messages(
    messages: list[LLMMessage],
    keep_recent: int = _MIN_RECENT_MESSAGES,
) -> tuple[list[LLMMessage], list[LLMMessage]]:
    """Split messages into older (to compress) and recent (to keep).

    Args:
        messages: Full message history.
        keep_recent: Minimum number of recent messages to preserve.

    Returns:
        Tuple of (older_messages, recent_messages).
    """
    if len(messages) <= keep_recent:
        return [], messages

    split_idx = len(messages) - keep_recent
    return messages[:split_idx], messages[split_idx:]


def _build_compression_prompt(older_messages: list[LLMMessage]) -> str:
    """Build a prompt asking the LLM to summarize older messages."""
    # Serialize the messages to text for the summary prompt
    conversation_text = []
    for msg in older_messages:
        role = msg.role.upper()
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        # Truncate very long messages
        if len(content) > 2000:
            content = content[:2000] + "... [truncated]"
        conversation_text.append(f"[{role}]: {content}")

    conversation_str = "\n\n".join(conversation_text)

    return f"""Summarize the following conversation history into a compact summary that preserves:

1. **Key decisions and conclusions** — What was decided and why
2. **Delegation results** — Which operators were called and what they found/did
3. **Tool results** — Important findings from tool calls (vulnerabilities, alerts, system state)
4. **User preferences** — Any stated preferences or constraints
5. **Outstanding tasks** — Things that still need to be done

Be concise but complete. Focus on actionable information, not conversational filler.
Use bullet points. Omit pleasantries and repetition.

CONVERSATION HISTORY:
---
{conversation_str}
---

COMPACT SUMMARY:"""


class ContextCompressor:
    """Compresses conversation history when it approaches the context window limit.

    Uses a cheap/fast LLM call to summarize older messages into a compact block.
    """

    def __init__(
        self,
        provider: Any,
        model: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        trigger_threshold: float = 0.7,
    ):
        """
        Args:
            provider: LLM provider instance for generating summaries.
            model: Model override (uses provider default if None).
            max_tokens: Maximum context window size in tokens.
            trigger_threshold: Fraction of max_tokens at which to trigger compression.
        """
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.trigger_threshold = trigger_threshold

    async def compress_if_needed(
        self,
        messages: list[LLMMessage],
        *,
        system_tokens: int = 0,
    ) -> tuple[list[LLMMessage], str | None]:
        """Compress messages if they exceed the threshold.

        Args:
            messages: Full conversation history.
            system_tokens: Approximate tokens used by system prompt + tools.

        Returns:
            Tuple of (possibly compressed messages, summary text or None).
            If no compression was needed, returns (messages, None).
            If compressed, returns (recent_messages + summary_message, summary_text).
        """
        current_tokens = estimate_tokens(messages) + system_tokens
        threshold = int(self.max_tokens * self.trigger_threshold)

        if current_tokens < threshold:
            logger.debug(
                "compressor.skip",
                current_tokens=current_tokens,
                threshold=threshold,
            )
            return messages, None

        logger.info(
            "compressor.triggered",
            current_tokens=current_tokens,
            threshold=threshold,
            max_tokens=self.max_tokens,
            message_count=len(messages),
        )

        return await self.compress(messages)

    async def compress(
        self,
        messages: list[LLMMessage],
    ) -> tuple[list[LLMMessage], str]:
        """Compress older messages into a summary.

        Always keeps the most recent messages intact.

        Returns:
            Tuple of (compressed messages, summary text).
        """
        # Determine how many recent messages to keep
        # Keep at least _MIN_RECENT_MESSAGES, at most _MAX_RECENT_MESSAGES
        keep_count = max(_MIN_RECENT_MESSAGES, min(_MAX_RECENT_MESSAGES, len(messages) // 3))
        older, recent = _split_messages(messages, keep_recent=keep_count)

        if not older:
            logger.debug("compressor.no_older_messages")
            return messages, ""

        # Build and call the compression prompt
        summary_prompt = _build_compression_prompt(older)
        summary_text = await self._generate_summary(summary_prompt)

        if not summary_text:
            logger.warning("compressor.empty_summary")
            return messages, ""

        # Build the compressed message list
        summary_msg = LLMMessage(
            role="user",
            content=(
                "[CONTEXT SUMMARY — This is a compressed summary of earlier conversation. "
                "Treat it as background context, not a new request.]\n\n"
                f"{summary_text}"
            ),
        )

        compressed = [summary_msg, *recent]

        original_tokens = estimate_tokens(messages)
        compressed_tokens = estimate_tokens(compressed)
        reduction = (1 - compressed_tokens / max(original_tokens, 1)) * 100

        logger.info(
            "compressor.done",
            original_messages=len(messages),
            compressed_messages=len(compressed),
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction_pct=f"{reduction:.1f}%",
        )

        return compressed, summary_text

    async def _generate_summary(self, prompt: str) -> str:
        """Generate a summary using the configured LLM provider."""
        try:
            # Use a small max_tokens for the summary to keep costs down
            response = await self.provider.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                system="You are a concise summarizer. Produce compact, factual summaries.",
                tools=None,
                temperature=0.0,
                max_tokens=2048,
            )
            return response.content or ""
        except Exception as e:
            logger.error("compressor.summary_failed", error=str(e))
            # Fallback: create a simple summary from message metadata
            return self._fallback_summary(prompt)

    @staticmethod
    def _fallback_summary(prompt: str) -> str:
        """Generate a basic summary without LLM (fallback when provider fails)."""
        # Extract just the conversation text (before the instruction)
        lines = []
        for line in prompt.split("\n"):
            if line.startswith("[") and "]:" in line:
                # Truncate each message to first 100 chars
                truncated = line[:150] + "..." if len(line) > 150 else line
                lines.append(truncated)

        if not lines:
            return ""

        return "Previous conversation summary (auto-generated):\n" + "\n".join(lines[-20:])
