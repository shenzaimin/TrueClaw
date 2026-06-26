from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent


class StreamOutboundCoalescer:
    def __init__(self, *, coalesce_ms: float = 300.0) -> None:
        self.coalesce_ms = max(0.0, coalesce_ms) / 1000.0
        self._buffers: dict[tuple[str, str], str] = {}
        self._meta: dict[tuple[str, str], dict[str, Any]] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._send_fn: Callable[[OutboundMessageEvent], Awaitable[None]] | None = None

    def bind(self, send_fn: Callable[[OutboundMessageEvent], Awaitable[None]]) -> None:
        self._send_fn = send_fn

    def _key(self, msg: OutboundMessageEvent) -> tuple[str, str]:
        return (msg.channel, msg.chat_id)

    async def _flush(self, key: tuple[str, str]) -> None:
        self._tasks.pop(key, None)
        if self._send_fn is None:
            return
        text = self._buffers.pop(key, "")
        meta = self._meta.pop(key, {})
        if not text:
            return
        channel, chat_id = key
        await self._send_fn(
            OutboundMessageEvent(
                channel=channel,
                chat_id=chat_id,
                content=text,
                metadata={**meta, "stream_phase": "coalesced"},
            )
        )

    def _schedule_flush(self, key: tuple[str, str]) -> None:
        if self.coalesce_ms <= 0:
            return
        task = self._tasks.get(key)
        if task and not task.done():
            return
        self._tasks[key] = asyncio.create_task(self._delayed_flush(key))

    async def _delayed_flush(self, key: tuple[str, str]) -> None:
        await asyncio.sleep(self.coalesce_ms)
        await self._flush(key)

    async def handle(self, msg: OutboundMessageEvent) -> None:
        if self._send_fn is None:
            return
        phase = msg.metadata.get("stream_phase")
        if phase is None:
            await self._send_fn(msg)
            return
        key = self._key(msg)
        if phase == "delta":
            self._buffers[key] = self._buffers.get(key, "") + msg.content
            self._meta[key] = {k: v for k, v in msg.metadata.items() if k != "stream_phase"}
            if self.coalesce_ms <= 0:
                await self._flush(key)
            else:
                self._schedule_flush(key)
            return
        if phase == "final":
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            pending = self._buffers.pop(key, "")
            self._meta.pop(key, {})
            final_text = pending + msg.content
            if final_text:
                await self._send_fn(
                    OutboundMessageEvent(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=final_text,
                        metadata={**msg.metadata, "stream_phase": "final"},
                    )
                )
            return
        await self._send_fn(msg)

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
        for key in list(self._buffers):
            await self._flush(key)
