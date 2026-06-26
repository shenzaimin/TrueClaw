from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from trueclaw.tools.mcp.config import McpServerConfig


class McpStdioBridge:
    """MCP stdio 传输：newline-delimited JSON-RPC 2.0。"""

    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._log = logging.getLogger(__name__)

    async def connect(self) -> None:
        if not self.cfg.command:
            raise RuntimeError(f"mcp server {self.cfg.name}: stdio command missing")
        self._proc = await asyncio.create_subprocess_exec(
            *self.cfg.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "trueclaw", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if not isinstance(content, list):
            return str(result)
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)

    async def healthcheck(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            self._proc.kill()
        self._proc = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("mcp stdio not connected")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                raise RuntimeError("mcp stdio not connected")
            req_id = self._next_id()
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
            while True:
                raw = await asyncio.wait_for(
                    self._proc.stdout.readline(),
                    timeout=self.cfg.timeout_sec,
                )
                if not raw:
                    raise RuntimeError("mcp stdio closed")
                frame = json.loads(raw.decode("utf-8", errors="replace"))
                if frame.get("id") != req_id:
                    continue
                if "error" in frame:
                    err = frame["error"]
                    raise RuntimeError(f"mcp error: {err.get('message', err)}")
                result = frame.get("result", {})
                return result if isinstance(result, dict) else {}
