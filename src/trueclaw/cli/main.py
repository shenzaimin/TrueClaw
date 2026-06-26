from __future__ import annotations

import argparse
import asyncio
import json
import sys

from trueclaw import __version__
from trueclaw.channels.registry import discover_all, discover_channel_names
from trueclaw.config.loader import load_config, resolve_config_path
from trueclaw.cli.config_cmd import cmd_config_print_path, cmd_config_validate, cmd_init
from trueclaw.cli.doctor_cmd import cmd_doctor
from trueclaw.cli.gateway_cmd import cmd_gateway_ctl, cmd_gateway_run
from trueclaw.cli.plugins_cmd import cmd_plugins_doctor, cmd_plugins_explain, cmd_plugins_list
from trueclaw.cli.tools_cmd import cmd_tools_list, cmd_tools_mcp_doctor, cmd_tools_mcp_list
from trueclaw.cli.wake_cmd import cmd_wake_list, cmd_wake_run
from trueclaw.cli.agent_chat import run_agent_chat
from trueclaw.cli.smoke import print_smoke_report, run_webhook_smoke
from trueclaw.cli.verify import SUITES, run_verify_sync

DEFAULT_CONFIG = "~/.trueclaw/config.json"


def _extract_globals(argv: list[str]) -> tuple[str, str, list[str], bool]:
    """从任意位置提取 --config / --log-level / --version，避免子命令前后写法不一致。"""
    config = DEFAULT_CONFIG
    log_level = "INFO"
    show_version = False
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--config":
            if i + 1 >= len(argv):
                raise SystemExit("--config requires a value")
            config = argv[i + 1]
            i += 2
            continue
        if tok == "--log-level":
            if i + 1 >= len(argv):
                raise SystemExit("--log-level requires a value")
            log_level = argv[i + 1]
            i += 2
            continue
        if tok == "--version":
            show_version = True
            i += 1
            continue
        rest.append(tok)
        i += 1
    return config, log_level, rest, show_version


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trueclaw",
        epilog="全局选项 --config / --log-level 可写在子命令前或后。",
    )

    sub = p.add_subparsers(dest="cmd")

    sp_init = sub.add_parser("init", help="initialize config")
    sp_init.add_argument("--force", action="store_true")

    sp_cfg = sub.add_parser("config", help="config operations")
    cfg_sub = sp_cfg.add_subparsers(dest="config_cmd", required=True)
    cfg_sub.add_parser("validate")
    cfg_sub.add_parser("print-path")

    sp_doc = sub.add_parser("doctor", help="run health checks")
    sp_doc.add_argument("--json", action="store_true")

    sp_status = sub.add_parser("status", help="show current runtime status")
    sp_status.add_argument("--json", action="store_true")

    sp_plugins = sub.add_parser("plugins", help="plugin commands")
    psub = sp_plugins.add_subparsers(dest="plugins_cmd", required=True)
    psub.add_parser("list")
    psub.add_parser("doctor")
    pexp = psub.add_parser("explain")
    pexp.add_argument("name", help="Plugin entry name")

    sp_gw = sub.add_parser("gateway", help="gateway operations")
    gw_sub = sp_gw.add_subparsers(dest="gw_cmd", required=True)
    gw_run = gw_sub.add_parser("run")
    gw_run.add_argument("--bind", default=None, help="Override gateway.bind")
    gw_run.add_argument("--port", type=int, default=None, help="Override gateway.port")
    gw_smoke = gw_sub.add_parser("smoke", help="run webhook end-to-end smoke test")
    gw_smoke.add_argument("--json", action="store_true")

    sp_verify = sub.add_parser("verify", help="run integration acceptance suites (chapter 16)")
    sp_verify.add_argument(
        "--suite",
        action="append",
        choices=SUITES,
        dest="suites",
        help="Suite to run (repeatable). Default: all",
    )
    sp_verify.add_argument("--json", action="store_true")
    gw_ctl = gw_sub.add_parser("ctl", help="call control plane action")
    gw_ctl.add_argument("--host", default=None, help="Control host (default gateway.bind)")
    gw_ctl.add_argument("--port", type=int, default=None, help="Control port (default gateway.port+1)")
    gw_ctl.add_argument("--action", required=True, help="Action name, e.g. gateway.ping")
    gw_ctl.add_argument("--payload", default="{}", help="JSON object payload")
    gw_ctl.add_argument("--limit", type=int, default=None, help="Pagination size for session.list")
    gw_ctl.add_argument("--offset", type=int, default=None, help="Pagination offset for session.list")

    sp_wake = sub.add_parser("wake", help="scheduler wake commands")
    wake_sub = sp_wake.add_subparsers(dest="wake_cmd", required=True)
    wake_sub.add_parser("list")
    wake_run = wake_sub.add_parser("run", help="manually fire a scheduler task once")
    wake_run.add_argument("--name", required=True, help="Task name")

    sp_tools = sub.add_parser("tools", help="tool commands")
    tools_sub = sp_tools.add_subparsers(dest="tools_cmd", required=True)
    tools_sub.add_parser("list")
    tools_mcp = tools_sub.add_parser("mcp", help="MCP bridge commands")
    mcp_sub = tools_mcp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser("list")
    mcp_sub.add_parser("doctor")

    sp_agent = sub.add_parser("agent", help="agent debugging")
    agent_sub = sp_agent.add_subparsers(dest="agent_cmd", required=True)
    agent_chat = agent_sub.add_parser("chat", help="local REPL without channels")
    agent_chat.add_argument("--message", "-m", default=None, help="Single-turn message")
    agent_chat.add_argument("--no-tools", action="store_true", help="Disable tool calls")

    return p


def cmd_status(config_path: str, as_json: bool) -> int:
    cfg = load_config(config_path)
    payload = {
        "version": __version__,
        "config": str(resolve_config_path(config_path)),
        "model": cfg.agents["defaults"].model,
        "provider": cfg.agents["defaults"].provider,
        "discovered_channels": sorted(discover_all().keys()),
        "builtin_channels": sorted(discover_channel_names()),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"TrueClaw {payload['version']}")
        print(f"config: {payload['config']}")
        print(f"provider: {payload['provider']}")
        print(f"model: {payload['model']}")
        print(f"channels: {', '.join(payload['discovered_channels']) or '-'}")
    return 0


def entrypoint(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    config_path, log_level, rest, show_version = _extract_globals(raw)
    if show_version:
        print(__version__)
        return 0

    args = build_parser().parse_args(rest)
    args.config = config_path
    args.log_level = log_level

    if args.cmd == "init":
        return cmd_init(args.config, args.force)
    if args.cmd == "config" and args.config_cmd == "validate":
        return cmd_config_validate(args.config)
    if args.cmd == "config" and args.config_cmd == "print-path":
        return cmd_config_print_path(args.config)
    if args.cmd == "doctor":
        return cmd_doctor(args.config, args.json)
    if args.cmd == "status":
        return cmd_status(args.config, args.json)
    if args.cmd == "plugins" and args.plugins_cmd == "list":
        return cmd_plugins_list()
    if args.cmd == "plugins" and args.plugins_cmd == "doctor":
        return cmd_plugins_doctor()
    if args.cmd == "plugins" and args.plugins_cmd == "explain":
        return cmd_plugins_explain(args.name)
    if args.cmd == "wake" and args.wake_cmd == "list":
        return cmd_wake_list(args.config)
    if args.cmd == "wake" and args.wake_cmd == "run":
        return asyncio.run(cmd_wake_run(args.config, args.name, args.log_level))
    if args.cmd == "tools" and args.tools_cmd == "list":
        return cmd_tools_list(args.config)
    if args.cmd == "tools" and args.tools_cmd == "mcp" and args.mcp_cmd == "list":
        return cmd_tools_mcp_list(args.config)
    if args.cmd == "tools" and args.tools_cmd == "mcp" and args.mcp_cmd == "doctor":
        return cmd_tools_mcp_doctor(args.config)
    if args.cmd == "agent" and args.agent_cmd == "chat":
        return asyncio.run(
            run_agent_chat(
                args.config,
                message=args.message,
                no_tools=args.no_tools,
                log_level=args.log_level,
            )
        )
    if args.cmd == "gateway" and args.gw_cmd == "run":
        return asyncio.run(
            cmd_gateway_run(args.config, args.log_level, bind=args.bind, port=args.port)
        )
    if args.cmd == "gateway" and args.gw_cmd == "smoke":
        report = asyncio.run(run_webhook_smoke(args.config, log_level=args.log_level))
        print_smoke_report(report, as_json=args.json)
        return 0 if report.ok else 1
    if args.cmd == "verify":
        suites = tuple(args.suites) if args.suites else ("all",)
        report = run_verify_sync(args.config, suites=suites, log_level=args.log_level)
        print_smoke_report(report, as_json=args.json, label=None if args.json else "VERIFY")
        return 0 if report.ok else 1
    if args.cmd == "gateway" and args.gw_cmd == "ctl":
        return asyncio.run(
            cmd_gateway_ctl(
                args.config,
                args.host,
                args.port,
                args.action,
                args.payload,
                args.limit,
                args.offset,
            )
        )

    build_parser().print_help()
    return 0
