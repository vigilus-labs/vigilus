"""Control-block filtering for streamed orchestrator text.

The orchestrator's raw reply mixes user-facing prose with machine-only JSON
(delegation, research, remember). When that reply is streamed, the filter must
release the prose promptly and never let a control block reach the screen.
"""

from __future__ import annotations

import json

from vigilus.core.stream_text import SafeTextStreamer, find_open_block, strip_control_blocks


def _stream(text: str, *, size: int = 7) -> str:
    """Feed *text* through the streamer in fixed-size slices."""
    streamer = SafeTextStreamer()
    out = []
    for i in range(0, len(text), size):
        out.append(streamer.feed(text[i : i + size]))
    return "".join(out)


def test_plain_prose_streams_through():
    text = "I'll check every server for pending updates and report back."
    assert _stream(text) == text


def test_prose_is_released_before_the_reply_finishes():
    """The whole point: text is visible while the model is still writing."""
    streamer = SafeTextStreamer()
    first = streamer.feed("I'll have the Systems ")
    assert first == "I'll have the Systems "
    assert streamer.feed("Operator check.") == "Operator check."


def test_fenced_delegation_block_never_leaks():
    delegation = json.dumps({"delegate": "Systems Operator", "task": "check updates"})
    text = "I'll have the Systems Operator check every server.\n\n" f"```json\n{delegation}\n```"
    released = _stream(text)

    assert released.startswith("I'll have the Systems Operator check every server.")
    assert "delegate" not in released
    assert "```" not in released
    assert "{" not in released


def test_inline_delegation_block_never_leaks():
    text = 'Delegating now. {"delegate": "Systems Operator", "task": "check updates"}'
    released = _stream(text)

    assert released.startswith("Delegating now.")
    assert "delegate" not in released


def test_remember_and_research_blocks_never_leak():
    text = (
        'Noting that for later. {"remember": "web01 runs nginx", "category": "infra"}\n'
        'Looking it up. {"search": "nginx 1.27 http3"}'
    )
    released = _stream(text)

    assert "remember" not in released
    assert "search" not in released
    assert "Noting that for later." in released


def test_partial_fence_is_held_until_resolved():
    """A trailing ``` must not flash on screen before its block arrives."""
    streamer = SafeTextStreamer()
    assert streamer.feed("Here goes.\n\n`") == "Here goes.\n\n"
    assert streamer.feed("``json\n") == ""
    assert streamer.feed('{"delegate": "X", "task": "y"}\n```') == ""


def test_backticks_in_prose_are_eventually_released():
    text = "Run `systemctl status nginx` on web01."
    assert _stream(text) == text


def test_prose_after_a_closed_control_block_still_streams():
    text = 'Plan set. {"remember": "web01 runs nginx"} Starting the check now.'
    released = _stream(text)

    assert "Plan set." in released
    assert "Starting the check now." in released
    assert "remember" not in released


def test_released_text_is_a_prefix_of_the_final_stripped_text():
    """Streamed output must never contradict the authoritative final message."""
    delegation = json.dumps({"delegate": "Systems Operator", "task": "check updates"})
    text = f"Checking every server now.\n\n```json\n{delegation}\n```"

    streamer = SafeTextStreamer()
    released = "".join(streamer.feed(text[i : i + 5]) for i in range(0, len(text), 5))
    final = strip_control_blocks(text).strip()

    assert final.startswith(released.strip())


def test_holds_at_every_chunk_boundary():
    """Chunk boundaries land anywhere; none of them may open a leak."""
    delegation = json.dumps({"delegate": "Systems Operator", "task": "check updates"})
    text = (
        "I'll have the Systems Operator SSH into every server.\n\n" f"```json\n{delegation}\n```\n"
    )
    final = strip_control_blocks(text).strip()

    for size in range(1, 14):
        released = _stream(text, size=size)
        assert "delegate" not in released, f"leaked at chunk size {size}"
        assert "```" not in released, f"leaked a fence at chunk size {size}"
        assert final.startswith(released.strip()), f"diverged at chunk size {size}"


def test_find_open_block_skips_closed_blocks():
    assert find_open_block("plain prose") is None
    assert find_open_block('done {"a": 1} more') is None
    assert find_open_block('half {"a": ') == 5
    assert find_open_block("```json\n{}\n```") is None
    assert find_open_block("text ```json\n{") == 5
    assert find_open_block('nested {"a": {"b": 1}} tail') is None
