from __future__ import annotations

import asyncio
import os
import socket
import time
from urllib.parse import urlparse


class RedisSchedulerLeader:
    """Redis SET NX EX 选主（标准库 RESP，无 redis 包依赖）。"""

    def __init__(self, redis_url: str, *, key: str, ttl_sec: float = 20.0) -> None:
        self.redis_url = redis_url
        self.key = key
        self.ttl_sec = max(5, int(ttl_sec))
        self._holder = f"pid={os.getpid()}@{socket.gethostname()}"
        self._is_leader = False
        parsed = urlparse(redis_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 6379
        self._db = 0
        if parsed.path and parsed.path != "/":
            try:
                self._db = int(parsed.path.lstrip("/"))
            except ValueError:
                self._db = 0

    @staticmethod
    def lock_path_for(config_path: str) -> str:
        return f"trueclaw:scheduler:leader:{os.path.basename(os.path.expanduser(config_path))}"

    @staticmethod
    def read_holder(redis_url: str, key: str) -> str:
        leader = RedisSchedulerLeader(redis_url, key=key, ttl_sec=20.0)
        try:
            val = leader._cmd(["GET", key])
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="replace") or "unknown"
            return str(val) if val else "free"
        except Exception:  # noqa: BLE001
            return "unreachable"

    def _send_resp(self, sock: socket.socket, parts: list[str]) -> None:
        payload = f"*{len(parts)}\r\n"
        for part in parts:
            b = part.encode("utf-8")
            payload += f"${len(b)}\r\n{part}\r\n"
        sock.sendall(payload.encode("utf-8"))

    def _read_resp(self, sock: socket.socket) -> object:
        sock.settimeout(2.0)
        data = sock.recv(4096)
        if not data:
            raise RuntimeError("redis empty response")
        text = data.decode("utf-8", errors="replace")
        if text.startswith("+"):
            return text[1:].split("\r\n", 1)[0]
        if text.startswith("-"):
            raise RuntimeError(text[1:].split("\r\n", 1)[0])
        if text.startswith(":"):
            return int(text[1:].split("\r\n", 1)[0])
        if text.startswith("$"):
            lines = text.split("\r\n")
            if lines[0] == "$-1":
                return None
            if len(lines) >= 2:
                return lines[1]
        return text

    def _cmd(self, parts: list[str]) -> object:
        with socket.create_connection((self._host, self._port), timeout=2.0) as sock:
            if self._db:
                self._send_resp(sock, ["SELECT", str(self._db)])
                self._read_resp(sock)
            self._send_resp(sock, parts)
            return self._read_resp(sock)

    def try_acquire(self) -> bool:
        result = self._cmd(["SET", self.key, self._holder, "NX", "EX", str(self._ttl_sec)])
        ok = result in ("OK", b"OK")
        self._is_leader = ok
        return ok

    def renew(self) -> bool:
        if not self._is_leader:
            return False
        current = self._cmd(["GET", self.key])
        holder = current.decode("utf-8", errors="replace") if isinstance(current, bytes) else str(current or "")
        if holder != self._holder:
            self._is_leader = False
            return False
        self._cmd(["EXPIRE", self.key, str(self._ttl_sec)])
        return True

    def release(self) -> None:
        if not self._is_leader:
            return
        current = self._cmd(["GET", self.key])
        holder = current.decode("utf-8", errors="replace") if isinstance(current, bytes) else str(current or "")
        if holder == self._holder:
            self._cmd(["DEL", self.key])
        self._is_leader = False

    async def renew_loop(self, cancel: asyncio.Event) -> None:
        interval = max(1.0, self.ttl_sec / 2.0)
        while not cancel.is_set():
            if not self.renew():
                break
            try:
                await asyncio.wait_for(cancel.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def path(self) -> str:
        return self.key
