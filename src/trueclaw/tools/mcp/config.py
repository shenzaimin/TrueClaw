from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class McpServerConfig:
    name: str
    enabled: bool = False
    transport: str = "mock"  # mock | stdio | http
    command: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: float = 30.0


def parse_mcp_servers(raw: dict | None) -> list[McpServerConfig]:
    if not isinstance(raw, dict):
        return []
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        return []
    out: list[McpServerConfig] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        cmd = cfg.get("command", [])
        if isinstance(cmd, str):
            cmd = [cmd]
        out.append(
            McpServerConfig(
                name=str(name),
                enabled=bool(cfg.get("enabled", False)),
                transport=str(cfg.get("transport", "mock")),
                command=[str(x) for x in cmd],
                url=str(cfg.get("url", "")),
                headers={str(k): str(v) for k, v in (cfg.get("headers") or {}).items()},
                timeout_sec=float(cfg.get("timeoutSec", 30.0)),
            )
        )
    return out
