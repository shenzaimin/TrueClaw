from __future__ import annotations

ACTION_ROLES: dict[str, set[str]] = {
    "gateway.ping": {"viewer", "operator", "admin"},
    "gateway.stats": {"operator", "admin"},
    "gateway.metrics": {"operator", "admin"},
    "gateway.subscribe": {"operator", "admin"},
    "gateway.unsubscribe": {"operator", "admin"},
    "session.list": {"operator", "admin"},
    "scheduler.list": {"operator", "admin"},
    "channel.telegram.allowlist.reload": {"admin"},
}


def check_action_role(action: str, role: str) -> bool:
    allowed = ACTION_ROLES.get(action, {"admin"})
    return role in allowed
