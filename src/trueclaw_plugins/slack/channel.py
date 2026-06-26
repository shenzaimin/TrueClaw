from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from trueclaw.bus.events import OutboundMessageEvent
from trueclaw.channels.base import BaseChannel
from trueclaw_plugins.slack.signature import verify_slack_signature


class SlackChannel(BaseChannel):
    """Slack Events API 骨架通道（第 13 章）：验签 → 归一化 → 入站；chat.postMessage 出站。"""

    name = "slack"
    display_name = "Slack"

    def __init__(self, config: Any, bus) -> None:
        super().__init__(config, bus)
        self._log = logging.getLogger(__name__)
        self._server: asyncio.AbstractServer | None = None

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "botToken": "",
            "signingSecret": "",
            "listenHost": "127.0.0.1",
            "listenPort": 18992,
            "webhookPath": "/hooks/slack/events",
            "botUserId": "",
            "allowWorkspaceIds": [],
        }

    def _cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _webhook_path(self) -> str:
        return str(self._cfg("webhookPath", "/hooks/slack/events")).strip() or "/hooks/slack/events"

    def _health_path(self) -> str:
        return f"{self._webhook_path().rstrip('/')}/health"

    async def start(self) -> None:
        if self._running:
            return
        host = str(self._cfg("listenHost", "127.0.0.1"))
        port = int(self._cfg("listenPort", 18992))
        self._server = await asyncio.start_server(self._handle_client, host, port)
        self._running = True
        self._log.info(
            "slack events listening http://%s:%s%s (health=%s)",
            host,
            port,
            self._webhook_path(),
            self._health_path(),
        )

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._log.info("slack channel stopped")

    async def send(self, msg: OutboundMessageEvent) -> None:
        token = str(self._cfg("botToken", "")).strip()
        if not token:
            self._log.info("[slack stub send/no-token] chat=%s text=%s", msg.chat_id, msg.content[:200])
            return
        payload: dict[str, Any] = {"channel": msg.chat_id, "text": msg.content}
        thread_id = msg.metadata.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            payload["thread_ts"] = thread_id
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def _post() -> dict[str, Any]:
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))

        try:
            result = await asyncio.to_thread(_post)
        except urllib.error.URLError as e:
            raise RuntimeError(f"slack outbound network error: {e.reason}") from e
        if not result.get("ok"):
            raise RuntimeError(f"slack outbound api error: {result.get('error', 'unknown')}")

    async def _write_json(self, writer: asyncio.StreamWriter, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        reason = "OK" if status == 200 else "Error"
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

    def _normalize_message_event(self, envelope: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") != "message":
            return None
        if event.get("subtype") or event.get("bot_id"):
            return None
        bot_user = str(self._cfg("botUserId", "")).strip()
        if bot_user and str(event.get("user", "")) == bot_user:
            return None
        text = str(event.get("text", "")).strip()
        if not text:
            return None
        team_id = str(envelope.get("team_id", "")).strip()
        allowed = self._cfg("allowWorkspaceIds", []) or []
        if allowed and team_id and team_id not in allowed:
            return None
        thread_ts = event.get("thread_ts")
        metadata: dict[str, Any] = {"team_id": team_id}
        if isinstance(thread_ts, str) and thread_ts:
            metadata["thread_id"] = thread_ts
        return {
            "sender_id": str(event.get("user", "")),
            "chat_id": str(event.get("channel", "")),
            "content": text,
            "metadata": metadata,
        }

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, body = await self._read_http_request(reader)
            expected = self._webhook_path()
            if method == "GET" and path == self._health_path():
                await self._write_json(
                    writer,
                    200,
                    {
                        "ok": True,
                        "channel": self.name,
                        "path": expected,
                        "auth_mode": "open" if not str(self._cfg("signingSecret", "")).strip() else "signature",
                    },
                )
                return
            if method != "POST" or path != expected:
                await self._write_json(writer, 404, {"ok": False, "error": "NOT_FOUND"})
                return
            if not verify_slack_signature(
                str(self._cfg("signingSecret", "")).strip(),
                body,
                headers.get("x-slack-request-timestamp"),
                headers.get("x-slack-signature"),
            ):
                await self._write_json(writer, 401, {"ok": False, "error": "INVALID_SIGNATURE"})
                return
            try:
                obj = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                await self._write_json(writer, 400, {"ok": False, "error": "INVALID_JSON"})
                return
            if obj.get("type") == "url_verification":
                challenge = str(obj.get("challenge", ""))
                await self._write_json(writer, 200, {"challenge": challenge})
                return
            if obj.get("type") != "event_callback":
                await self._write_json(writer, 200, {"ok": True, "ignored": True})
                return
            event = obj.get("event")
            if not isinstance(event, dict):
                await self._write_json(writer, 200, {"ok": True})
                return
            normalized = self._normalize_message_event(obj, event)
            await self._write_json(writer, 200, {"ok": True})
            if normalized:
                await self._handle_message(**normalized)
        except Exception as e:  # noqa: BLE001
            self._log.warning("slack inbound error: %s", e)
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
