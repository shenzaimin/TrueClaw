#!/usr/bin/env python3
"""Minimal MCP stdio echo server for TrueClaw integration tests (chapter 10)."""

from __future__ import annotations

import json
import sys


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(req: dict) -> None:
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        _write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-echo", "version": "0.1.0"},
                },
            }
        )
        return
    if method == "notifications/initialized":
        return
    if method == "tools/list":
        _write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo input text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ],
                },
            }
        )
        return
    if method == "tools/call":
        params = req.get("params") or {}
        args = params.get("arguments") or {}
        text = str(args.get("text", ""))
        _write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        )
        return
    if req_id is not None:
        _write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            _handle(json.loads(line))
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()
