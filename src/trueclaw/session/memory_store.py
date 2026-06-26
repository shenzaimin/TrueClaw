from __future__ import annotations

from trueclaw.session.store import SessionState, SessionStore


class MemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}

    async def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._states:
            self._states[session_id] = SessionState(session_id=session_id)
        return self._states[session_id]

    async def list_sessions(self) -> list[SessionState]:
        return list(self._states.values())
