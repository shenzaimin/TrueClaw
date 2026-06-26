from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any
from urllib.parse import urlparse

from trueclaw.tools.mcp.config import McpServerConfig


class McpHttpBridge:
    """MCP HTTP 传输：单次 POST JSON-RPC 2.0（streamable HTTP 最小子集）。"""

    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._log = logging.getLogger(__name__)
        self._session_id: str | None = None

    async def connect(self) -> None:
        if not self.cfg.url:
            raise RuntimeError(f"mcp server {self.cfg.name}: http url missing")
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
        try:
            await self._request("tools/list", {})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        self._session_id = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._post_json({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            req_id = self._next_id()
            frame = await self._post_json(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            )
            if "error" in frame:
                err = frame["error"]
                raise RuntimeError(f"mcp error: {err.get('message', err)}")
            result = frame.get("result", {})
            return result if isinstance(result, dict) else {}

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        parsed = urlparse(self.cfg.url)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError(f"unsupported mcp url scheme: {parsed.scheme}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Host": f"{host}:{port}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Length": str(len(body)),
            "Connection": "close",
        }
        for key, value in self.cfg.headers.items():
            headers[str(key)] = str(value)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        header_block = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        request = (
            f"POST {path} HTTP/1.1\r\n{header_block}\r\n".encode("utf-8") + body
        )
        ssl_ctx = ssl.create_default_context() if parsed.scheme == "https" else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx),
            timeout=self.cfg.timeout_sec,
        )
        try:
            writer.write(request)
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), timeout=self.cfg.timeout_sec)
            if b"204" in status_line:
                return {}
            if b"200" not in status_line:
                raise RuntimeError(f"mcp http unexpected status: {status_line.decode().strip()}")
            content_length: int | None = None
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=self.cfg.timeout_sec)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, val = line.decode("utf-8", errors="replace").partition(":")
                key_l = key.strip().lower()
                if key_l == "content-length":
                    content_length = int(val.strip())
                elif key_l == "mcp-session-id":
                    self._session_id = val.strip()
            if content_length is not None:
                raw = await asyncio.wait_for(reader.readexactly(content_length), timeout=self.cfg.timeout_sec)
            else:
                raw = await asyncio.wait_for(reader.read(), timeout=self.cfg.timeout_sec)
            if not raw:
                return {}
            frame = json.loads(raw.decode("utf-8", errors="replace"))
            return frame if isinstance(frame, dict) else {}
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
