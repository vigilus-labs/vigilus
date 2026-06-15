"""Outbound text chunking.

Platform message limits differ (Telegram 4096, Discord 2000). This splits a
response into pieces that fit, preferring to break at line boundaries so the
output stays readable.
"""

from __future__ import annotations


def chunk_text(text: str, limit: int) -> list[str]:
    """Split *text* into chunks of at most *limit* characters.

    Prefers line boundaries. A single over-long line is hard-split at the
    limit. Empty/None input returns an empty list so callers can ``for`` over
    the result without emitting a blank message.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:           # a single over-long line
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        chunks.append(cur)
    return chunks
