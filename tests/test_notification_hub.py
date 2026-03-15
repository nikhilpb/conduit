"""Tests for NotificationHub pub/sub."""

from __future__ import annotations

import asyncio

import pytest

from conduit.notification_hub import NotificationEvent
from conduit.notification_hub import NotificationHub


def _make_event(**overrides) -> NotificationEvent:
    defaults = {
        "type": "new_message",
        "session_id": "sess-1",
        "session_title": "Hello",
        "session_kind": "interactive",
        "preview": "Hi there",
    }
    defaults.update(overrides)
    return NotificationEvent(**defaults)


@pytest.mark.anyio
async def test_subscribe_and_broadcast():
    hub = NotificationHub()
    queue = await hub.subscribe()

    event = _make_event()
    await hub.broadcast(event)

    payload = queue.get_nowait()
    assert payload["type"] == "new_message"
    assert payload["session_id"] == "sess-1"
    assert payload["session_title"] == "Hello"
    assert payload["preview"] == "Hi there"


@pytest.mark.anyio
async def test_unsubscribe_stops_delivery():
    hub = NotificationHub()
    queue = await hub.subscribe()
    await hub.unsubscribe(queue)

    await hub.broadcast(_make_event())

    assert queue.empty()


@pytest.mark.anyio
async def test_multiple_subscribers():
    hub = NotificationHub()
    q1 = await hub.subscribe()
    q2 = await hub.subscribe()

    await hub.broadcast(_make_event())

    assert not q1.empty()
    assert not q2.empty()
    assert q1.get_nowait()["session_id"] == "sess-1"
    assert q2.get_nowait()["session_id"] == "sess-1"


@pytest.mark.anyio
async def test_queue_full_drops_event():
    hub = NotificationHub(max_queue_size=1)
    queue = await hub.subscribe()

    await hub.broadcast(_make_event(session_id="a"))
    await hub.broadcast(_make_event(session_id="b"))

    assert queue.get_nowait()["session_id"] == "a"
    assert queue.empty()


@pytest.mark.anyio
async def test_scheduled_event_includes_job_id():
    hub = NotificationHub()
    queue = await hub.subscribe()

    event = _make_event(
        session_kind="scheduled",
        scheduled_job_id="daily-check",
    )
    await hub.broadcast(event)

    payload = queue.get_nowait()
    assert payload["session_kind"] == "scheduled"
    assert payload["scheduled_job_id"] == "daily-check"


@pytest.mark.anyio
async def test_interactive_event_omits_null_job_id():
    hub = NotificationHub()
    queue = await hub.subscribe()

    await hub.broadcast(_make_event())

    payload = queue.get_nowait()
    assert "scheduled_job_id" not in payload


@pytest.mark.anyio
async def test_unsubscribe_idempotent():
    hub = NotificationHub()
    queue = await hub.subscribe()
    await hub.unsubscribe(queue)
    await hub.unsubscribe(queue)  # Should not raise.
