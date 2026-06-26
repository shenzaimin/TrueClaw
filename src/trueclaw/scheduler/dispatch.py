from __future__ import annotations

from dataclasses import asdict

from trueclaw.bus.events import InboundMessageEvent
from trueclaw.scheduler.wake_context import WakeContext


def wake_to_inbound(wake: WakeContext, content: str) -> InboundMessageEvent:
    channel = wake.target_channel or "internal"
    chat_id = wake.target_chat_id or f"scheduler:{wake.name}"
    return InboundMessageEvent(
        channel=channel,
        sender_id=f"scheduler:{wake.source}",
        chat_id=chat_id,
        content=content,
        metadata={
            "wake_id": wake.wake_id,
            "wake": asdict(wake),
            "trace_id": wake.wake_id,
            "delivery": wake.delivery,
            "tool_profile": wake.tool_profile,
        },
        session_key_override=wake.target_session_id,
    )
