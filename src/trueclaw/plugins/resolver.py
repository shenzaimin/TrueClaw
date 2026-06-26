from __future__ import annotations

from typing import Any

from trueclaw.channels.base import BaseChannel


def resolve_channel_class(loaded: Any) -> type[BaseChannel]:
    """Normalize entry-point load results to a BaseChannel subclass."""
    if isinstance(loaded, type) and issubclass(loaded, BaseChannel):
        return loaded
    if isinstance(loaded, BaseChannel):
        return type(loaded)
    if callable(loaded) and not isinstance(loaded, type):
        return resolve_channel_class(loaded())
    raise TypeError(f"plugin entry must return BaseChannel subclass, got {type(loaded).__name__}")
