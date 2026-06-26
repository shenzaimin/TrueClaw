from __future__ import annotations

import asyncio
import json

from trueclaw.channels.registry import discover_all
from trueclaw.config.loader import load_config
from trueclaw.config.validate import validate_config
from trueclaw.gateway.instance_lock import GatewayInstanceLock
from trueclaw.llm.factory import provider_summary
from trueclaw.plugins.loader import PluginLoader
from trueclaw.scheduler.leader_factory import leader_holder_detail
from trueclaw.tools.bootstrap import build_tool_registry
from trueclaw.tools.sync_async import run_sync_coro


def cmd_doctor(config_path: str, as_json: bool) -> int:
    checks = []
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
        checks.append({"id": "config", "ok": True, "detail": "valid"})
        checks.append(
            {
                "id": "gateway",
                "ok": True,
                "detail": (
                    f"ws={cfg.gateway.bind}:{cfg.gateway.port}{cfg.gateway.path} "
                    f"ctl_port={cfg.gateway.port + 1} "
                    f"heartbeat={cfg.gateway.heartbeatIntervalSec}s "
                    f"idle={cfg.gateway.idleTimeoutSec}s"
                ),
            }
        )
        checks.append({"id": "channels", "ok": True, "detail": f"discovered={len(discover_all())}"})
        webhook_section = cfg.channels.get("webhook")
        checks.append(_webhook_doctor_check(webhook_section))
        checks.append(asyncio.run(_webhook_health_probe(webhook_section)))
        checks.append(_scheduler_doctor_check(cfg.scheduler))
        checks.append(_scheduler_leader_doctor_check(config_path, cfg.scheduler))
        checks.append(_tools_doctor_check(cfg))
        checks.append(_llm_doctor_check(cfg))
        checks.append(_session_doctor_check(cfg))
        checks.append(_telegram_doctor_check(cfg))
        checks.append(_mcp_doctor_check(cfg))
        plugin_results = PluginLoader().discover_and_load()
        checks.append({"id": "plugins", "ok": True, "detail": f"discovered={len(plugin_results)}"})
        checks.append(_instance_lock_doctor_check(config_path, cfg.gateway))
    except Exception as e:
        checks.append({"id": "config", "ok": False, "detail": str(e)})

    ok = all(c["ok"] for c in checks)
    result = {"ok": ok, "checks": checks}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for c in checks:
            print(("✅" if c["ok"] else "❌"), c["id"], c["detail"])
    return 0 if ok else 3


def _webhook_doctor_check(section: object) -> dict[str, object]:
    if not isinstance(section, dict):
        return {"id": "webhook", "ok": True, "detail": "not configured"}
    if not bool(section.get("enabled", False)):
        return {"id": "webhook", "ok": True, "detail": "disabled"}
    if not bool(section.get("inboundEnabled", False)):
        return {"id": "webhook", "ok": True, "detail": "inbound disabled"}

    host = str(section.get("listenHost", "")).strip()
    path = str(section.get("path", "")).strip()
    port = section.get("listenPort", 0)
    problems: list[str] = []
    if not host:
        problems.append("listenHost empty")
    if not isinstance(port, int) or port <= 0 or port > 65535:
        problems.append("listenPort invalid")
    if not path.startswith("/"):
        problems.append("path must start with '/'")
    if problems:
        return {"id": "webhook", "ok": False, "detail": "; ".join(problems)}

    sign = str(section.get("signingSecret", "")).strip()
    token = str(section.get("verifyToken", "")).strip()
    mode = "signature+token" if sign and token else ("signature-only" if sign else ("token-only" if token else "open"))
    health_path = f"{path.rstrip('/')}/health"
    return {
        "id": "webhook",
        "ok": True,
        "detail": f"inbound {host}:{port}{path} health={health_path} mode={mode}",
    }


def _session_doctor_check(cfg) -> dict[str, object]:
    s = cfg.session
    return {
        "id": "session",
        "ok": True,
        "detail": (
            f"maxMessages={s.maxMessages} maxPromptTokensEst={s.maxPromptTokensEst} "
            f"storeCap={s.maxMessagesPerSession}"
        ),
    }


def _instance_lock_doctor_check(config_path: str, gateway_cfg) -> dict[str, object]:
    if not bool(getattr(gateway_cfg, "instanceLock", True)):
        return {"id": "instance_lock", "ok": True, "detail": "disabled"}
    lock_path = GatewayInstanceLock.lock_path_for(config_path, int(gateway_cfg.port))
    if not lock_path.exists():
        return {"id": "instance_lock", "ok": True, "detail": f"free path={lock_path}"}
    holder = GatewayInstanceLock.read_holder(lock_path)
    return {
        "id": "instance_lock",
        "ok": True,
        "detail": f"lock_file={lock_path} holder={holder} (may be stale if process died)",
    }


def _llm_doctor_check(cfg) -> dict[str, object]:
    name = cfg.agents["defaults"].provider
    if name == "mock":
        return {"id": "llm", "ok": True, "detail": "provider=mock (offline)"}
    pcfg = cfg.providers.get(name)
    if pcfg is None:
        return {"id": "llm", "ok": False, "detail": f"provider not found: {name}"}
    ok = bool(pcfg.apiKey)
    return {
        "id": "llm",
        "ok": ok,
        "detail": f"provider={name} {provider_summary(pcfg)}",
    }


def _telegram_doctor_check(cfg) -> dict[str, object]:
    section = cfg.channels.get("telegram")
    if not isinstance(section, object) or not bool(getattr(section, "enabled", False)):
        return {"id": "telegram", "ok": True, "detail": "disabled"}
    token = bool(str(getattr(section, "botToken", "")).strip())
    allow = getattr(section, "allowFrom", []) or []
    if not token:
        return {"id": "telegram", "ok": False, "detail": "enabled but botToken empty"}
    warn_allow = "" if allow else " allowFrom empty"
    return {
        "id": "telegram",
        "ok": True,
        "detail": f"enabled token={'set' if token else 'missing'} allowFrom={len(allow)}{warn_allow}",
    }


def _mcp_doctor_check(cfg) -> dict[str, object]:
    from trueclaw.tools.mcp.config import parse_mcp_servers

    servers = [s for s in parse_mcp_servers(cfg.mcp) if s.enabled]
    if not servers:
        return {"id": "mcp", "ok": True, "detail": "no servers enabled"}
    registry, router = build_tool_registry(cfg)
    mcp_tools = [n for n in registry.names() if n.startswith("mcp__")]
    detail = f"servers={len(servers)} tools={len(mcp_tools)}"
    if router is not None:
        try:
            health = run_sync_coro(router.health_snapshot())
            detail += f" health={health}"
        except Exception as e:  # noqa: BLE001
            detail += f" health_error={e}"
    return {"id": "mcp", "ok": len(mcp_tools) > 0, "detail": detail}


def _tools_doctor_check(cfg) -> dict[str, object]:
    registry, _router = build_tool_registry(cfg)
    names = registry.names()
    ws = str(cfg.tools.workspaceDir)
    return {"id": "tools", "ok": True, "detail": f"registered={len(names)} workspace={ws}"}


def _scheduler_leader_doctor_check(config_path: str, scheduler_cfg) -> dict[str, object]:
    if str(getattr(scheduler_cfg, "mode", "off")) != "inprocess":
        return {"id": "scheduler_leader", "ok": True, "detail": "mode not inprocess"}
    if not bool(getattr(scheduler_cfg, "leaderLock", True)):
        return {"id": "scheduler_leader", "ok": True, "detail": "leaderLock disabled"}
    return {"id": "scheduler_leader", "ok": True, "detail": leader_holder_detail(config_path, scheduler_cfg)}


def _scheduler_doctor_check(scheduler) -> dict[str, object]:
    mode = getattr(scheduler, "mode", "off")
    tasks = getattr(scheduler, "tasks", [])
    enabled = sum(1 for t in tasks if getattr(t, "enabled", False))
    if mode == "off":
        return {"id": "scheduler", "ok": True, "detail": "mode=off"}
    if mode != "inprocess":
        return {"id": "scheduler", "ok": False, "detail": f"unsupported mode={mode}"}
    return {"id": "scheduler", "ok": True, "detail": f"mode=inprocess tasks={len(tasks)} enabled={enabled}"}


async def _webhook_health_probe(section: object) -> dict[str, object]:
    if not isinstance(section, dict):
        return {"id": "webhook_health", "ok": True, "detail": "skipped"}
    if not bool(section.get("enabled", False)) or not bool(section.get("inboundEnabled", False)):
        return {"id": "webhook_health", "ok": True, "detail": "skipped"}

    host = str(section.get("listenHost", "127.0.0.1")).strip()
    port = int(section.get("listenPort", 18890))
    path = str(section.get("path", "/webhook")).strip() or "/webhook"
    health_path = f"{path.rstrip('/')}/health"
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
        req = (
            f"GET {health_path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(req)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4096), timeout=1.0)
        writer.close()
        await writer.wait_closed()
        text = raw.decode("utf-8", errors="replace")
        status_line = text.split("\r\n", 1)[0]
        if "200" not in status_line:
            return {"id": "webhook_health", "ok": False, "detail": f"unexpected status: {status_line}"}
        body = text.split("\r\n\r\n", 1)[-1]
        payload = json.loads(body) if body else {}
        mode = payload.get("auth_mode", "unknown")
        return {"id": "webhook_health", "ok": True, "detail": f"reachable {host}:{port}{health_path} mode={mode}"}
    except (ConnectionRefusedError, TimeoutError, asyncio.TimeoutError, OSError) as e:
        return {"id": "webhook_health", "ok": True, "detail": f"not running ({e})"}
    except Exception as e:  # noqa: BLE001
        return {"id": "webhook_health", "ok": False, "detail": f"probe failed: {e}"}
