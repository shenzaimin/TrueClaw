from dataclasses import dataclass

@dataclass
class PluginLoadResult:
    name: str
    status: str
    detail: str = ""
