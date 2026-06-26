from __future__ import annotations

import asyncio
import hmac
import json
import logging
import hashlib
import urllib.request
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.channels.base import BaseChannel


class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self._log = logging.getLogger(__name__)
        self._server: asyncio.AbstractServer | None = None

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "inboundEnabled": False,
            "listenHost": "127.0.0.1",
            "listenPort": 18890,
            "path": "/webhook",
            "verifyToken": "",
            "signingSecret": "",
            "signatureHeader": "X-TrueClaw-Signature",
            "outboundUrl": "",
            "outboundAuthHeader": "",
        }

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    async def _read_http_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes]:
        start_line = await reader.readline()
        if not start_line:
            raise ValueError("empty request")
        parts = start_line.decode("utf-8", errors="replace").strip().split()
        if len(parts) < 2:
            raise ValueError("invalid request line")
        method, path = parts[0].upper(), parts[1]

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                break
            if line in (b"\r\n", b"\n"):
                break
            text = line.decode("utf-8", errors="replace")
            if ":" not in text:
                continue
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0") or "0")
        body = await reader.readexactly(length) if length > 0 else b""
        return method, path, headers, body

    async def _write_http_response(
        self, writer: asyncio.StreamWriter, status: int, body: dict[str, Any]
    ) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        reason = "OK" if status < 400 else "ERROR"
        head = [
            f"HTTP/1.1 {status} {reason}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(payload)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(head).encode("utf-8") + payload)
        await writer.drain()

    def _token_ok(self, headers: dict[str, str]) -> bool:
        expected = str(self._cfg("verifyToken", "")).strip()
        if not expected:
            return True
        got = headers.get("x-trueclaw-token", "")
        return got == expected

    def _health_path(self) -> str:
        base = str(self._cfg("path", "/webhook")).strip() or "/webhook"
        return f"{base.rstrip('/')}/health"

    def _auth_mode(self) -> str:
        sign = str(self._cfg("signingSecret", "")).strip()
        token = str(self._cfg("verifyToken", "")).strip()
        if sign and token:
            return "signature+token"
        if sign:
            return "signature-only"
        if token:
            return "token-only"
        return "open"

    def _signature_ok(self, headers: dict[str, str], body: bytes) -> bool:
        secret = str(self._cfg("signingSecret", "")).strip()
        if not secret:
            return True
        header_name = str(self._cfg("signatureHeader", "X-TrueClaw-Signature")).strip().lower()
        if not header_name:
            header_name = "x-trueclaw-signature"
        got = headers.get(header_name, "").strip()
        if not got:
            return False
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        expected = f"sha256={digest}"
        return hmac.compare_digest(got, expected)

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        await self._write_http_response(
            writer,
            200,
            {
                "ok": True,
                "channel": self.name,
                "inbound_enabled": bool(self._cfg("inboundEnabled", False)),
                "path": str(self._cfg("path", "/webhook")).strip() or "/webhook",
                "health_path": self._health_path(),
                "auth_mode": self._auth_mode(),
            },
        )

    async def _handle_inbound_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            method, path, headers, body = await self._read_http_request(reader)
            expected_path = str(self._cfg("path", "/webhook")).strip() or "/webhook"
            if method == "GET" and path == self._health_path():
                await self._handle_health(writer)
                return
            if method != "POST" or path != expected_path:
                await self._write_http_response(writer, 404, {"ok": False, "error": "NOT_FOUND"})
                return
            if not self._token_ok(headers):
                await self._write_http_response(writer, 401, {"ok": False, "error": "UNAUTHORIZED"})
                return
            if not self._signature_ok(headers, body):
                await self._write_http_response(writer, 401, {"ok": False, "error": "INVALID_SIGNATURE"})
                return
            try:
                obj = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                await self._write_http_response(writer, 400, {"ok": False, "error": "INVALID_JSON"})
                return
            sender_id = str(obj.get("sender_id", "")).strip()
            chat_id = str(obj.get("chat_id", "")).strip()
            content = str(obj.get("content", "")).strip()
            if not sender_id or not chat_id or not content:
                await self._write_http_response(writer, 400, {"ok": False, "error": "MISSING_FIELDS"})
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
            await self._write_http_response(writer, 200, {"ok": True})
        except Exception as e:  # noqa: BLE001
            self._log.warning("webhook inbound error: %s", e)
            try:
                await self._write_http_response(writer, 500, {"ok": False, "error": "INTERNAL_ERROR"})
            except Exception:  # noqa: BLE001
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        self._running = True
        if not bool(self._cfg("enabled", False)):
            self._log.info("webhook disabled, skip start")
            return
        if bool(self._cfg("inboundEnabled", False)):
            host = str(self._cfg("listenHost", "127.0.0.1"))
            port = int(self._cfg("listenPort", 18890))
            self._server = await asyncio.start_server(self._handle_inbound_client, host=host, port=port)
            self._log.info(
                "webhook inbound listening on %s:%s%s (health=%s)",
                host,
                port,
                self._cfg("path", "/webhook"),
                self._health_path(),
            )

    async def stop(self) -> None:
        self._running = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def send(self, msg: OutboundMessageEvent) -> None:
        outbound_url = str(self._cfg("outboundUrl", "")).strip()
        if not outbound_url:
            self._log.info("[webhook stub send/no-url] chat=%s text=%s", msg.chat_id, msg.content)
            return

        payload = {
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "content": msg.content,
            "metadata": msg.metadata,
        }
        headers = {"Content-Type": "application/json"}
        auth = str(self._cfg("outboundAuthHeader", "")).strip()
        if auth:
            headers["Authorization"] = auth
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def _run() -> int:
            req = urllib.request.Request(outbound_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return int(resp.status)

        status = await asyncio.to_thread(_run)
        if status >= 400:
            raise RuntimeError(f"webhook outbound status={status}")
