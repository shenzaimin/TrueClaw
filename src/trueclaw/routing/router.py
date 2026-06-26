from __future__ import annotations

from trueclaw.bus.events import InboundMessageEvent
from trueclaw.routing.route_intent import RouteIntent
from trueclaw.session.keys import build_session_id, session_id_for_inbound

__all__ = ["Router", "build_session_id", "session_id_for_inbound"]


class Router:
    def route_event(self, msg: InboundMessageEvent) -> RouteIntent:
        return RouteIntent(session_id=session_id_for_inbound(msg))
