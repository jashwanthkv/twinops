"""
client/workers/worker_queue.py — Background Async Task Worker Queue.

Processes receipt generation, invoice emails, and analytics events
asynchronously in a background daemon thread.
Measures worker_queue_depth and simulates worker stall / queue backlog faults.
"""
from __future__ import annotations
import queue
import threading
import time
import logging
from client.chaos_state import chaos

logger = logging.getLogger("client.worker")


class AsyncTaskWorkerQueue:
    """Background thread-based task queue for async post-checkout processing.

    Responsibilities:
      - Accept tasks from route handlers via enqueue()
      - Process tasks in a daemon thread loop
      - Stall when chaos.worker_stalled is True (simulating thread deadlock)
      - Expose queue_depth and stats for telemetry observability
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._total_enqueued: int = 0
        self._total_processed: int = 0
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="AsyncTaskWorker"
        )
        self._worker_thread.start()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def enqueue(self, task_name: str, payload: dict) -> None:
        """Add a new task to the queue; non-blocking."""
        self._q.put({"task": task_name, "payload": payload, "enqueued_at": time.time()})
        with self._lock:
            self._total_enqueued += 1

    # ------------------------------------------------------------------ #
    # Properties for telemetry
    # ------------------------------------------------------------------ #

    @property
    def queue_depth(self) -> int:
        """Current number of pending unprocessed tasks."""
        return self._q.qsize()

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #

    def _worker_loop(self) -> None:
        """Background daemon loop — drains tasks or stalls on chaos inject."""
        while True:
            try:
                task = self._q.get(timeout=0.5)
                # Chaos: stall the worker (simulates deadlock / thread hang)
                while chaos.worker_stalled:
                    time.sleep(0.5)
                # Normal task processing simulation (5-15 ms)
                time.sleep(0.01)
                self._q.task_done()
                with self._lock:
                    self._total_processed += 1
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("Error in worker loop: %s", exc)

    # ------------------------------------------------------------------ #
    # Observability helpers
    # ------------------------------------------------------------------ #

    def get_depth(self) -> int:
        """Alias for queue_depth — explicit method for health checks."""
        return self.queue_depth

    def get_stats(self) -> dict:
        """Return queue depth and processing statistics."""
        with self._lock:
            return {
                "queue_depth": self.queue_depth,
                "total_enqueued": self._total_enqueued,
                "total_processed": self._total_processed,
                "worker_stalled": chaos.worker_stalled,
                "pending_backlog": self._total_enqueued - self._total_processed,
            }

    def reset_stats(self) -> None:
        """Reset counters (used during profile transitions)."""
        with self._lock:
            self._total_enqueued = 0
            self._total_processed = 0

    def is_healthy(self) -> bool:
        """Worker is healthy if queue depth is under control."""
        return self.queue_depth < 100


# Singleton
worker_queue = AsyncTaskWorkerQueue()
