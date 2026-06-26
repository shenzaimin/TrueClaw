from __future__ import annotations

from trueclaw.bus.events import InboundMessageEvent


def normalize_session_key(key: str) -> str:
    return key.strip()


def build_session_id(channel: str, chat_id: str, thread_id: str | None = None) -> str:
    thread = thread_id or "-"
    return f"{channel}:{chat_id}:{thread}"


def thread_id_from_metadata(metadata: dict) -> str | None:
    raw = metadata.get("thread_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def session_id_for_inbound(msg: InboundMessageEvent) -> str:
    if msg.session_key_override:
        return normalize_session_key(msg.session_key_override)
    thread_id = thread_id_from_metadata(msg.metadata)
    return build_session_id(msg.channel, msg.chat_id, thread_id)


def parse_session_id(session_id: str) -> tuple[str, str, str]:
    parts = session_id.split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], "-"
    return session_id, "-", "-"
