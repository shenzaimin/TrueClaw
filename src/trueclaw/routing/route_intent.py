from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteIntent:
    session_id: str
