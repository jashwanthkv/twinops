"""
client/components/worker_queue.py — Background Async Worker Queue (Receipts, Invoicing).
Measures worker_queue_depth and simulates worker stalls / queue backlogs.
"""
from __future__ import annotations
import queue
import threading
import time
import logging
from client.chaos_state import chaos

logger = logging.getLogger("client.worker")

class WorkerQueue:
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def enqueue(self, task_name: str, payload: dict) -> None:
        self._q.put({"task": task_name, "payload": payload, "enqueued_at": time.time()})

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._q.get(timeout=0.5)
                # If worker is stalled by chaos, do not drain the queue
                while chaos.worker_stalled:
                    time.sleep(0.5)
                # Normal task processing (5-15ms)
                time.sleep(0.01)
                self._q.task_done()
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("Error in worker loop: %s", exc)

    @property
    def queue_depth(self) -> int:
        return self._q.qsize()

worker_queue = WorkerQueue()
