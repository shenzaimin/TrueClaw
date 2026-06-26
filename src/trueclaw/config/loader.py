from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trueclaw.config.schema import (
    AgentDefaults,
    AppConfig,
    GatewayConfig,
    ProviderConfig,
    SchedulerConfig,
    SchedulerTaskConfig,
    SessionConfig,
    TelegramConfig,
    ToolsConfig,
    WakeTaskWakeConfig,
)


def resolve_config_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _deep_update(target: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_update(target[k], v)
        else:
            target[k] = v
    return target


def _apply_env(raw: dict[str, Any]) -> dict[str, Any]:
    prefix = "TRUECLAW__"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        cur = raw
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    return raw


def load_raw_config(path: str) -> dict[str, Any]:
    cfg_path = resolve_config_path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    return _apply_env(raw)


def build_config(raw: dict[str, Any]) -> AppConfig:
    gw = GatewayConfig(**raw.get("gateway", {}))

    providers = {
        name: ProviderConfig(**cfg)
        for name, cfg in raw.get("providers", {"mock": {}}).items()
    }

    agents = {
        name: AgentDefaults(**cfg)
        for name, cfg in raw.get("agents", {"defaults": {}}).items()
    }

    channels: dict[str, object] = {}
    for name, cfg in raw.get("channels", {"telegram": {}}).items():
        if name == "telegram":
            channels[name] = TelegramConfig(**cfg)
        else:
            channels[name] = cfg

    tools = ToolsConfig(**raw.get("tools", {}))
    sched_raw = raw.get("scheduler", {})
    tasks = [
        SchedulerTaskConfig(
            name=str(t["name"]),
            enabled=bool(t.get("enabled", True)),
            cron=t.get("cron"),
            intervalSec=t.get("intervalSec"),
            wake=WakeTaskWakeConfig(**t.get("wake", {})),
        )
        for t in sched_raw.get("tasks", [])
        if isinstance(t, dict) and t.get("name")
    ]
    scheduler = SchedulerConfig(
        mode=str(sched_raw.get("mode", "off")),
        leaderLock=bool(sched_raw.get("leaderLock", True)),
        leaderLockTtlSec=float(sched_raw.get("leaderLockTtlSec", 20.0)),
        leaderBackend=str(sched_raw.get("leaderBackend", "file")),
        redisUrl=str(sched_raw.get("redisUrl", "")),
        tasks=tasks,
        quiet_hours=dict(sched_raw.get("quiet_hours", {})),
    )
    plugins = raw.get("plugins", {"enabled": [], "entries": []})
    session = SessionConfig(**raw.get("session", {}))
    mcp = raw.get("mcp", {"servers": {}})

    return AppConfig(
        gateway=gw,
        providers=providers,
        agents=agents,
        channels=channels,
        tools=tools,
        scheduler=scheduler,
        session=session,
        plugins=plugins,
        mcp=mcp if isinstance(mcp, dict) else {"servers": {}},
    )


def load_config(path: str) -> AppConfig:
    return build_config(load_raw_config(path))
