from __future__ import annotations

import asyncio

from trueclaw.config.loader import load_config
from trueclaw.tools.bootstrap import build_tool_registry


def cmd_tools_list(config_path: str) -> int:
    cfg = load_config(config_path)
    registry, _router = build_tool_registry(cfg)
    if not registry.names():
        print("No tools registered")
        return 0
    for name in registry.names():
        tool = registry.get(name)
        print(f"{name}\t{tool.description}")
    return 0


def cmd_tools_mcp_list(config_path: str) -> int:
    cfg = load_config(config_path)
    registry, router = build_tool_registry(cfg)
    if router is None:
        print("No MCP bridges enabled")
        return 0
    for bridge in router.bridge_names:
        print(f"bridge\t{bridge}")
    for name in registry.names():
        if name.startswith("mcp__"):
            print(f"tool\t{name}")
    return 0


async def _cmd_tools_mcp_doctor_async(config_path: str) -> int:
    cfg = load_config(config_path)
    _, router = build_tool_registry(cfg)
    if router is None:
        print("mcp\tdisabled")
        return 0
    health = await router.health_snapshot()
    for name, ok in health.items():
        print(f"{name}\t{'OK' if ok else 'FAIL'}")
    return 0


def cmd_tools_mcp_doctor(config_path: str) -> int:
    return asyncio.run(_cmd_tools_mcp_doctor_async(config_path))
