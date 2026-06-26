from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionMessage:
    role: str
    text: str


@dataclass
class SessionState:
    session_id: str
    messages: list[SessionMessage] = field(default_factory=list)


class SessionStore:
    async def get_or_create(self, session_id: str) -> SessionState:
        raise NotImplementedError

    async def list_sessions(self) -> list[SessionState]:
        raise NotImplementedError
