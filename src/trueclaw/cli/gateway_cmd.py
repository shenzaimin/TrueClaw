from __future__ import annotations

import asyncio
import json
import sys

from trueclaw.config.loader import load_config, resolve_config_path
from trueclaw.config.validate import validate_config
from trueclaw.agent.factory import make_agent_runner
from trueclaw.bus.queue import MessageBus
from trueclaw.channels.manager import ChannelManager
from trueclaw.gateway.instance_lock import GatewayInstanceLock, InstanceLockError
from trueclaw.gateway.server import GatewayServer
from trueclaw.observability.logging import setup_logging
from trueclaw.scheduler.engine import SchedulerEngine
from trueclaw.scheduler.leader_factory import make_scheduler_leader
from trueclaw.session.activity_tracker import MemoryActivityTracker
from trueclaw.tools.bootstrap import close_mcp_routers


def _print_ctl_pretty(action: str, frame: dict) -> None:
    if frame.get("type") != "response":
        print(json.dumps(frame, ensure_ascii=False))
        return
    payload = frame.get("payload", {})
    if action != "session.list":
        print(json.dumps(frame, ensure_ascii=False))
        return
    items = payload.get("items", [])
    count = payload.get("count", 0)
    limit = payload.get("limit", 0)
    offset = payload.get("offset", 0)
    next_offset = payload.get("next_offset")
    print(f"sessions total={count} page={len(items)} offset={offset} limit={limit}")
    for i, item in enumerate(items, start=1):
        sid = item.get("session_id", "")
        msg_count = item.get("message_count", 0)
        last_role = item.get("last_role", "-")
        print(f"{i:02d}. {sid} messages={msg_count} last_role={last_role}")
    if next_offset is not None:
        print(f"next_offset={next_offset}")


async def cmd_gateway_run(
    config_path: str,
    log_level: str,
    *,
    bind: str | None = None,
    port: int | None = None,
) -> int:
    cfg = load_config(config_path)
    if bind:
        cfg.gateway.bind = bind
    if port is not None:
        cfg.gateway.port = port
    validate_config(cfg)
    setup_logging(log_level or cfg.gateway.logLevel)

    lock: GatewayInstanceLock | None = None
    if cfg.gateway.instanceLock:
        lock = GatewayInstanceLock(GatewayInstanceLock.lock_path_for(config_path, cfg.gateway.port))
        try:
            lock.acquire()
        except InstanceLockError as e:
            print(str(e), file=sys.stderr)
            return 2

    scheduler_leader = make_scheduler_leader(config_path, cfg.scheduler)

    bus = MessageBus()
    channels = ChannelManager(cfg, bus, config_path=config_path)
    activity = MemoryActivityTracker()
    runner = make_agent_runner(cfg, activity_tracker=activity)
    scheduler = SchedulerEngine(
        cfg.scheduler,
        bus,
        activity_tracker=activity,
        leader=scheduler_leader,
        config_path=config_path,
    )
    server = GatewayServer(
        bind=cfg.gateway.bind,
        port=cfg.gateway.port,
        path=cfg.gateway.path,
        bus=bus,
        channels=channels,
        runner=runner,
        scheduler=scheduler,
        push_outbound_events=cfg.gateway.pushOutboundEvents,
        gateway_cfg=cfg.gateway,
        config_path=config_path,
    )
    server.install_signal_handlers()

    try:
        await server.run()
    except KeyboardInterrupt:
        pass
    finally:
        await server.shutdown()
        close_mcp_routers()
        if lock is not None:
            lock.release()
    return 0


# 与第 14 章书中命名对齐
cmd_run = cmd_gateway_run


async def cmd_gateway_ctl(
    config_path: str,
    host: str | None,
    port: int | None,
    action: str,
    payload_json: str,
    limit: int | None,
    offset: int | None,
) -> int:
    cfg = load_config(config_path)
    validate_config(cfg)
    host = host or cfg.gateway.bind
    port = port or (cfg.gateway.port + 1)
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise ValueError("--payload must be a JSON object")
    if action == "session.list":
        if limit is not None:
            payload.setdefault("limit", limit)
        if offset is not None:
            payload.setdefault("offset", offset)
    reader, writer = await asyncio.open_connection(host, port)
    req = {"type": "request", "id": "cli-1", "action": action, "payload": payload}
    writer.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    if not line:
        raise RuntimeError("empty control-plane response")
    raw = line.decode("utf-8", errors="replace").strip()
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return 0
    _print_ctl_pretty(action, frame)
    return 0
