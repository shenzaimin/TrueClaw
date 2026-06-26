from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.channels.base import BaseChannel


class EchoChannel(BaseChannel):
    """示例插件通道：HTTP 入站 + 出站日志回显（第 12/13 章 entry point 演示）。"""

    name = "echo"
    display_name = "Echo Plugin"

    def __init__(self, config: Any, bus) -> None:
        super().__init__(config, bus)
        self._log = logging.getLogger(__name__)
        self._server: asyncio.AbstractServer | None = None

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "inboundEnabled": True,
            "listenHost": "127.0.0.1",
            "listenPort": 18991,
            "path": "/echo/inbound",
            "verifyToken": "",
            "logPrefix": "[echo-plugin]",
        }

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    async def start(self) -> None:
        if self._running:
            return
        if not bool(self._cfg("inboundEnabled", True)):
            self._running = True
            self._log.info("echo plugin started (outbound-only)")
            return
        host = str(self._cfg("listenHost", "127.0.0.1"))
        port = int(self._cfg("listenPort", 18991))
        self._server = await asyncio.start_server(self._handle_client, host, port)
        self._running = True
        self._log.info("echo plugin listening http://%s:%s%s", host, port, self._cfg("path", "/echo/inbound"))

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._log.info("echo plugin stopped")

    async def send(self, msg: OutboundMessageEvent) -> None:
        prefix = str(self._cfg("logPrefix", "[echo-plugin]"))
        text = msg.content if len(msg.content) <= 500 else msg.content[:500] + "…"
        self._log.info("%s channel=%s chat=%s content=%s", prefix, msg.channel, msg.chat_id, text)

    def _health_path(self) -> str:
        base = str(self._cfg("path", "/echo/inbound")).strip() or "/echo/inbound"
        return f"{base.rstrip('/')}/health"

    def _token_ok(self, headers: dict[str, str]) -> bool:
        expected = str(self._cfg("verifyToken", "")).strip()
        if not expected:
            return True
        return headers.get("x-trueclaw-token", "") == expected

    async def _write_json(self, writer: asyncio.StreamWriter, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        head = [
            f"HTTP/1.1 {status} OK" if status == 200 else f"HTTP/1.1 {status}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(payload)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(head).encode("utf-8") + payload)
        await writer.drain()

    async def _read_http_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes]:
        request_line = (await reader.readline()).decode("utf-8", errors="replace").strip()
        parts = request_line.split()
        method = parts[0] if parts else ""
        path = parts[1].split("?", 1)[0] if len(parts) > 1 else ""
        headers: dict[str, str] = {}
        while True:
            line = (await reader.readline()).decode("utf-8", errors="replace").strip()
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0") or "0")
        body = await reader.readexactly(length) if length > 0 else b""
        return method, path, headers, body

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, body = await self._read_http_request(reader)
            expected = str(self._cfg("path", "/echo/inbound")).strip() or "/echo/inbound"
            if method == "GET" and path == self._health_path():
                await self._write_json(
                    writer,
                    200,
                    {"ok": True, "channel": self.name, "path": expected, "health_path": self._health_path()},
                )
                return
            if method != "POST" or path != expected:
                await self._write_json(writer, 404, {"ok": False, "error": "NOT_FOUND"})
                return
            if not self._token_ok(headers):
                await self._write_json(writer, 401, {"ok": False, "error": "UNAUTHORIZED"})
                return
            try:
                obj = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                await self._write_json(writer, 400, {"ok": False, "error": "INVALID_JSON"})
                return
            sender_id = str(obj.get("sender_id", "")).strip()
            chat_id = str(obj.get("chat_id", "")).strip()
            content = str(obj.get("content", "")).strip()
            if not sender_id or not chat_id or not content:
                await self._write_json(writer, 400, {"ok": False, "error": "MISSING_FIELDS"})
                return
            metadata = obj.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            session_key = obj.get("session_key")
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata,
                session_key=session_key if isinstance(session_key, str) else None,
            )
            await self._write_json(writer, 200, {"ok": True})
        except Exception as e:  # noqa: BLE001
            self._log.warning("echo inbound error: %s", e)
            try:
                await self._write_json(writer, 500, {"ok": False, "error": "INTERNAL"})
            except Exception:  # noqa: BLE001
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
