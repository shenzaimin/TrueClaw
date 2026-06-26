from __future__ import annotations

from trueclaw_plugin_echo.channel import EchoChannel


def plugin_entry() -> type[EchoChannel]:
    """第 12 章推荐：entry point 指向可调用工厂，返回通道类。"""
    return EchoChannel
