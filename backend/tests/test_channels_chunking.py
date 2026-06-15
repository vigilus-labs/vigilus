"""Tests for outbound text chunking."""

from __future__ import annotations

from vigilus.integrations.chunking import chunk_text


class TestChunkText:
    def test_empty_returns_nothing(self):
        assert chunk_text("", 100) == []
        assert chunk_text(None, 100) == []  # type: ignore[arg-type]

    def test_short_text_single_chunk(self):
        assert chunk_text("hello", 100) == ["hello"]

    def test_exactly_at_limit(self):
        assert chunk_text("abcd", 4) == ["abcd"]

    def test_splits_at_line_boundaries(self):
        text = "line one\nline two\nline three"
        # limit forces a split after the first line
        chunks = chunk_text(text, 14)
        assert all(len(c) <= 14 for c in chunks)
        assert "".join(chunks) == text

    def test_single_overlong_line_is_hard_split(self):
        text = "a" * 250
        chunks = chunk_text(text, 100)
        assert chunks == ["a" * 100, "a" * 100, "a" * 50]
        assert "".join(chunks) == text

    def telegram_limit_is_4096(self):
        text = "x" * 5000
        chunks = chunk_text(text, 4096)
        assert all(len(c) <= 4096 for c in chunks)
        assert "".join(chunks) == text

    def discord_limit_is_2000(self):
        text = "y" * 3000
        chunks = chunk_text(text, 2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert "".join(chunks) == text

    def test_prefers_breaking_between_lines(self):
        # Two 10-char lines; limit 20 holds both; limit 15 splits between them.
        text = "first line\nsecond line"  # 10 + 1 + 11 = 22 chars
        assert chunk_text(text, 25) == [text]
        chunks = chunk_text(text, 15)
        # The split lands at the newline, not mid-line.
        assert "first line\n" in chunks[0]
        assert chunks[-1] == "second line"
        assert "".join(chunks) == text
