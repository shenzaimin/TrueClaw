from __future__ import annotations

from trueclaw.plugins.discovery import entry_point_row, iter_channel_entry_points, load_channel_entry
from trueclaw.plugins.models import PluginLoadResult


class PluginLoader:
    def __init__(self) -> None:
        self.results: list[PluginLoadResult] = []

    def discover_and_load(self) -> list[PluginLoadResult]:
        loaded: list[PluginLoadResult] = []
        for ep in iter_channel_entry_points():
            row = entry_point_row(ep)
            try:
                cls = load_channel_entry(ep)
                loaded.append(
                    PluginLoadResult(
                        name=row["name"],
                        status="LOADED",
                        detail=f"{cls.__module__}:{cls.__name__}",
                    )
                )
            except Exception as e:
                loaded.append(PluginLoadResult(name=row["name"], status="FAILED", detail=str(e)))
        self.results = loaded
        return self.results

    def list_entries(self) -> list[dict[str, str]]:
        return [entry_point_row(ep) for ep in iter_channel_entry_points()]

    def explain(self, name: str) -> dict[str, str] | None:
        for row in self.list_entries():
            if row["name"] == name:
                return row
        return None
