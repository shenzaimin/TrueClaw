from __future__ import annotations

import json
import sys
from pathlib import Path

from trueclaw.channels.registry import discover_all
from trueclaw.config.loader import resolve_config_path


def example_config() -> dict:
    defaults = {
        "gateway": {"bind": "127.0.0.1", "port": 18789, "path": "/ws", "logLevel": "INFO", "streamCoalesceMs": 300},
        "providers": {
            "mock": {"apiKey": "", "apiBase": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "timeoutSec": 60},
            "openai": {"apiKey": "", "apiBase": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "timeoutSec": 60},
        },
        "agents": {"defaults": {"provider": "mock", "model": "gpt-4.1-mini", "maxTurns": 8, "streamReplies": True}},
        "channels": {
            "telegram": {
                "enabled": False,
                "botToken": "",
                "botUsername": "",
                "allowFrom": [],
                "groupPolicy": "mention_only",
                "pollIntervalSec": 1,
            }
        },
        "tools": {"enableFileRead": True, "workspaceDir": "./workspace", "maxToolCallsPerTurn": 4},
        "scheduler": {
            "mode": "off",
            "leaderBackend": "file",
            "tasks": [
                {
                    "name": "heartbeat",
                    "enabled": False,
                    "intervalSec": 300,
                    "wake": {
                        "delivery": "fixed_target",
                        "target_channel": "webhook",
                        "target_chat_id": "scheduler-heartbeat",
                        "tool_profile": "readonly",
                        "prompt": "[scheduled] heartbeat check",
                    },
                }
            ],
            "quiet_hours": {"tz": "Asia/Shanghai", "start": "23:00", "end": "07:00", "behavior": "drop"},
        },
        "session": {
            "maxMessages": 40,
            "maxPromptTokensEst": 8000,
            "reservedCompletionTokensEst": 2000,
            "maxMessagesPerSession": 200,
            "maxToolResultChars": 2000,
        },
        "plugins": {"enabled": [], "entries": []},
        "mcp": {"servers": {"demo": {"enabled": True, "transport": "mock"}}},
    }
    for name, cls in discover_all().items():
        defaults["channels"].setdefault(name, cls.default_config())
    return defaults


def cmd_init(config_path: str, force: bool) -> int:
    path = resolve_config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"config already exists: {path} (use --force)")
    path.write_text(json.dumps(example_config(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {path}")
    return 0


def cmd_config_validate(config_path: str) -> int:
    from trueclaw.config.loader import load_config
    from trueclaw.config.validate import validate_config

    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except Exception as e:  # noqa: BLE001
        print(str(e), file=sys.stderr)
        return 2
    print("OK")
    return 0


# 与第 14 章书中命名对齐
cmd_validate = cmd_config_validate


def cmd_config_print_path(config_path: str) -> int:
    print(resolve_config_path(config_path))
    return 0
