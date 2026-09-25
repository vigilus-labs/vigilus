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

# Default context window (tokens) when the model is unknown.
_DEFAULT_MAX_TOKENS = 100_000

# Local OpenAI-compatible servers (Ollama, LM Studio) are often 8k.
_LOCAL_MAX_TOKENS = 8_192

# Known model families. Matched as a substring of the model id so OpenRouter
# ids like "anthropic/claude-sonnet-4" still resolve.
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-", 200_000),
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4", 128_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("gemini-2.5", 1_000_000),
    ("gemini-2", 1_000_000),
    ("gemini-1.5", 1_000_000),
)

# Older tool results replaced once a run has this many newer ones.
_KEEP_RECENT_TOOL_RESULTS = 4
_TOOL_RESULT_STUB = "[earlier tool output omitted to fit the context window]"

# Target compression ratio (keep this fraction of original tokens)
_COMPRESSION_TARGET = 0.3


def resolve_context_window(provider_row: Any, model: str | None = None) -> int:
    """Tokens available for this provider and model.

    An explicit ``context_window`` on the provider wins. Otherwise a known
    model id is used, then 8k for local OpenAI-compatible servers, then the
    conservative cloud default.
    """
    explicit = getattr(provider_row, "context_window", None)
    if explicit:
        return int(explicit)

    name = (model or getattr(provider_row, "default_model", None) or "").lower()
    for prefix, window in _MODEL_CONTEXT_WINDOWS:
        if prefix in name:
            return window

    provider_type = getattr(provider_row, "type", None)
    type_value = (
        provider_type.value if hasattr(provider_type, "value") else str(provider_type or "")
    )
    if type_value == "openai_compat":
        return _LOCAL_MAX_TOKENS
    return _DEFAULT_MAX_TOKENS


def elide_old_tool_results(
    messages: list[LLMMessage],
    *,
    keep_recent: int = _KEEP_RECENT_TOOL_RESULTS,
) -> list[LLMMessage]:
    """Replace old tool-result bodies with a stub.

    ``tool_use_id`` is kept so the transcript stays valid for providers that
    require a result for every tool call. Returns the same list when nothing
    needs to change.
    """
    tool_indexes = [i for i, msg in enumerate(messages) if msg.role == "tool"]
    stale = tool_indexes[:-keep_recent] if keep_recent > 0 else tool_indexes
    if not stale:
        return messages
    stale_set = set(stale)
    updated: list[LLMMessage] = []
    changed = False
    for index, msg in enumerate(messages):
        if index in stale_set and msg.content != _TOOL_RESULT_STUB:
            updated.append(
                LLMMessage(
                    role=msg.role,
                    content=_TOOL_RESULT_STUB,
                    tool_use_id=msg.tool_use_id,
                    name=msg.name,
                    tool_calls=msg.tool_calls,
                    raw=msg.raw,
                )
            )
            changed = True
        else:
            updated.append(msg)
    return updated if changed else messages


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
    # Don't start the kept tail on a tool result. Walk back to the assistant
    # message that requested it so the pair stays together.
    while split_idx > 0 and messages[split_idx].role == "tool":
        split_idx -= 1
    if split_idx <= 0:
        return [], messages
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
        current_tokens = await self._measured_tokens(messages, system_tokens)
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

    async def _measured_tokens(self, messages: list[LLMMessage], system_tokens: int) -> int:
        """Heuristic count, confirmed with the provider when we are near the cap."""
        estimated = estimate_tokens(messages) + system_tokens
        threshold = int(self.max_tokens * self.trigger_threshold)
        if estimated < threshold:
            return estimated
        counter = getattr(self.provider, "count_tokens", None)
        if counter is None:
            return estimated
        try:
            exact = await counter(messages)
        except Exception as e:  # noqa: BLE001 — counting must never block a turn
            logger.warning("compressor.count_tokens_failed", error=str(e))
            return estimated
        if not isinstance(exact, int):
            return estimated
        return exact + system_tokens

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
