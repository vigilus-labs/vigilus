"""Tests for SSE streaming helpers."""

import asyncio

import pytest

from vigilus.api.sse import SSEEvent, StreamBridge, get_bridge, register_bridge, unregister_bridge


def test_sse_event_encode():
    """SSEEvent encodes to proper SSE wire format."""
    event = SSEEvent(event="thinking", data={"iteration": 1})
    encoded = event.encode()
    assert encoded == 'event: thinking\ndata: {"iteration": 1}\n\n'


def test_sse_event_encode_empty_data():
    """SSEEvent with empty data."""
    event = SSEEvent(event="done", data={})
    encoded = event.encode()
    assert encoded == "event: done\ndata: {}\n\n"


@pytest.mark.asyncio
async def test_stream_bridge_publish_and_consume():
    """Bridge publishes events and consumer reads them via aiter."""
    bridge = StreamBridge()
    bridge.publish("thinking", {"iteration": 1})
    bridge.publish("text_delta", {"text": "Hello"})
    bridge.close()

    events = []
    async for encoded in bridge.aiter():
        events.append(encoded)

    assert len(events) == 2
    assert "thinking" in events[0]
    assert "Hello" in events[1]


@pytest.mark.asyncio
async def test_stream_bridge_close_stops_iteration():
    """Closing bridge without events produces empty stream."""
    bridge = StreamBridge()
    bridge.close()

    events = []
    async for encoded in bridge.aiter():
        events.append(encoded)

    assert len(events) == 0


@pytest.mark.asyncio
async def test_stream_bridge_discards_after_close():
    """Events published after close are discarded."""
    bridge = StreamBridge()
    bridge.publish("thinking", {"iteration": 1})
    bridge.close()
    bridge.publish("text_delta", {"text": "Should be discarded"})

    events = []
    async for encoded in bridge.aiter():
        events.append(encoded)

    assert len(events) == 1
    assert "thinking" in events[0]


def test_bridge_registry():
    """Global bridge registry registers, retrieves, and unregisters."""
    bridge = StreamBridge()
    session_id = "test-session-1"

    register_bridge(session_id, bridge)
    assert get_bridge(session_id) is bridge

    unregister_bridge(session_id)
    assert get_bridge(session_id) is None


def test_bridge_registry_nonexistent():
    """Getting a non-existent bridge returns None."""
    assert get_bridge("nonexistent-session") is None


def test_unregister_nonexistent():
    """Unregistering a non-existent session does not error."""
    unregister_bridge("nonexistent-session")  # Should not raise


@pytest.mark.asyncio
async def test_stream_endpoint_waits_for_late_bridge():
    """A stream opened before the turn registers its bridge still attaches.

    The frontend opens the stream immediately after POSTing its message, so it
    can beat the POST handler's bridge registration. Bailing out on the first
    miss would drop the turn's whole live feed.
    """
    from vigilus.api.chat import stream_session

    session_id = "late-bridge-session"
    bridge = StreamBridge()

    async def _register_late():
        await asyncio.sleep(0.2)
        register_bridge(session_id, bridge)
        bridge.publish("text_delta", {"text": "I'll have the Systems Operator check."})
        bridge.close()

    registrar = asyncio.create_task(_register_late())
    try:
        response = await stream_session(session_id)
        chunks = [chunk async for chunk in response.body_iterator]
    finally:
        await registrar
        unregister_bridge(session_id)

    assert any("Systems Operator" in c for c in chunks)


@pytest.mark.asyncio
async def test_stream_endpoint_gives_up_without_a_turn():
    """With no turn running, the stream ends promptly with a done event."""
    from vigilus.api import chat as chat_api

    original = chat_api._BRIDGE_WAIT_SECONDS
    chat_api._BRIDGE_WAIT_SECONDS = 0.1
    try:
        response = await chat_api.stream_session("no-such-session")
        chunks = [chunk async for chunk in response.body_iterator]
    finally:
        chat_api._BRIDGE_WAIT_SECONDS = original

    assert chunks == ["event: done\ndata: {}\n\n"]
