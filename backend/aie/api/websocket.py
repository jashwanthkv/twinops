"""
aie/api/websocket.py — WebSocket handler for live analysis progress streaming.

Streams progress updates for an active analysis run via WebSocket:
WS /ws/aie/analyses/{analysis_id}
Format: {"step": ..., "progress": ..., "message": ...}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aie.events import event_bus

logger = logging.getLogger(__name__)

ws_router = APIRouter(tags=["aie-ws"])
# Alias for main.py import compatibility
router = ws_router


@ws_router.websocket("/ws/aie/analyses/{analysis_id}")
async def analysis_progress_ws(websocket: WebSocket, analysis_id: str) -> None:
    """Stream real-time analysis progress updates for a given analysis_id."""
    await websocket.accept()
    logger.info("WebSocket connected for analysis %s", analysis_id)

    async with event_bus.subscribe(analysis_id) as queue:
        try:
            while True:
                try:
                    event: dict[str, Any] | None = await asyncio.wait_for(
                        queue.get(), timeout=30.0
                    )
                    # None sentinel indicates end-of-stream
                    if event is None:
                        break

                    step = event.get("step") or event.get("stage") or ""
                    progress = event.get("progress", 0)
                    message = event.get("message", "")

                    msg_payload: dict[str, Any] = {
                        "step": step,
                        "progress": progress,
                        "message": message,
                    }
                    if "status" in event:
                        msg_payload["status"] = event["status"]

                    await websocket.send_text(json.dumps(msg_payload))

                    # Terminate streaming once complete or failed
                    if step in ("complete", "completed", "failed"):
                        break

                except asyncio.TimeoutError:
                    # Send keepalive ping to maintain connection
                    try:
                        await websocket.send_text(
                            json.dumps({"type": "ping", "step": "ping", "progress": 0, "message": "ping"})
                        )
                    except Exception:
                        break

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected gracefully by client for analysis %s", analysis_id)
        except Exception as exc:
            logger.error("WebSocket error for analysis %s: %s", analysis_id, exc)
            try:
                await websocket.close(code=1011, reason=str(exc))
            except Exception:
                pass
        finally:
            logger.debug("WebSocket handler finished for analysis %s", analysis_id)
