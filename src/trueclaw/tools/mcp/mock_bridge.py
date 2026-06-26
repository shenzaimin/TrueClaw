from __future__ import annotations

from typing import Any


class MockMcpBridge:
    """离线 MCP 桥接，用于验收与本地开发。"""

    def __init__(self, *, name: str = "demo") -> None:
        self.name = name
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "echo",
                "description": "Echo text via mock MCP bridge",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            {
                "name": "ping",
                "description": "Return pong",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "echo":
            return str(arguments.get("text", ""))
        if name == "ping":
            return "pong"
        return f"error: unknown mock tool {name!r}"

    async def healthcheck(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self._connected = False
