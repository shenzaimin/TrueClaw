from __future__ import annotations

import asyncio

from trueclaw.bus.events import InboundMessageEvent, OutboundMessageEvent


class MessageBus:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessageEvent] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessageEvent] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessageEvent) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessageEvent:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessageEvent) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessageEvent:
        return await self.outbound.get()
