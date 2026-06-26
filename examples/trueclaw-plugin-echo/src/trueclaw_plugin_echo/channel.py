from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.channels.base import BaseChannel


class EchoChannel(BaseChannel):
    name = "echo"
    display_name = "Echo Plugin (standalone package)"

    def __init__(self, config: Any, bus) -> None:
        super().__init__(config, bus)
        self._log = logging.getLogger(__name__)
        self._server: asyncio.AbstractServer | None = None

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "inboundEnabled": True,
            "listenHost": "127.0.0.1",
            "listenPort": 18991,
            "path": "/echo/inbound",
            "verifyToken": "",
            "logPrefix": "[echo-plugin-standalone]",
        }

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._log.info("standalone echo plugin started (outbound log only)")

    async def stop(self) -> None:
        self._running = False
        self._log.info("standalone echo plugin stopped")

    async def send(self, msg: OutboundMessageEvent) -> None:
        prefix = str(self._cfg("logPrefix", "[echo-plugin-standalone]"))
        text = msg.content if len(msg.content) <= 500 else msg.content[:500] + "…"
        self._log.info("%s chat=%s content=%s", prefix, msg.chat_id, text)
