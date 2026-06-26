from __future__ import annotations

import os

from trueclaw.config.schema import SchedulerConfig
from trueclaw.scheduler.leader import SchedulerLeaderLock
from trueclaw.scheduler.redis_leader import RedisSchedulerLeader


def make_scheduler_leader(config_path: str, cfg: SchedulerConfig):
    if cfg.mode != "inprocess" or not cfg.leaderLock:
        return None
    ttl = float(cfg.leaderLockTtlSec)
    backend = str(getattr(cfg, "leaderBackend", "file") or "file").lower()
    if backend == "redis":
        url = str(getattr(cfg, "redisUrl", "") or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
        key = RedisSchedulerLeader.lock_path_for(config_path)
        return RedisSchedulerLeader(url, key=key, ttl_sec=ttl)
    return SchedulerLeaderLock(SchedulerLeaderLock.lock_path_for(config_path), ttl_sec=ttl)


def leader_holder_detail(config_path: str, cfg: SchedulerConfig) -> str:
    backend = str(getattr(cfg, "leaderBackend", "file") or "file").lower()
    if backend == "redis":
        url = str(getattr(cfg, "redisUrl", "") or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
        key = RedisSchedulerLeader.lock_path_for(config_path)
        holder = RedisSchedulerLeader.read_holder(url, key)
        return f"backend=redis key={key} holder={holder}"
    path = SchedulerLeaderLock.lock_path_for(config_path)
    if not path.exists():
        return f"backend=file free path={path}"
    holder = SchedulerLeaderLock.read_holder(path)
    return f"backend=file path={path} holder={holder}"
