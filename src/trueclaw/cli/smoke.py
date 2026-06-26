from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trueclaw.agent.factory import make_agent_runner
from trueclaw.bus.queue import MessageBus
from trueclaw.channels.manager import ChannelManager
from trueclaw.config.loader import build_config, load_raw_config
from trueclaw.config.schema import AppConfig
from trueclaw.config.validate import validate_config
from trueclaw.gateway.server import GatewayServer
from trueclaw.gateway.websocket import ws_collect_events, ws_json_request
from trueclaw.observability.logging import setup_logging
from trueclaw.scheduler.engine import SchedulerEngine
from trueclaw.tools.bootstrap import build_tool_registry_async, close_mcp_routers_async


@dataclass
class SmokeStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SmokeReport:
    ok: bool
    steps: list[SmokeStep] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append(SmokeStep(name=name, ok=ok, detail=detail))


async def _read_http_response(reader: asyncio.StreamReader) -> tuple[str, bytes]:
    raw = await reader.read(65536)
    text = raw.decode("utf-8", errors="replace")
    status_line = text.split("\r\n", 1)[0]
    body = text.split("\r\n\r\n", 1)[-1].encode("utf-8") if "\r\n\r\n" in text else b""
    return status_line, body


async def _http_get(host: str, port: int, path: str) -> tuple[str, dict[str, Any]]:
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8")
    writer.write(req)
    await writer.drain()
    status_line, body = await _read_http_response(reader)
    writer.close()
    await writer.wait_closed()
    payload: dict[str, Any] = {}
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")}
    return status_line, payload


async def _http_post_json(host: str, port: int, path: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + body
    writer.write(req)
    await writer.drain()
    status_line, resp_body = await _read_http_response(reader)
    writer.close()
    await writer.wait_closed()
    parsed: dict[str, Any] = {}
    if resp_body:
        try:
            parsed = json.loads(resp_body.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = {"raw": resp_body.decode("utf-8", errors="replace")}
    return status_line, parsed


async def _ctl_action(host: str, port: int, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection(host, port)
    req = {"type": "request", "id": "smoke-1", "action": action, "payload": payload}
    writer.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    if not line:
        raise RuntimeError("empty control-plane response")
    return json.loads(line.decode("utf-8", errors="replace"))


def _smoke_config(
    raw: dict[str, Any],
    *,
    gw_port: int,
    webhook_port: int,
    outbound_url: str,
    workspace_dir: str | None = None,
) -> AppConfig:
    raw = json.loads(json.dumps(raw))
    raw.setdefault("channels", {})
    raw["gateway"]["port"] = gw_port
    raw["channels"]["webhook"] = {
        "enabled": True,
        "inboundEnabled": True,
        "listenHost": "127.0.0.1",
        "listenPort": webhook_port,
        "path": "/webhook",
        "verifyToken": "",
        "signingSecret": "",
        "outboundUrl": outbound_url,
        "outboundAuthHeader": "",
    }
    raw["channels"].setdefault("telegram", {"enabled": False})
    if isinstance(raw["channels"].get("telegram"), dict):
        raw["channels"]["telegram"]["enabled"] = False
    if workspace_dir:
        raw.setdefault("tools", {})
        raw["tools"]["workspaceDir"] = workspace_dir
        raw["tools"]["enableFileRead"] = True
    cfg = build_config(raw)
    validate_config(cfg)
    return cfg


async def _run_gateway_smoke_loop(
    config_path: str,
    *,
    gw_port: int,
    webhook_port: int,
    inbound_payload: dict[str, Any],
    assert_outbound: Any,
    extra_steps: list[tuple[str, Any]] | None = None,
    workspace_dir: str | None = None,
    log_level: str = "WARNING",
    check_ws_outbound_event: bool = True,
) -> SmokeReport:
    setup_logging(log_level)
    report = SmokeReport(ok=False)
    raw = load_raw_config(config_path)
    host = "127.0.0.1"
    captured: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    capture_server: asyncio.AbstractServer | None = None
    gateway_task: asyncio.Task | None = None
    server: GatewayServer | None = None

    async def _capture_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            headers: dict[str, str] = {}
            while True:
                hline = await reader.readline()
                if hline in (b"\r\n", b"\n", b""):
                    break
                text = hline.decode("utf-8", errors="replace")
                if ":" in text:
                    k, v = text.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", "0") or "0")
            body = await reader.readexactly(length) if length > 0 else b""
            payload = json.loads(body.decode("utf-8")) if body else {}
            await captured.put(payload)
            resp = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
            writer.write(resp)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        capture_server = await asyncio.start_server(_capture_client, host=host, port=0)
        outbound_port = capture_server.sockets[0].getsockname()[1]
        outbound_url = f"http://{host}:{outbound_port}/callback"

        cfg = _smoke_config(
            raw,
            gw_port=gw_port,
            webhook_port=webhook_port,
            outbound_url=outbound_url,
            workspace_dir=workspace_dir,
        )
        bus = MessageBus()
        channels = ChannelManager(cfg, bus)
        runner = make_agent_runner(cfg)
        server = GatewayServer(
            bind=host,
            port=gw_port,
            path="/ws",
            bus=bus,
            channels=channels,
            runner=runner,
            push_outbound_events=True,
        )
        gateway_task = asyncio.create_task(server.run())
        await asyncio.sleep(0.4)

        ws_events_task: asyncio.Task | None = None
        if check_ws_outbound_event:
            ws_events_task = asyncio.create_task(
                ws_collect_events(
                    host,
                    gw_port,
                    "/ws",
                    timeout=5.0,
                    stop_on_event="channel.outbound",
                )
            )
            await asyncio.sleep(0.15)

        try:
            ws_frame = await ws_json_request(
                host,
                gw_port,
                "/ws",
                {"type": "request", "id": "smoke-ws", "action": "gateway.ping", "payload": {}},
            )
            ws_ok = ws_frame.get("type") == "response" and ws_frame.get("payload", {}).get("ok") is True
            report.add("gateway.ws.ping", ws_ok, json.dumps(ws_frame.get("payload", {}), ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            report.add("gateway.ws.ping", False, str(e))

        status, health = await _http_get(host, webhook_port, "/webhook/health")
        health_ok = "200" in status and health.get("ok") is True
        report.add("webhook.health", health_ok, status)

        status, inbound_resp = await _http_post_json(host, webhook_port, "/webhook", inbound_payload)
        inbound_ok = "200" in status and inbound_resp.get("ok") is True
        report.add("webhook.inbound", inbound_ok, status)

        try:
            outbound = await asyncio.wait_for(captured.get(), timeout=3.0)
            outbound_ok, detail = assert_outbound(outbound)
            report.add("agent.outbound", outbound_ok, detail)
        except asyncio.TimeoutError:
            report.add("agent.outbound", False, "timeout waiting for outbound callback")

        ping_frame = await _ctl_action(host, gw_port + 1, "gateway.ping", {})
        ping_ok = ping_frame.get("type") == "response" and ping_frame.get("payload", {}).get("ok") is True
        report.add("gateway.ping", ping_ok, json.dumps(ping_frame.get("payload", {}), ensure_ascii=False))

        metrics_frame = await _ctl_action(host, gw_port + 1, "gateway.metrics", {})
        metrics_payload = metrics_frame.get("payload", {})
        counters = metrics_payload.get("metrics", {}).get("counters", {})
        metrics_ok = (
            metrics_frame.get("type") == "response"
            and metrics_payload.get("ok") is True
            and int(counters.get("agent.completed", 0)) >= 1
        )
        report.add("gateway.metrics", metrics_ok, json.dumps(counters, ensure_ascii=False))

        list_frame = await _ctl_action(host, gw_port + 1, "session.list", {"limit": 10, "offset": 0})
        list_payload = list_frame.get("payload", {})
        items = list_payload.get("items", [])
        session_ok = list_frame.get("type") == "response" and list_payload.get("count", 0) >= 1
        report.add(
            "session.list",
            session_ok,
            f"count={list_payload.get('count', 0)} first={items[0].get('session_id') if items else '-'}",
        )

        if ws_events_task is not None:
            try:
                ws_frames = await asyncio.wait_for(ws_events_task, timeout=6.0)
            except asyncio.TimeoutError:
                ws_frames = []
            outbound_events = [
                f for f in ws_frames if f.get("type") == "event" and f.get("event") == "channel.outbound"
            ]
            report.add(
                "gateway.ws.outbound_event",
                len(outbound_events) >= 1,
                f"events={len(outbound_events)} total_frames={len(ws_frames)}",
            )

        if extra_steps:
            for name, coro_factory in extra_steps:
                try:
                    ok, detail = await coro_factory(host, gw_port, webhook_port)
                    report.add(name, ok, detail)
                except Exception as e:  # noqa: BLE001
                    report.add(name, False, str(e))
    except Exception as e:  # noqa: BLE001
        report.add("smoke.runtime", False, str(e))
    finally:
        if server is not None:
            await server.shutdown()
        if gateway_task is not None:
            try:
                await asyncio.wait_for(gateway_task, timeout=3.0)
            except asyncio.TimeoutError:
                gateway_task.cancel()
        if capture_server is not None:
            capture_server.close()
            await capture_server.wait_closed()

    report.ok = all(step.ok for step in report.steps)
    return report


async def run_webhook_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    inbound_payload = {
        "sender_id": "smoke-user",
        "chat_id": "smoke-chat",
        "content": "hello smoke",
    }

    def assert_outbound(outbound: dict[str, Any]) -> tuple[bool, str]:
        content = str(outbound.get("content", ""))
        ok = "[mock:" in content and "hello smoke" in content
        return ok, content

    report = await _run_gateway_smoke_loop(
        config_path,
        gw_port=28789,
        webhook_port=28890,
        inbound_payload=inbound_payload,
        assert_outbound=assert_outbound,
        log_level=log_level,
    )
    for step in report.steps:
        if step.name == "session.list":
            step.ok = step.ok and "webhook:smoke-chat" in step.detail
    report.ok = all(step.ok for step in report.steps)
    return report


async def run_tools_smoke(
    config_path: str,
    *,
    workspace_dir: str,
    log_level: str = "WARNING",
) -> SmokeReport:
    inbound_payload = {
        "sender_id": "smoke-tools",
        "chat_id": "smoke-tools-chat",
        "content": "please read_file:hello.txt and summarize",
    }

    def assert_outbound(outbound: dict[str, Any]) -> tuple[bool, str]:
        content = str(outbound.get("content", ""))
        ok = "read complete" in content and "Hello from TrueClaw" in content
        return ok, content[:300]

    return await _run_gateway_smoke_loop(
        config_path,
        gw_port=28791,
        webhook_port=28892,
        inbound_payload=inbound_payload,
        assert_outbound=assert_outbound,
        workspace_dir=workspace_dir,
        log_level=log_level,
    )


async def run_slack_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    setup_logging(log_level)
    report = SmokeReport(ok=False)
    raw = load_raw_config(config_path)
    host = "127.0.0.1"
    gw_port = 28793
    slack_port = 28894
    gateway_task: asyncio.Task | None = None
    server: GatewayServer | None = None

    try:
        raw = json.loads(json.dumps(raw))
        raw["gateway"]["port"] = gw_port
        raw.setdefault("channels", {})
        raw["channels"]["slack"] = {
            "enabled": True,
            "botToken": "",
            "signingSecret": "",
            "listenHost": host,
            "listenPort": slack_port,
            "webhookPath": "/hooks/slack/events",
        }
        raw["channels"]["webhook"] = {"enabled": False}
        if isinstance(raw["channels"].get("telegram"), dict):
            raw["channels"]["telegram"]["enabled"] = False
        cfg = build_config(raw)
        validate_config(cfg)

        bus = MessageBus()
        channels = ChannelManager(cfg, bus)
        runner = make_agent_runner(cfg)
        server = GatewayServer(
            bind=host,
            port=gw_port,
            path="/ws",
            bus=bus,
            channels=channels,
            runner=runner,
            push_outbound_events=True,
        )
        gateway_task = asyncio.create_task(server.run())
        await asyncio.sleep(0.4)

        status, health = await _http_get(host, slack_port, "/hooks/slack/events/health")
        report.add("slack.health", "200" in status and health.get("ok") is True, status)

        challenge_body = {"type": "url_verification", "challenge": "smoke-challenge-token"}
        status, challenge_resp = await _http_post_json(
            host, slack_port, "/hooks/slack/events", challenge_body
        )
        challenge_ok = "200" in status and challenge_resp.get("challenge") == "smoke-challenge-token"
        report.add("slack.url_verification", challenge_ok, json.dumps(challenge_resp, ensure_ascii=False))

        event_body = {
            "type": "event_callback",
            "team_id": "T-smoke",
            "event": {
                "type": "message",
                "user": "U-smoke",
                "text": "hello slack smoke",
                "channel": "C-smoke",
                "ts": "123.456",
            },
        }
        status, _ = await _http_post_json(host, slack_port, "/hooks/slack/events", event_body)
        inbound_ok = "200" in status
        report.add("slack.inbound", inbound_ok, status)

        await asyncio.sleep(0.8)
        list_frame = await _ctl_action(host, gw_port + 1, "session.list", {"limit": 10, "offset": 0})
        items = list_frame.get("payload", {}).get("items", [])
        session_ok = any("slack:C-smoke" in str(i.get("session_id", "")) for i in items)
        report.add(
            "slack.session",
            session_ok,
            f"first={items[0].get('session_id') if items else '-'}",
        )
    except Exception as e:  # noqa: BLE001
        report.add("slack.runtime", False, str(e))
    finally:
        if server is not None:
            await server.shutdown()
        if gateway_task is not None:
            try:
                await asyncio.wait_for(gateway_task, timeout=3.0)
            except asyncio.TimeoutError:
                gateway_task.cancel()

    report.ok = all(step.ok for step in report.steps)
    return report


async def run_scheduler_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    setup_logging(log_level)
    report = SmokeReport(ok=False)
    raw = load_raw_config(config_path)
    raw = json.loads(json.dumps(raw))
    raw.setdefault("scheduler", {})
    raw["scheduler"]["mode"] = "off"
    for task in raw["scheduler"].get("tasks", []):
        if isinstance(task, dict) and task.get("name") == "heartbeat":
            task["enabled"] = True
            task.setdefault("wake", {})["delivery"] = "drop"
    try:
        cfg = build_config(raw)
        validate_config(cfg)
        bus = MessageBus()
        runner = make_agent_runner(cfg)
        scheduler = SchedulerEngine(cfg.scheduler, bus)
        fired = await scheduler.fire_task("heartbeat")
        report.add("scheduler.fire", fired, "manual wake")
        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=2.0)
        outbound = await runner.handle_event(inbound)
        ok = "heartbeat" in outbound.content.lower() or "[scheduled]" in outbound.content
        report.add("scheduler.agent", ok, outbound.content[:200])
    except Exception as e:  # noqa: BLE001
        report.add("scheduler.runtime", False, str(e))
    finally:
        from trueclaw.tools.bootstrap import close_mcp_routers

        close_mcp_routers()
    report.ok = all(step.ok for step in report.steps)
    return report


async def run_mcp_stdio_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    import sys

    setup_logging(log_level)
    report = SmokeReport(ok=False)
    server_py = Path(__file__).resolve().parents[3] / "examples" / "mcp-echo-server" / "server.py"
    raw = json.loads(json.dumps(load_raw_config(config_path)))
    raw["mcp"] = {
        "servers": {
            "demo": {"enabled": False, "transport": "mock"},
            "stdio": {
                "enabled": True,
                "transport": "stdio",
                "command": [sys.executable, str(server_py)],
            },
        }
    }
    try:
        cfg = build_config(raw)
        validate_config(cfg)
        registry, router = await build_tool_registry_async(cfg)
        tool_name = "mcp__stdio__echo"
        report.add("mcp_stdio.discover", tool_name in registry.names(), ",".join(registry.names()))
        if router is not None and tool_name in registry.names():
            result = await router.call(tool_name, {"text": "stdio-smoke-ok"})
            report.add("mcp_stdio.call", result == "stdio-smoke-ok", result[:200])
    except Exception as e:  # noqa: BLE001
        report.add("mcp_stdio.runtime", False, str(e))
    finally:
        await close_mcp_routers_async()
    report.ok = all(step.ok for step in report.steps)
    return report


async def run_mcp_http_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    import socket
    import subprocess
    import sys

    setup_logging(log_level)
    report = SmokeReport(ok=False)
    server_py = Path(__file__).resolve().parents[3] / "examples" / "mcp-http-echo-server" / "server.py"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    http_port = sock.getsockname()[1]
    sock.close()
    proc = subprocess.Popen(
        [sys.executable, str(server_py), "--host", "127.0.0.1", "--port", str(http_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await asyncio.sleep(0.4)
    raw = json.loads(json.dumps(load_raw_config(config_path)))
    raw["mcp"] = {
        "servers": {
            "demo": {"enabled": False, "transport": "mock"},
            "http": {
                "enabled": True,
                "transport": "http",
                "url": f"http://127.0.0.1:{http_port}/",
            },
        }
    }
    try:
        cfg = build_config(raw)
        validate_config(cfg)
        registry, router = await build_tool_registry_async(cfg)
        tool_name = "mcp__http__echo"
        report.add("mcp_http.discover", tool_name in registry.names(), ",".join(registry.names()))
        if router is not None and tool_name in registry.names():
            result = await router.call(tool_name, {"text": "http-smoke-ok"})
            report.add("mcp_http.call", result == "http-smoke-ok", result[:200])
    except Exception as e:  # noqa: BLE001
        report.add("mcp_http.runtime", False, str(e))
    finally:
        await close_mcp_routers_async()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    report.ok = all(step.ok for step in report.steps)
    return report


async def run_ws_subscribe_smoke(config_path: str, *, log_level: str = "WARNING") -> SmokeReport:
    from trueclaw.gateway.websocket import read_text_message, write_text_message
    import base64
    import hashlib

    setup_logging(log_level)
    report = SmokeReport(ok=False)
    raw = json.loads(json.dumps(load_raw_config(config_path)))
    gw_port = 28795
    webhook_port = 28895
    cfg = _smoke_config(raw, gw_port=gw_port, webhook_port=webhook_port, outbound_url="")
    bus = MessageBus()
    channels = ChannelManager(cfg, bus)
    runner = make_agent_runner(cfg)
    server = GatewayServer(
        bind="127.0.0.1",
        port=gw_port,
        path="/ws",
        bus=bus,
        channels=channels,
        runner=runner,
        push_outbound_events=True,
    )
    gateway_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.4)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gw_port)
        key = base64.b64encode(b"trueclaw-sub-smoke!").decode("ascii")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        writer.write(
            (
                "GET /ws HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{gw_port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        await reader.readline()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n"):
                break
        sub_req = {
            "type": "request",
            "id": "sub-1",
            "action": "gateway.subscribe",
            "payload": {"patterns": ["channel.*"]},
        }
        await write_text_message(writer, json.dumps(sub_req, ensure_ascii=False))
        sub_resp = json.loads((await read_text_message(reader, writer)) or "{}")
        report.add(
            "ws.subscribe",
            sub_resp.get("type") == "response" and sub_resp.get("payload", {}).get("ok"),
            json.dumps(sub_resp.get("payload", {}), ensure_ascii=False)[:120],
        )
        for _ in range(4):
            try:
                await asyncio.wait_for(read_text_message(reader, writer), timeout=0.2)
            except asyncio.TimeoutError:
                break
        await _http_post_json(
            "127.0.0.1",
            webhook_port,
            "/webhook",
            {"sender_id": "ws-smoke", "chat_id": "sub-smoke", "content": "subscribe smoke"},
        )
        await asyncio.sleep(0.8)
        events: list[str] = []
        for _ in range(12):
            try:
                raw_frame = await asyncio.wait_for(read_text_message(reader, writer), timeout=1.0)
            except asyncio.TimeoutError:
                break
            if not raw_frame:
                break
            frame = json.loads(raw_frame)
            if frame.get("type") == "event":
                events.append(str(frame.get("event", "")))
        writer.close()
        await writer.wait_closed()
        has_channel = any(e == "channel.outbound" for e in events)
        has_gateway = any(e.startswith("gateway.") for e in events)
        report.add("ws.filtered_outbound", has_channel, ",".join(events) or "-")
        report.add("ws.filtered_gateway", not has_gateway, "gateway events suppressed")
    except Exception as e:  # noqa: BLE001
        report.add("ws.subscribe_runtime", False, str(e))
    finally:
        await server.shutdown()
        gateway_task.cancel()
        try:
            await gateway_task
        except asyncio.CancelledError:
            pass
    report.ok = all(step.ok for step in report.steps)
    return report


def print_smoke_report(report: SmokeReport, *, as_json: bool, label: str | None = "SMOKE") -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in report.steps],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for step in report.steps:
        mark = "PASS" if step.ok else "FAIL"
        print(f"[{mark}] {step.name}: {step.detail}")
    if label:
        print(label, "PASS" if report.ok else "FAIL")
