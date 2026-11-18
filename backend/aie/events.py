"""
aie/events.py — AnalysisEventBus for broadcasting analysis progress events.

Architecture:
  - One global AnalysisEventBus instance (`event_bus`).
  - Callers subscribe to a specific run_id and receive copies of every event
    published for that run via their own asyncio.Queue.
  - The bus is entirely in-process; no external message broker is needed.

Usage:
    # Publisher (inside an analysis task):
    await event_bus.publish(run_id, {"type": "progress", "stage": "cloning"})

    # Subscriber (e.g. a WebSocket handler):
    async with event_bus.subscribe(run_id) as queue:
        event = await queue.get()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class AnalysisEventBus:
    """In-process pub/sub bus keyed by analysis run_id.

    Thread-safety note: asyncio queues are not thread-safe; all callers must
    run in the same event loop (the FastAPI default loop).
    """

    def __init__(self) -> None:
        # run_id -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = (
            defaultdict(list)
        )
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Publishing
    # ------------------------------------------------------------------ #

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Broadcast *event* to every subscriber watching *run_id*.

        Events are shallow-copied before enqueueing so that the publisher
        can safely mutate its dict afterwards.
        """
        event_copy = dict(event)
        event_copy.setdefault("run_id", run_id)

        async with self._lock:
            queues = list(self._subscribers.get(run_id, []))

        for q in queues:
            try:
                q.put_nowait(event_copy)
            except asyncio.QueueFull:
                logger.warning(
                    "event_bus: queue full for run_id=%s, event dropped", run_id
                )

    async def publish_done(self, run_id: str) -> None:
        """Signal end-of-stream to all subscribers of *run_id*.

        Each subscriber receives a ``None`` sentinel so it knows the run has
        finished and can break out of its read loop.
        """
        async with self._lock:
            queues = list(self._subscribers.get(run_id, []))

        for q in queues:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    # ------------------------------------------------------------------ #
    # Subscribing
    # ------------------------------------------------------------------ #

    @contextlib.asynccontextmanager
    async def subscribe(
        self,
        run_id: str,
        maxsize: int = 256,
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any] | None]]:
        """Async context manager that yields a private Queue for *run_id*.

        The queue is registered before the body executes and unregistered
        (and drained) automatically on exit, even if an exception is raised.

        Example::

            async with event_bus.subscribe(run_id) as q:
                while (event := await q.get()) is not None:
                    await websocket.send_json(event)
        """
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=maxsize)

        async with self._lock:
            self._subscribers[run_id].append(queue)

        try:
            yield queue
        finally:
            async with self._lock:
                try:
                    self._subscribers[run_id].remove(queue)
                except ValueError:
                    pass
                if not self._subscribers[run_id]:
                    del self._subscribers[run_id]

            # Drain remaining items so the queue can be GC'd cleanly.
            while not queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()

    # ------------------------------------------------------------------ #
    # Introspection helpers
    # ------------------------------------------------------------------ #

    async def subscriber_count(self, run_id: str) -> int:
        """Return how many subscribers are currently watching *run_id*."""
        async with self._lock:
            return len(self._subscribers.get(run_id, []))

    async def active_run_ids(self) -> list[str]:
        """Return all run IDs that currently have at least one subscriber."""
        async with self._lock:
            return list(self._subscribers.keys())


# Module-level singleton — import this everywhere
event_bus: AnalysisEventBus = AnalysisEventBus()
