from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ActivityRecord:
    principal: str
    session_id: str
    channel: str
    chat_id: str
    thread_id: str | None
    updated_at: float


class MemoryActivityTracker:
    def __init__(self) -> None:
        self._by_principal: dict[str, ActivityRecord] = {}
        self._global_last: ActivityRecord | None = None

    def record(
        self,
        *,
        principal: str,
        session_id: str,
        channel: str,
        chat_id: str,
        thread_id: str | None = None,
    ) -> None:
        row = ActivityRecord(
            principal=principal,
            session_id=session_id,
            channel=channel,
            chat_id=chat_id,
            thread_id=thread_id,
            updated_at=time.time(),
        )
        self._by_principal[principal] = row
        self._global_last = row

    def resolve_last_active(self, principal: str | None = None) -> ActivityRecord | None:
        if principal and principal in self._by_principal:
            return self._by_principal[principal]
        return self._global_last
