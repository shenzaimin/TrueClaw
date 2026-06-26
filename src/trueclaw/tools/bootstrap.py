from __future__ import annotations

import asyncio
import logging

from trueclaw.config.schema import AppConfig
from trueclaw.tools.builtins import make_file_read_tool
from trueclaw.tools.mcp.config import parse_mcp_servers
from trueclaw.tools.mcp.router import McpRouter, make_bridge, register_mcp_tools
from trueclaw.tools.registry import ToolRegistry
from trueclaw.tools.sync_async import run_sync_coro

_log = logging.getLogger(__name__)


_last_mcp_router: McpRouter | None = None


async def _load_mcp_router(cfg: AppConfig) -> McpRouter | None:
    servers = parse_mcp_servers(cfg.mcp)
    enabled = [s for s in servers if s.enabled]
    if not enabled:
        return None
    router = McpRouter()
    for server in enabled:
        try:
            bridge = make_bridge(server)
            await router.add_bridge(server.name, bridge)
            _log.info("mcp bridge connected name=%s transport=%s", server.name, server.transport)
        except Exception as e:  # noqa: BLE001
            _log.warning("mcp bridge failed name=%s error=%s", server.name, e)
    if not router.bridge_names:
        return None
    return router


def build_tool_registry(cfg: AppConfig) -> tuple[ToolRegistry, McpRouter | None]:
    global _last_mcp_router
    registry = ToolRegistry()
    if cfg.tools.enableFileRead:
        registry.register(make_file_read_tool(cfg.tools.workspaceDir))
    router = run_sync_coro(_load_mcp_router(cfg))
    if router is not None:
        register_mcp_tools(router, registry)
    _last_mcp_router = router
    return registry, router


async def build_tool_registry_async(cfg: AppConfig) -> tuple[ToolRegistry, McpRouter | None]:
    """在已有 asyncio 事件循环中加载 MCP（stdio 子进程须绑定当前 loop）。"""
    global _last_mcp_router
    registry = ToolRegistry()
    if cfg.tools.enableFileRead:
        registry.register(make_file_read_tool(cfg.tools.workspaceDir))
    router = await _load_mcp_router(cfg)
    if router is not None:
        register_mcp_tools(router, registry)
    _last_mcp_router = router
    return registry, router


async def close_mcp_routers_async() -> None:
    global _last_mcp_router
    if _last_mcp_router is None:
        return
    try:
        await _last_mcp_router.close_all()
    finally:
        _last_mcp_router = None


def close_mcp_routers() -> None:
    global _last_mcp_router
    if _last_mcp_router is None:
        return
    try:
        run_sync_coro(_last_mcp_router.close_all())
    finally:
        _last_mcp_router = None
