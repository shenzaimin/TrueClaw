from __future__ import annotations

import asyncio

from trueclaw.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, *, timeout_sec: float = 30.0) -> None:
        self.registry = registry
        self.timeout_sec = timeout_sec

    async def execute(self, name: str, args: dict) -> str:
        tool = self.registry.get(name)

        async def _run() -> str:
            return await asyncio.to_thread(tool.func, args)

        return await asyncio.wait_for(_run(), timeout=self.timeout_sec)
