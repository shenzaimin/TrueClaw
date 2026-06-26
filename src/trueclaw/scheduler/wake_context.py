from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class WakeContext:
    wake_id: str
    source: Literal["cron", "interval", "webhook", "email", "manual", "plugin"]
    name: str
    schedule: str | None = None
    requested_at: float | None = None
    target_channel: str | None = None
    target_chat_id: str | None = None
    target_thread_id: str | None = None
    target_session_id: str | None = None
    delivery: Literal["fixed_target", "last_active", "drop"] = "fixed_target"
    priority: int = 100
    tool_profile: str | None = None
    allow_user_visible_reply: bool = True
    meta: dict[str, Any] = field(default_factory=dict)
