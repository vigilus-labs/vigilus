"""Incremental control-block filtering for streamed orchestrator text.

Vigilus talks to the user in prose and to *itself* in JSON control blocks —
``{"delegate": …}``, ``{"search": …}``, ``{"remember": …}`` — either inline or
inside a ```json fence. The non-streaming path strips those blocks after the
full reply has arrived (see :mod:`vigilus.core.delegation`,
:mod:`vigilus.core.research`, :mod:`vigilus.core.memory`).

When the reply is streamed token-by-token that is no longer possible: by the
time we know a block was a control block, we would already have shown its
opening brace to the user. :class:`SafeTextStreamer` closes that gap. It buffers
the raw stream and releases only the text it is *certain* is prose — everything
up to the first unterminated ``{`` or code fence is safe, anything after it is
withheld until the block closes and can be classified.

Nothing here is a security boundary; it is a display filter. The authoritative,
fully-stripped text is still computed the old way once the reply completes.
"""

from __future__ import annotations

# A trailing run of backticks this long could still grow into a ``` fence, so
# it is held back until the next chunk resolves it.
_MAX_PARTIAL_FENCE = 2


def strip_control_blocks(text: str) -> str:
    """Remove every complete control block from *text*, leaving the prose."""
    from vigilus.core.delegation import strip_delegation
    from vigilus.core.memory import parse_remember_blocks
    from vigilus.core.research import parse_research_blocks

    text, _ = parse_remember_blocks(text)
    text, _ = parse_research_blocks(text)
    return strip_delegation(text)


def find_open_block(text: str) -> int | None:
    """Index where an unterminated JSON object or code fence starts, if any.

    Complete blocks are skipped over — they are the strippers' problem, not
    ours. Only a block that is still being written blocks the stream.
    """
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("```", i):
            end = text.find("```", i + 3)
            if end == -1:
                return i
            i = end + 3
            continue
        if text[i] == "{":
            depth = 0
            close = -1
            for j in range(i, n):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        close = j
                        break
            if close == -1:
                return i
            i = close + 1
            continue
        i += 1
    return None


class SafeTextStreamer:
    """Turns a raw LLM text stream into user-safe incremental chunks.

    Feed it provider deltas; it returns the portion that is safe to show right
    now (possibly ``""``). Text is never retracted once returned, so when
    stripping a newly-closed control block would invalidate what was already
    released, the streamer simply goes quiet and lets the caller's final,
    authoritative message correct the display.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._released = ""

    @property
    def buffer(self) -> str:
        """Everything the provider has sent so far, unfiltered."""
        return self._buffer

    @property
    def released(self) -> str:
        """Everything handed to the caller so far."""
        return self._released

    def feed(self, delta: str) -> str:
        """Add a provider delta and return the newly safe text."""
        if not delta:
            return ""
        self._buffer += delta

        visible = self._visible_prefix()
        if not visible.startswith(self._released):
            # A closing block rewrote text we have already shown. We cannot
            # take it back, so stop emitting for the rest of this message.
            return ""

        chunk = visible[len(self._released) :]
        self._released = visible
        return chunk

    def _visible_prefix(self) -> str:
        """The longest prefix of the buffer that is certainly user-facing prose."""
        open_at = find_open_block(self._buffer)
        if open_at is not None:
            head = self._buffer[:open_at]
        else:
            head = self._buffer
            trailing = 0
            while trailing < len(head) and head[len(head) - 1 - trailing] == "`":
                trailing += 1
            # A run of three or more backticks is a finished fence delimiter and
            # must stay put — cutting it off would leave the block it closes
            # looking unterminated, and the strippers would only half-remove it.
            if 0 < trailing <= _MAX_PARTIAL_FENCE:
                head = head[: len(head) - trailing]
        return strip_control_blocks(head)
