from __future__ import annotations

import asyncio
from typing import Any

from trueclaw.tools.base import ToolDefinition
from trueclaw.tools.sync_async import run_sync_coro
from trueclaw.tools.mcp.bridge import McpBridgeBase
from trueclaw.tools.mcp.config import McpServerConfig
from trueclaw.tools.mcp.mock_bridge import MockMcpBridge
from trueclaw.tools.mcp.stdio_bridge import McpStdioBridge
from trueclaw.tools.mcp.http_bridge import McpHttpBridge


def _fq_name(bridge_name: str, tool_name: str) -> str:
    return f"mcp__{bridge_name}__{tool_name}"


class McpRouter:
    def __init__(self) -> None:
        self._bridges: dict[str, McpBridgeBase] = {}
        self._tools_index: dict[str, str] = {}
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def bridge_names(self) -> list[str]:
        return sorted(self._bridges.keys())

    async def add_bridge(self, bridge_name: str, bridge: McpBridgeBase) -> None:
        await bridge.connect()
        async with self._lock:
            self._bridges[bridge_name] = bridge
        await self.refresh_tools(bridge_name)

    async def refresh_tools(self, bridge_name: str) -> None:
        bridge = self._bridges[bridge_name]
        tools = await bridge.list_tools()
        async with self._lock:
            stale = [k for k, v in self._tools_index.items() if v == bridge_name]
            for k in stale:
                del self._tools_index[k]
                self._tool_schemas.pop(k, None)
            for t in tools:
                name = t.get("name")
                if not name:
                    continue
                fq = _fq_name(bridge_name, str(name))
                self._tools_index[fq] = bridge_name
                schema = t.get("inputSchema") or {"type": "object", "properties": {}}
                self._tool_schemas[fq] = schema if isinstance(schema, dict) else {"type": "object"}

    def tool_definitions(self) -> list[tuple[str, str, dict[str, Any]]]:
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for fq, bridge_name in sorted(self._tools_index.items()):
            base = fq.split("__", 2)[-1]
            rows.append((fq, f"MCP tool from {bridge_name}: {base}", self._tool_schemas.get(fq, {})))
        return rows

    async def call(self, fq_name: str, arguments: dict[str, Any]) -> str:
        async with self._lock:
            bridge_name = self._tools_index.get(fq_name)
            if bridge_name is None:
                return f"error: unknown mcp tool {fq_name!r}"
            bridge = self._bridges[bridge_name]
        base_name = fq_name.split("__", 2)[-1]
        return await bridge.call_tool(base_name, arguments)

    async def health_snapshot(self) -> dict[str, bool]:
        async with self._lock:
            pairs = list(self._bridges.items())
        out: dict[str, bool] = {}
        for name, bridge in pairs:
            try:
                out[name] = await bridge.healthcheck()
            except Exception:  # noqa: BLE001
                out[name] = False
        return out

    async def close_all(self) -> None:
        async with self._lock:
            bridges = list(self._bridges.values())
            self._bridges.clear()
            self._tools_index.clear()
            self._tool_schemas.clear()
        for bridge in bridges:
            try:
                await bridge.close()
            except Exception:  # noqa: BLE001
                pass


def _run_sync(coro):
    return run_sync_coro(coro)


def make_bridge(cfg: McpServerConfig) -> McpBridgeBase:
    if cfg.transport == "stdio":
        return McpStdioBridge(cfg)
    if cfg.transport == "http":
        return McpHttpBridge(cfg)
    return MockMcpBridge(name=cfg.name)


def register_mcp_tools(router: McpRouter, registry) -> None:
    for fq_name, description, schema in router.tool_definitions():

        def _make_func(name: str):
            def _run(args: dict[str, Any]) -> str:
                return run_sync_coro(router.call(name, args))

            return _run

        registry.register(
            ToolDefinition(
                name=fq_name,
                description=description,
                func=_make_func(fq_name),
                parameters_schema=schema,
            )
        )
