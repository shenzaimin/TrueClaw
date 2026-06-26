from __future__ import annotations

import importlib
import pkgutil

from trueclaw.plugins.discovery import entry_point_row, iter_channel_entry_points, load_channel_entry

_INTERNAL = frozenset({"base", "manager", "registry", "models", "telegram_adapter", "delta_coalescer"})


def discover_channel_names() -> list[str]:
    import trueclaw.channels as pkg

    return [
        name
        for _, name, ispkg in pkgutil.iter_modules(pkg.__path__)
        if name not in _INTERNAL and not ispkg
    ]


def load_channel_class(module_name: str):
    from trueclaw.channels.base import BaseChannel

    mod = importlib.import_module(f"trueclaw.channels.{module_name}")
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if isinstance(obj, type) and issubclass(obj, BaseChannel) and obj is not BaseChannel:
            return obj
    raise ImportError(f"No BaseChannel subclass in trueclaw.channels.{module_name}")


def discover_plugins() -> dict[str, type]:
    plugins: dict[str, type] = {}
    for ep in iter_channel_entry_points():
        row = entry_point_row(ep)
        try:
            cls = load_channel_entry(ep)
            plugins[row["name"]] = cls
        except Exception:
            continue
    return plugins


def discover_all() -> dict[str, type]:
    builtin: dict[str, type] = {}
    for modname in discover_channel_names():
        try:
            builtin[modname] = load_channel_class(modname)
        except Exception:
            continue
    external = discover_plugins()
    return {**external, **builtin}
