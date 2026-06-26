#!/usr/bin/env python3
"""Minimal MCP HTTP echo server for TrueClaw integration tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any


def _handle_rpc(payload: dict[str, Any]) -> dict[str, Any]:
    method = payload.get("method")
    req_id = payload.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-http-echo", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return {}
    if method == "tools/list":
        return {
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
    if method == "tools/call":
        params = payload.get("params") or {}
        args = params.get("arguments") or {}
        text = str(args.get("text", ""))
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {}


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        parts = request_line.decode("utf-8", errors="replace").strip().split()
        if len(parts) < 2 or parts[0] != "POST":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        content_length = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, val = line.decode("utf-8", errors="replace").partition(":")
            if key.strip().lower() == "content-length":
                content_length = int(val.strip())
        body = await reader.readexactly(content_length) if content_length else b""
        payload = json.loads(body.decode("utf-8", errors="replace") or "{}")
        result = _handle_rpc(payload)
        if not result:
            writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        out = json.dumps(result, ensure_ascii=False).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(out)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + out
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main_async(host: str, port: int) -> None:
    server = await asyncio.start_server(_handle_client, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"mcp-http-echo listening on {addrs}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28901)
    args = parser.parse_args()
    asyncio.run(main_async(args.host, args.port))


if __name__ == "__main__":
    main()
