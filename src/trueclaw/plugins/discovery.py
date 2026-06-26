from __future__ import annotations

import importlib
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Iterator

from trueclaw.plugins.resolver import resolve_channel_class

CHANNEL_ENTRY_GROUP = "trueclaw.plugins.channel"

# PYTHONPATH=src 开发时无 entry_points 元数据，回退扫描内置示例插件。
_BUNDLED_CHANNEL_PLUGINS: tuple[tuple[str, str, str], ...] = (
    ("echo", "trueclaw_plugins.echo.channel", "EchoChannel"),
    ("slack", "trueclaw_plugins.slack.channel", "SlackChannel"),
)


def iter_channel_entry_points() -> Iterator[EntryPoint | dict[str, str]]:
    seen: set[str] = set()
    for ep in entry_points(group=CHANNEL_ENTRY_GROUP):
        seen.add(ep.name)
        yield ep
    for name, module, attr in _BUNDLED_CHANNEL_PLUGINS:
        if name in seen:
            continue
        yield {
            "name": name,
            "value": f"{module}:{attr}",
            "module": module,
            "attr": attr,
            "group": CHANNEL_ENTRY_GROUP,
        }


def entry_point_row(ep: EntryPoint | dict[str, str]) -> dict[str, str]:
    if isinstance(ep, dict):
        return ep
    return {
        "name": ep.name,
        "value": str(ep.value),
        "module": getattr(ep, "module", ""),
        "attr": getattr(ep, "attr", ""),
        "group": CHANNEL_ENTRY_GROUP,
    }


def load_channel_entry(ep: EntryPoint | dict[str, str]) -> type:
    if isinstance(ep, dict):
        mod = importlib.import_module(ep["module"])
        loaded = getattr(mod, ep["attr"])
    else:
        loaded = ep.load()
    return resolve_channel_class(loaded)


def load_channel_by_name(name: str) -> type | None:
    for ep in iter_channel_entry_points():
        row = entry_point_row(ep)
        if row["name"] == name:
            try:
                return load_channel_entry(ep)
            except Exception:
                return None
    return None
