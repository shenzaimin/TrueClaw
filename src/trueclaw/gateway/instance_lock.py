from __future__ import annotations

import fcntl
import os
from pathlib import Path


class InstanceLockError(RuntimeError):
    """另一网关实例已持有锁。"""


class GatewayInstanceLock:
    """基于 fcntl 的网关单实例文件锁（第 14 章）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None
        self._file = None

    @staticmethod
    def lock_path_for(config_path: str, port: int) -> Path:
        expanded = Path(os.path.expanduser(config_path)).resolve()
        if expanded.suffix == ".json":
            return expanded.parent / f"gateway-{port}.lock"
        return Path(os.path.expanduser("~/.trueclaw")) / f"gateway-{port}.lock"

    @staticmethod
    def read_holder(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115
        self._fd = self._file.fileno()
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            holder = self.read_holder(self.path)
            raise InstanceLockError(
                f"gateway already running (lock={self.path}, holder={holder})"
            ) from e
        self._file.seek(0)
        self._file.truncate()
        self._file.write(f"pid={os.getpid()}\nport={self.path.stem}\n")
        self._file.flush()

    def release(self) -> None:
        if self._file is None or self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self._fd = None

    def __enter__(self) -> GatewayInstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
