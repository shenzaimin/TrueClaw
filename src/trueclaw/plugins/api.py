from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from trueclaw.channels.base import BaseChannel


@dataclass
class PluginContext:
    plugin_name: str
    plugin_config: dict[str, Any]
    global_config: dict[str, Any]
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChannelPlugin(Protocol):
    """第 12 章通道插件契约：返回已配置的 ChannelAdapter / BaseChannel。"""

    name: str
    version: str

    async def create_adapters(self, ctx: PluginContext) -> list["BaseChannel"]:
        ...
