from __future__ import annotations

import os
import time
from pathlib import Path

from trueclaw.scheduler.redis_leader import RedisSchedulerLeader


class WakeIdStore:
    """wake_id 幂等门闩：进程内 + 可选文件/Redis 持久化（第 15 章）。"""

    def __init__(
        self,
        *,
        config_path: str,
        backend: str = "file",
        redis_url: str = "",
        ttl_sec: float = 3600.0,
    ) -> None:
        self.backend = backend
        self.ttl_sec = max(60.0, ttl_sec)
        self._memory: dict[str, float] = {}
        self._file_path = self._file_path_for(config_path)
        self._redis_key_prefix = f"trueclaw:wake_id:{os.path.basename(os.path.expanduser(config_path))}:"
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    @staticmethod
    def _file_path_for(config_path: str) -> Path:
        expanded = Path(os.path.expanduser(config_path)).resolve()
        if expanded.suffix == ".json":
            return expanded.parent / "runtime" / "wake_ids.json"
        return Path(os.path.expanduser("~/.trueclaw/runtime/wake_ids.json"))

    def _prune_memory(self) -> None:
        now = time.time()
        stale = [k for k, exp in self._memory.items() if exp <= now]
        for k in stale:
            del self._memory[k]

    def _redis_claim(self, wake_id: str) -> bool:
        key = f"{self._redis_key_prefix}{wake_id}"
        leader = RedisSchedulerLeader(self._redis_url, key=key, ttl_sec=self.ttl_sec)
        return leader.try_acquire()

    def _file_claim(self, wake_id: str) -> bool:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        entries: dict[str, float] = {}
        if self._file_path.exists():
            try:
                import json

                raw = json.loads(self._file_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    entries = {str(k): float(v) for k, v in raw.items()}
            except Exception:  # noqa: BLE001
                entries = {}
        entries = {k: v for k, v in entries.items() if v > now}
        if wake_id in entries:
            return False
        entries[wake_id] = now + self.ttl_sec
        import json

        self._file_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return True

    def try_claim(self, wake_id: str) -> bool:
        self._prune_memory()
        if wake_id in self._memory and self._memory[wake_id] > time.time():
            return False
        if self.backend == "redis":
            if not self._redis_claim(wake_id):
                return False
        elif self.backend == "file":
            if not self._file_claim(wake_id):
                return False
        self._memory[wake_id] = time.time() + self.ttl_sec
        return True
