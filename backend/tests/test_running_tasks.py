"""Tests for cooperative cancellation of live chat turns."""

from __future__ import annotations

import asyncio

import pytest

from vigilus.core.tasks import TaskCancelled, await_cancelled, get_task_registry


@pytest.mark.asyncio
async def test_await_cancelled_stops_blocked_operation():
    started = asyncio.Event()
    operation_cancelled = asyncio.Event()
    cancel_event = asyncio.Event()

    async def blocked_operation():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            operation_cancelled.set()
            raise

    wait = asyncio.create_task(await_cancelled(blocked_operation(), cancel_event))
    await started.wait()
    cancel_event.set()

    with pytest.raises(TaskCancelled):
        await wait
    assert operation_cancelled.is_set()


def test_unregister_does_not_remove_newer_task_for_same_session():
    registry = get_task_registry()
    session_id = "task-registry-replacement-test"
    registry.unregister(session_id)

    older = registry.register(session_id, "Older task")
    newer = registry.register(session_id, "Newer task")
    registry.unregister(session_id, older.id)

    assert registry.get(session_id) is newer
    registry.unregister(session_id, newer.id)


@pytest.mark.asyncio
async def test_cancel_running_task_endpoint_signals_registry(async_client):
    registry = get_task_registry()
    session_id = "task-cancel-api-test"
    registry.unregister(session_id)
    task = registry.register(session_id, "Cancellable task")

    try:
        response = await async_client.post(f"/api/running-tasks/{session_id}/cancel")
        assert response.status_code == 200
        assert task.cancel_event.is_set()
        assert task.cancelling is True
        assert task.current_step == "Cancelling…"
    finally:
        registry.unregister(session_id, task.id)
