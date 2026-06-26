from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from trueclaw.gateway.protocol import event_frame
from trueclaw.gateway.websocket import write_text_message


async def heartbeat_loop(
    writer: asyncio.StreamWriter,
    *,
    interval_sec: float,
    cancel: asyncio.Event,
) -> None:
    interval = max(1.0, float(interval_sec))
    while not cancel.is_set():
        try:
            await asyncio.wait_for(cancel.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            frame = event_frame("gateway.heartbeat", {"ts": int(time.time())})
            await write_text_message(writer, json.dumps(frame, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            break


async def idle_guard_loop(
    writer: asyncio.StreamWriter,
    *,
    idle_timeout_sec: float,
    last_seen_at: Callable[[], float],
    cancel: asyncio.Event,
) -> None:
    timeout = max(5.0, float(idle_timeout_sec))
    while not cancel.is_set():
        try:
            await asyncio.wait_for(cancel.wait(), timeout=1.0)
            break
        except asyncio.TimeoutError:
            pass
        if time.time() - last_seen_at() > timeout:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            break
