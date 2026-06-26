from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
import uuid
from typing import Any

from trueclaw.agent.runner import AgentRunner
from trueclaw.bus.queue import MessageBus
from trueclaw.channels.manager import ChannelManager
from trueclaw.config.schema import GatewayConfig
from trueclaw.gateway.connection_manager import ConnectionManager
from trueclaw.gateway.protocol import (
    ProtocolError,
    decode_frame,
    error_frame,
    event_frame,
    response_frame,
    validate_request,
)
from trueclaw.gateway.rbac import check_action_role
from trueclaw.gateway.websocket import (
    MessageTooLargeError,
    accept_websocket,
    read_text_message,
    write_text_message,
)
from trueclaw.gateway.ws_loops import heartbeat_loop, idle_guard_loop
from trueclaw.observability.metrics import get_metrics
from trueclaw.scheduler.engine import SchedulerEngine


class GatewayServer:
    def __init__(
        self,
        *,
        bind: str,
        port: int,
        path: str = "/ws",
        bus: MessageBus,
        channels: ChannelManager,
        runner: AgentRunner,
        scheduler: SchedulerEngine | None = None,
        push_outbound_events: bool = True,
        gateway_cfg: GatewayConfig | None = None,
        config_path: str = "",
    ) -> None:
        self.bind = bind
        self.port = port
        self.path = path
        self.bus = bus
        self.channels = channels
        self.runner = runner
        self.scheduler = scheduler
        self._push_outbound_events = push_outbound_events
        self._gateway_cfg = gateway_cfg or GatewayConfig(bind=bind, port=port, path=path)
        self._config_path = config_path
        self._heartbeat_interval = float(self._gateway_cfg.heartbeatIntervalSec)
        self._idle_timeout = float(self._gateway_cfg.idleTimeoutSec)
        self._max_message_bytes = int(self._gateway_cfg.maxMessageBytes)
        self._cancel = asyncio.Event()
        self._log = logging.getLogger(__name__)
        self._conn = ConnectionManager()
        self._ws_server: asyncio.AbstractServer | None = None
        self._control_server: asyncio.AbstractServer | None = None
        self._started_at = time.time()

    async def _publish_outbound(self, msg) -> None:
        from trueclaw.bus.events import OutboundMessageEvent

        if not isinstance(msg, OutboundMessageEvent):
            return
        await self.bus.publish_outbound(msg)
        if not self._push_outbound_events:
            return
        sent = await self._conn.broadcast_ws(
            event_frame(
                "channel.outbound",
                {
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "content": msg.content,
                    "metadata": msg.metadata,
                },
            )
        )
        if sent:
            get_metrics().inc("gateway.ws.events.outbound", sent)

    async def _consume_inbound(self) -> None:
        while not self._cancel.is_set():
            try:
                ev = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            out = await self.runner.handle_event(ev, on_outbound=self._publish_outbound)
            await self._publish_outbound(out)

    async def _dispatch_action(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        conn_id: str | None = None,
        role: str = "admin",
    ) -> dict[str, Any]:
        get_metrics().inc(f"gateway.action.{action}")
        if action == "gateway.auth":
            if conn_id is None:
                raise ProtocolError("gateway.auth requires websocket connection")
            new_role = str(payload.get("role", "viewer")).strip().lower()
            if new_role not in {"viewer", "operator", "admin"}:
                raise ProtocolError("invalid role")
            self._conn.set_ws_role(conn_id, new_role)
            return {"ok": True, "role": new_role}
        if action not in {"gateway.auth"} and not check_action_role(action, role):
            raise ProtocolError(f"forbidden action for role={role}: {action}")
        if action == "gateway.ping":
            return {
                "ok": True,
                "bind": self.bind,
                "port": self.port,
                "path": self.path,
                "active_connections": self._conn.count(),
            }
        if action == "gateway.subscribe":
            patterns = payload.get("patterns", [])
            if not isinstance(patterns, list):
                raise ProtocolError("patterns must be a list")
            if conn_id is None:
                raise ProtocolError("gateway.subscribe requires websocket connection")
            self._conn.subscribe(conn_id, [str(p) for p in patterns])
            conn = self._conn.get_ws(conn_id)
            subs = sorted(conn.subscriptions) if conn else []
            return {"ok": True, "subscriptions": subs}
        if action == "gateway.unsubscribe":
            patterns = payload.get("patterns", [])
            if not isinstance(patterns, list):
                raise ProtocolError("patterns must be a list")
            if conn_id is None:
                raise ProtocolError("gateway.unsubscribe requires websocket connection")
            self._conn.unsubscribe(conn_id, [str(p) for p in patterns])
            conn = self._conn.get_ws(conn_id)
            subs = sorted(conn.subscriptions) if conn else []
            return {"ok": True, "subscriptions": subs}
        if action == "gateway.metrics":
            snap = get_metrics().snapshot()
            snap["active_connections"] = self._conn.count()
            return {"ok": True, "metrics": snap}
        if action == "gateway.stats":
            snap = get_metrics().snapshot()
            return {
                "ok": True,
                "uptime_sec": int(time.time() - self._started_at),
                "active_connections": self._conn.count(),
                "queue_backlog": snap.get("agent.events", 0),
                "metrics": snap,
            }
        if action == "scheduler.list":
            if self.scheduler is None:
                return {"items": [], "count": 0}
            items = self.scheduler.list_tasks()
            return {"items": items, "count": len(items)}
        if action == "session.list":
            limit = int(payload.get("limit", 50))
            offset = int(payload.get("offset", 0))
            offset = max(0, offset)
            sessions = await self.runner.list_sessions()
            page_size = max(limit, 0)
            items = sessions[offset : offset + page_size] if page_size else []
            return {
                "items": items,
                "count": len(sessions),
                "offset": offset,
                "limit": page_size,
                "next_offset": (offset + page_size) if (offset + page_size) < len(sessions) else None,
            }
        if action == "channel.telegram.allowlist.reload":
            allow_from = self.channels.reload_telegram_allowlist(self._config_path)
            return {"ok": True, "allowFrom": allow_from, "count": len(allow_from)}
        raise ProtocolError(f"unsupported action: {action}")

    async def _handle_request_raw(
        self,
        raw: str,
        req_id: str | None,
        *,
        conn_id: str | None = None,
        role: str = "admin",
    ) -> dict[str, Any]:
        frame = decode_frame(raw)
        req_id, action, payload = validate_request(frame)
        result = await self._dispatch_action(action, payload, conn_id=conn_id, role=role)
        return response_frame(req_id, result)

    async def _handle_control_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn_id = str(uuid.uuid4())
        self._conn.add_control(conn_id, writer)
        peer = writer.get_extra_info("peername")
        self._log.info("control connected id=%s peer=%s", conn_id, peer)
        try:
            while not self._cancel.is_set():
                line = await reader.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").strip()
                req_id: str | None = None
                try:
                    out = await self._handle_request_raw(raw, req_id)
                except ProtocolError as e:
                    out = error_frame(req_id, "INVALID_REQUEST", str(e), retryable=False)
                except Exception as e:  # noqa: BLE001
                    out = error_frame(req_id, "INTERNAL_ERROR", str(e), retryable=False)
                writer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()
        finally:
            self._conn.remove(conn_id)
            writer.close()
            await writer.wait_closed()
            self._log.info("control disconnected id=%s", conn_id)

    async def _handle_ws_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn_id = str(uuid.uuid4())
        peer = writer.get_extra_info("peername")
        conn_cancel: asyncio.Event | None = None
        hb_task: asyncio.Task | None = None
        idle_task: asyncio.Task | None = None
        try:
            if not await accept_websocket(reader, writer, expected_path=self.path):
                self._log.info("websocket handshake rejected peer=%s", peer)
                return
            self._conn.add_ws(conn_id, writer)
            self._log.info("websocket connected id=%s peer=%s path=%s", conn_id, peer, self.path)
            conn_cancel = asyncio.Event()
            hb_task = asyncio.create_task(
                heartbeat_loop(
                    writer,
                    interval_sec=self._heartbeat_interval,
                    cancel=conn_cancel,
                )
            )

            def _last_seen() -> float:
                conn = self._conn.get_ws(conn_id)
                return conn.last_seen_at if conn is not None else time.time()

            idle_task = asyncio.create_task(
                idle_guard_loop(
                    writer,
                    idle_timeout_sec=self._idle_timeout,
                    last_seen_at=_last_seen,
                    cancel=conn_cancel,
                )
            )
            await self._conn.broadcast_ws(
                event_frame("gateway.client_connected", {"conn_id": conn_id, "peer": str(peer)}),
                except_conn_id=conn_id,
            )
            while not self._cancel.is_set():
                try:
                    raw = await read_text_message(
                        reader, writer, max_bytes=self._max_message_bytes
                    )
                except MessageTooLargeError as e:
                    out = error_frame(
                        None, "MESSAGE_TOO_LARGE", str(e), retryable=False
                    )
                    await write_text_message(writer, json.dumps(out, ensure_ascii=False))
                    break
                except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
                    break
                if raw is None:
                    break
                self._conn.touch_ws(conn_id)
                text = raw.strip()
                if not text:
                    continue
                req_id: str | None = None
                role = self._conn.get_ws_role(conn_id)
                try:
                    out = await self._handle_request_raw(
                        text, req_id, conn_id=conn_id, role=role
                    )
                except ProtocolError as e:
                    out = error_frame(req_id, "INVALID_REQUEST", str(e), retryable=False)
                except Exception as e:  # noqa: BLE001
                    out = error_frame(req_id, "INTERNAL_ERROR", str(e), retryable=False)
                await write_text_message(writer, json.dumps(out, ensure_ascii=False))
        finally:
            if conn_cancel is not None:
                conn_cancel.set()
            for task in (hb_task, idle_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._conn.remove(conn_id)
            try:
                writer.close()
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError, OSError):  # noqa: BLE001
                pass
            self._log.info("websocket disconnected id=%s", conn_id)
            await self._conn.broadcast_ws(
                event_frame("gateway.client_disconnected", {"conn_id": conn_id, "peer": str(peer)}),
            )

    async def _start_ws_plane(self) -> None:
        self._ws_server = await asyncio.start_server(
            self._handle_ws_client,
            host=self.bind,
            port=self.port,
        )
        self._log.info("websocket listening on %s:%s%s", self.bind, self.port, self.path)

    async def _stop_ws_plane(self) -> None:
        if self._ws_server is None:
            return
        self._ws_server.close()
        await self._ws_server.wait_closed()
        self._ws_server = None

    async def _start_control_plane(self) -> None:
        control_port = self.port + 1
        self._control_server = await asyncio.start_server(
            self._handle_control_client,
            host=self.bind,
            port=control_port,
        )
        self._log.info("control plane listening on %s:%s", self.bind, control_port)

    async def _stop_control_plane(self) -> None:
        if self._control_server is None:
            return
        self._control_server.close()
        await self._control_server.wait_closed()
        self._control_server = None

    async def run(self) -> None:
        await self.channels.start_all()
        if self.scheduler is not None:
            await self.scheduler.start()
        await self._start_ws_plane()
        await self._start_control_plane()
        await self._conn.broadcast_ws(
            event_frame(
                "gateway.started",
                {"bind": self.bind, "port": self.port, "path": self.path},
            )
        )
        consumer = asyncio.create_task(self._consume_inbound())
        try:
            while not self._cancel.is_set():
                await asyncio.sleep(0.5)
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
            await self._conn.close_all_ws(reason="gateway run ended")
            await self._conn.broadcast_ws(
                event_frame("gateway.stopped", {"bind": self.bind, "port": self.port}),
                force=True,
            )
            await self._stop_control_plane()
            await self._stop_ws_plane()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def _on_signal() -> None:
            self._log.info("received shutdown signal")
            self._cancel.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                pass

    async def shutdown(self) -> None:
        self._cancel.set()
        await self._conn.close_all_ws(reason="shutdown requested")
        if self.scheduler is not None:
            await self.scheduler.stop()
        await self.channels.stop_all()
