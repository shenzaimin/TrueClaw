from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from dataclasses import dataclass, field

from trueclaw.gateway.websocket import write_text_message


@dataclass
class WsConnection:
    writer: asyncio.StreamWriter
    subscriptions: set[str] = field(default_factory=set)
    role: str = "admin"
    last_seen_at: float = field(default_factory=time.time)


@dataclass
class ConnectionManager:
    ws_connections: dict[str, WsConnection] = field(default_factory=dict)
    control_writers: dict[str, asyncio.StreamWriter] = field(default_factory=dict)

    def add_ws(self, conn_id: str, writer: asyncio.StreamWriter) -> None:
        self.ws_connections[conn_id] = WsConnection(writer=writer)

    def add_control(self, conn_id: str, writer: asyncio.StreamWriter) -> None:
        self.control_writers[conn_id] = writer

    def get_ws(self, conn_id: str) -> WsConnection | None:
        return self.ws_connections.get(conn_id)

    def touch_ws(self, conn_id: str) -> None:
        conn = self.ws_connections.get(conn_id)
        if conn is not None:
            conn.last_seen_at = time.time()

    def set_ws_role(self, conn_id: str, role: str) -> None:
        conn = self.ws_connections.get(conn_id)
        if conn is not None:
            conn.role = role

    def get_ws_role(self, conn_id: str) -> str:
        conn = self.ws_connections.get(conn_id)
        return conn.role if conn is not None else "admin"

    def subscribe(self, conn_id: str, patterns: list[str]) -> None:
        conn = self.ws_connections.get(conn_id)
        if conn is None:
            return
        for p in patterns:
            p = str(p).strip()
            if p:
                conn.subscriptions.add(p)

    def unsubscribe(self, conn_id: str, patterns: list[str]) -> None:
        conn = self.ws_connections.get(conn_id)
        if conn is None:
            return
        for p in patterns:
            conn.subscriptions.discard(str(p).strip())

    def remove(self, conn_id: str) -> None:
        self.ws_connections.pop(conn_id, None)
        self.control_writers.pop(conn_id, None)

    def count(self) -> int:
        return len(self.ws_connections) + len(self.control_writers)

    def ws_count(self) -> int:
        return len(self.ws_connections)

    @staticmethod
    def _event_matches(event_type: str, patterns: set[str]) -> bool:
        if not patterns:
            return True
        for pattern in patterns:
            if fnmatch.fnmatch(event_type, pattern):
                return True
        return False

    async def broadcast_ws(
        self,
        frame: dict,
        *,
        except_conn_id: str | None = None,
        force: bool = False,
    ) -> int:
        if not self.ws_connections:
            return 0
        event_type = str(frame.get("event", ""))
        text = json.dumps(frame, ensure_ascii=False)
        dead: list[str] = []
        sent = 0
        for conn_id, conn in list(self.ws_connections.items()):
            if except_conn_id and conn_id == except_conn_id:
                continue
            if not force and not self._event_matches(event_type, conn.subscriptions):
                continue
            try:
                await write_text_message(conn.writer, text)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(conn_id)
        for conn_id in dead:
            self.ws_connections.pop(conn_id, None)
        return sent

    async def close_all_ws(self, *, reason: str = "gateway stopping", timeout_sec: float = 5.0) -> None:
        if not self.ws_connections:
            return
        stopping = {
            "type": "event",
            "event": "gateway.stopping",
            "payload": {"reason": reason},
        }
        await self.broadcast_ws(stopping, force=True)
        writers = [c.writer for c in self.ws_connections.values()]
        self.ws_connections.clear()
        for writer in writers:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=timeout_sec)
            except Exception:  # noqa: BLE001
                pass
