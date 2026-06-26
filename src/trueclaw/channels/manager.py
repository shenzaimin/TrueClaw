from __future__ import annotations

import asyncio
import logging
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.bus.queue import MessageBus
from trueclaw.channels.base import BaseChannel
from trueclaw.channels.delta_coalescer import StreamOutboundCoalescer
from trueclaw.channels.registry import discover_all
from trueclaw.config.schema import AppConfig


class ChannelManager:
    def __init__(self, config: AppConfig, bus: MessageBus, *, config_path: str = ""):
        self.config = config
        self.config_path = config_path
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._log = logging.getLogger(__name__)
        coalesce_ms = float(getattr(config.gateway, "streamCoalesceMs", 300.0))
        self._coalescer = StreamOutboundCoalescer(coalesce_ms=coalesce_ms)
        self._coalescer.bind(self._deliver_message)
        self._init_channels()

    def _is_enabled(self, section: Any) -> bool:
        if section is None:
            return False
        if isinstance(section, dict):
            return bool(section.get("enabled", False))
        return bool(getattr(section, "enabled", False))

    def _init_channels(self) -> None:
        for name, cls in discover_all().items():
            section = self.config.channels.get(name)
            if not self._is_enabled(section):
                continue
            section_data = section if isinstance(section, dict) else vars(section)
            if name == "telegram":
                ch = cls(section_data, self.bus, config_path=self.config_path)
            else:
                ch = cls(section_data, self.bus)
            self.channels[name] = ch
            self._log.info("channel enabled: %s", name)

    async def start_all(self) -> None:
        if not self.channels:
            self._log.warning("no channels enabled")
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        for ch in self.channels.values():
            await ch.start()

    async def stop_all(self) -> None:
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        for ch in self.channels.values():
            await ch.stop()
        await self._coalescer.close()

    async def _deliver_message(self, msg: OutboundMessageEvent) -> None:
        channel = self.channels.get(msg.channel)
        if channel:
            await self._send_with_retry(channel, msg)
        else:
            self._log.warning("unknown outbound channel: %s", msg.channel)

    async def _dispatch_outbound(self) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if msg.metadata.get("delivery") == "drop":
                self._log.info("outbound dropped delivery=drop channel=%s", msg.channel)
                continue
            await self._coalescer.handle(msg)

    async def _send_with_retry(self, channel: BaseChannel, msg: OutboundMessageEvent) -> None:
        max_attempts = int(getattr(self.config.channels, "send_max_retries", 3) if not isinstance(self.config.channels, dict) else self.config.channels.get("send_max_retries", 3))
        max_attempts = max(1, min(max_attempts, 10))
        delay = 0.5
        for i in range(max_attempts):
            try:
                await channel.send(msg)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if i == max_attempts - 1:
                    self._log.error("outbound send failed channel=%s attempts=%s error=%s", msg.channel, max_attempts, e)
                    return
                self._log.warning("outbound send retry channel=%s attempt=%s/%s error=%s", msg.channel, i + 1, max_attempts, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 4.0)

    @property
    def enabled_channels(self) -> list[str]:
        return sorted(self.channels.keys())

    def reload_telegram_allowlist(self, config_path: str | None = None) -> list[str]:
        from trueclaw.config.loader import load_config

        path = config_path or self.config_path
        if not path:
            raise RuntimeError("config_path required for allowlist reload")
        cfg = load_config(path)
        section = cfg.channels.get("telegram")
        if section is None:
            raise RuntimeError("telegram channel not configured")
        allow = list(getattr(section, "allowFrom", []) or [])
        if isinstance(section, dict):
            allow = list(section.get("allowFrom", []) or [])
        ch = self.channels.get("telegram")
        if ch is None:
            raise RuntimeError("telegram channel not enabled")
        if hasattr(ch, "reload_allowlist"):
            return ch.reload_allowlist(allow)
        if isinstance(ch.config, dict):
            ch.config["allowFrom"] = allow
        else:
            setattr(ch.config, "allowFrom", allow)
        self._log.info("telegram allowlist reloaded count=%s", len(allow))
        return allow
