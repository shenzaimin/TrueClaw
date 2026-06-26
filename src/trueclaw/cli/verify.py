from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from trueclaw.channels.registry import discover_all, discover_channel_names
from trueclaw.cli.smoke import (
    SmokeReport,
    run_mcp_http_smoke,
    run_mcp_stdio_smoke,
    run_scheduler_smoke,
    run_slack_smoke,
    run_tools_smoke,
    run_webhook_smoke,
    run_ws_subscribe_smoke,
)
from trueclaw.config.loader import load_config
from trueclaw.config.validate import validate_config
from trueclaw.plugins.loader import PluginLoader
from trueclaw.tools.bootstrap import build_tool_registry

SUITES = ("static", "webhook", "tools", "slack", "scheduler", "mcp_stdio", "mcp_http", "ws", "all")

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _merge_report(target: SmokeReport, source: SmokeReport, *, prefix: str = "") -> None:
    for step in source.steps:
        name = f"{prefix}{step.name}" if prefix else step.name
        target.add(name, step.ok, step.detail)
    if not source.ok:
        target.ok = False


def _run_unittest_file(path: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", str(path)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    ok = proc.returncode == 0
    detail = (proc.stdout + proc.stderr).strip() or f"exit={proc.returncode}"
    return ok, detail[:300]


def run_static_suite(config_path: str) -> SmokeReport:
    report = SmokeReport(ok=True)
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
        report.add("static.config_validate", True, "ok")
    except Exception as e:  # noqa: BLE001
        report.add("static.config_validate", False, str(e))
        report.ok = False
        return report

    channels = discover_all()
    builtins = set(discover_channel_names())
    report.add(
        "static.channels_discover",
        len(channels) >= 4 and {"echo", "slack", "webhook"}.issubset(channels.keys()),
        f"count={len(channels)} names={','.join(sorted(channels))}",
    )
    report.add(
        "static.echo_is_plugin",
        "echo" in channels and "echo" not in builtins,
        "echo registered via entry point",
    )
    report.add(
        "static.slack_is_plugin",
        "slack" in channels and "slack" not in builtins,
        "slack registered via entry point",
    )

    loader = PluginLoader()
    results = {r.name: r for r in loader.discover_and_load()}
    for plugin_name in ("echo", "slack"):
        res = results.get(plugin_name)
        report.add(
            f"static.plugins_{plugin_name}",
            res is not None and res.status == "LOADED",
            res.detail if res else "missing",
        )

    try:
        cfg = load_config(config_path)
        registry, _router = build_tool_registry(cfg)
        names = registry.names()
        report.add(
            "static.tools_read_file",
            "read_file" in names,
            f"registered={','.join(names) or '-'}",
        )
        report.add(
            "static.mcp_mock_tools",
            any(n.startswith("mcp__demo__") for n in names),
            f"mcp_tools={','.join(n for n in names if n.startswith('mcp__')) or '-'}",
        )
    except Exception as e:  # noqa: BLE001
        report.add("static.tools_read_file", False, str(e))

    workspace = _REPO_ROOT / "workspace" / "hello.txt"
    report.add("static.workspace_fixture", workspace.is_file(), str(workspace))

    parser_ok, parser_detail = _run_unittest_file(_REPO_ROOT / "tests" / "test_stream_parser.py")
    report.add("static.stream_tool_calls_parser", parser_ok, parser_detail)

    mcp_ok, mcp_detail = _run_unittest_file(_REPO_ROOT / "tests" / "test_mcp_mock.py")
    report.add("static.mcp_mock_bridge", mcp_ok, mcp_detail)

    leader_ok, leader_detail = _run_unittest_file(_REPO_ROOT / "tests" / "test_scheduler_leader.py")
    report.add("static.scheduler_leader_lock", leader_ok, leader_detail)

    sub_ok, sub_detail = _run_unittest_file(_REPO_ROOT / "tests" / "test_gateway_subscribe.py")
    report.add("static.gateway_subscribe", sub_ok, sub_detail)

    report.ok = all(step.ok for step in report.steps)
    return report


async def run_verify(
    config_path: str,
    *,
    suites: tuple[str, ...] = ("all",),
    log_level: str = "WARNING",
) -> SmokeReport:
    selected = set(suites)
    if "all" in selected:
        selected = {"static", "webhook", "tools", "slack", "scheduler", "mcp_stdio", "mcp_http", "ws"}

    report = SmokeReport(ok=True)

    if "static" in selected:
        static = run_static_suite(config_path)
        _merge_report(report, static)

    if "webhook" in selected:
        webhook = await run_webhook_smoke(config_path, log_level=log_level)
        _merge_report(report, webhook, prefix="webhook.")

    if "tools" in selected:
        workspace_dir = str(_REPO_ROOT / "workspace")
        tools = await run_tools_smoke(config_path, workspace_dir=workspace_dir, log_level=log_level)
        _merge_report(report, tools, prefix="tools.")

    if "slack" in selected:
        slack = await run_slack_smoke(config_path, log_level=log_level)
        _merge_report(report, slack, prefix="slack.")

    if "scheduler" in selected:
        scheduler = await run_scheduler_smoke(config_path, log_level=log_level)
        _merge_report(report, scheduler, prefix="scheduler.")

    if "mcp_stdio" in selected:
        mcp_stdio = await run_mcp_stdio_smoke(config_path, log_level=log_level)
        _merge_report(report, mcp_stdio, prefix="mcp_stdio.")

    if "mcp_http" in selected:
        mcp_http = await run_mcp_http_smoke(config_path, log_level=log_level)
        _merge_report(report, mcp_http, prefix="mcp_http.")

    if "ws" in selected:
        ws = await run_ws_subscribe_smoke(config_path, log_level=log_level)
        _merge_report(report, ws, prefix="ws.")

    report.ok = all(step.ok for step in report.steps)
    return report


def run_verify_sync(
    config_path: str,
    *,
    suites: tuple[str, ...] = ("all",),
    log_level: str = "WARNING",
) -> SmokeReport:
    return asyncio.run(run_verify(config_path, suites=suites, log_level=log_level))
