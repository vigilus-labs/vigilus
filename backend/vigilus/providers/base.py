"""Abstract LLM provider interface and common data structures."""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

# HTTP status codes that indicate a transient (retryable) upstream failure.
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ProviderError(RuntimeError):
    """Raised when an LLM provider returns an unusable response.

    Some OpenAI-compatible gateways answer a failed upstream request with
    HTTP 200 and an in-body ``{"error": {"code": ...}}`` object (and
    ``choices=None``) rather than raising an exception. Adapters translate
    that into a :class:`ProviderError` so callers get a typed, retryable
    failure instead of a cryptic ``TypeError`` from subscripting ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        transient: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # If the caller didn't say explicitly, infer transience from the code.
        if transient is None:
            transient = status_code in _TRANSIENT_STATUS_CODES if status_code else False
        self.transient = transient


async def retry_transient(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    on_retry: Callable[[Exception, int, float], Any] | None = None,
) -> T:
    """Call ``func``, retrying on transient failures with exponential backoff.

    Retries are triggered by:
      * :class:`ProviderError` with ``transient=True``
      * network-level timeouts / connection errors (``httpx.TimeoutException``,
        ``httpx.TransportError``, ``openai.APITimeoutError``,
        ``openai.APIConnectionError``)
      * SDK status errors mapped to retryable HTTP codes (429 / 5xx / 408).

    Non-transient errors (auth failures, 4xx, malformed requests) propagate
    immediately without consuming retry budget.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient(exc) or attempt >= max_attempts:
                raise
            # Exponential backoff with full jitter: 0.5, 1.0, 2.0, ... capped.
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, delay)
            if on_retry is not None:
                try:
                    on_retry(exc, attempt, delay)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(delay)
    # Defensive — the loop either returns or re-raises above.
    assert last_exc is not None
    raise last_exc


def _is_transient(exc: Exception) -> bool:
    """Return True if ``exc`` represents a retryable upstream failure."""
    if isinstance(exc, ProviderError):
        return exc.transient

    # Vendor SDK / network errors. Imported lazily so a missing optional
    # dependency never breaks provider-agnostic code paths.
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dep
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True

    try:
        import openai
    except ImportError:  # pragma: no cover
        openai = None  # type: ignore[assignment]
    if openai is not None:
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
            return True
        if isinstance(exc, openai.APIStatusError):
            return getattr(exc, "status_code", None) in _TRANSIENT_STATUS_CODES

    return False


@dataclass
class LLMMessage:
    """A single message in a conversation."""

    role: str  # "user" | "assistant" | "tool"
    content: str | list[dict[str, Any]]
    tool_use_id: str | None = None
    name: str | None = None
    tool_calls: list | None = None  # Tool call blocks for assistant messages
    raw: Any = None  # Raw provider response for persistence/reconstruction


@dataclass
class ToolSpec:
    """Describes a tool that the LLM can invoke."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolUse:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """The response from an LLM completion call."""

    content: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


class AgentLLM(ABC):
    """Abstract base class for LLM provider adapters.

    Concrete implementations wrap vendor SDKs (Anthropic, OpenAI, etc.)
    and present a uniform interface to the Vigilus operator engine.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse | AsyncIterator[LLMResponse]:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation history.
            system: Optional system prompt.
            tools: Tools available for the LLM to call.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            stream: If True, return an async iterator of partial responses.

        Returns:
            A single LLMResponse or an async iterator of partial responses.
        """
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """Verify that the provider is reachable and credentials are valid.

        Returns:
            True if the connection succeeds, False otherwise.
        """
        ...
