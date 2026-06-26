from __future__ import annotations

import asyncio
import fcntl
import os
import time
from pathlib import Path


class SchedulerLeaderError(RuntimeError):
    """无法成为调度 leader。"""


class SchedulerLeaderLock:
    """基于文件锁 + 租约时间的调度 leader（第 15 章 MVP）。"""

    def __init__(self, path: Path, *, ttl_sec: float = 20.0) -> None:
        self.path = path
        self.ttl_sec = max(5.0, ttl_sec)
        self._fd: int | None = None
        self._file = None
        self._is_leader = False

    @staticmethod
    def lock_path_for(config_path: str) -> Path:
        expanded = Path(os.path.expanduser(config_path)).resolve()
        if expanded.suffix == ".json":
            return expanded.parent / "scheduler-leader.lock"
        return Path(os.path.expanduser("~/.trueclaw")) / "scheduler-leader.lock"

    @staticmethod
    def read_holder(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def _write_lease(self) -> None:
        if self._file is None:
            return
        expires = time.time() + self.ttl_sec
        self._file.seek(0)
        self._file.truncate()
        self._file.write(f"pid={os.getpid()}\nexpires_at={expires:.3f}\n")
        self._file.flush()

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115
        self._fd = self._file.fileno()
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            self._fd = None
            return False
        self._write_lease()
        self._is_leader = True
        return True

    def renew(self) -> bool:
        if not self._is_leader or self._file is None or self._fd is None:
            return False
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._is_leader = False
            return False
        self._write_lease()
        return True

    def release(self) -> None:
        if self._file is None or self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self._fd = None
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
