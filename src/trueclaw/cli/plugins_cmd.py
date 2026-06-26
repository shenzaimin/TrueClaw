from __future__ import annotations

import json

from trueclaw.channels.registry import discover_all, discover_channel_names
from trueclaw.plugins.loader import PluginLoader


def cmd_plugins_list() -> int:
    all_channels = discover_all()
    builtins = set(discover_channel_names())
    if not all_channels:
        print("No channels discovered")
        return 0
    for name in sorted(all_channels):
        source = "builtin" if name in builtins else "plugin"
        print(f"{name}\t{source}\t{all_channels[name].__name__}")
    return 0


def cmd_plugins_doctor() -> int:
    loader = PluginLoader()
    entries = loader.list_entries()
    results = {r.name: r for r in loader.discover_and_load()}
    if not entries:
        print("No plugin entry points found (group=trueclaw.plugins.channel)")
        return 0
    for e in entries:
        res = results.get(e["name"])
        if res is None:
            print(f"{e['name']}\tUNKNOWN\t{e['value']}")
        else:
            print(f"{e['name']}\t{res.status}\t{res.detail or e['value']}")
    return 0


def cmd_plugins_explain(name: str) -> int:
    loader = PluginLoader()
    row = loader.explain(name)
    if row is None:
        print(f"Plugin entry not found: {name}")
        return 1
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0
