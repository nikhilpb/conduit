"""Lightweight async pub/sub hub for session notification events."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationEvent:
    """A session update event broadcast to notification subscribers."""

    type: str
    session_id: str
    session_title: str
    session_kind: str
    preview: str
    scheduled_job_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class NotificationHub:
    """Fan-out pub/sub for notification WebSocket subscribers."""

    def __init__(self, *, max_queue_size: int = 64) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._max_queue_size,
        )
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def broadcast(self, event: NotificationEvent) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)

        payload = event.to_dict()
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "Notification subscriber queue full, dropping event for session %s",
                    event.session_id,
                )
