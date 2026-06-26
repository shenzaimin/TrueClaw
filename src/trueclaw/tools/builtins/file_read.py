from __future__ import annotations

from pathlib import Path

from trueclaw.tools.base import ToolDefinition


def make_file_read_tool(workspace_dir: str) -> ToolDefinition:
    ws = Path(workspace_dir).expanduser().resolve()

    def _run(args: dict) -> str:
        rel = args.get("path", "")
        target = (ws / rel).resolve()
        if ws not in target.parents and target != ws:
            return "error: path outside workspace"
        if not target.exists():
            return "error: file not found"
        return target.read_text(encoding="utf-8")[:4000]

    return ToolDefinition(
        name="read_file",
        description="Read a text file from workspace",
        func=_run,
    )
