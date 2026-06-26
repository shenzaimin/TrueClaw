from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from typing import Any


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(ValueError):
    pass


class MessageTooLargeError(WebSocketError):
    pass


async def _read_http_headers(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    request_line = await reader.readline()
    if not request_line:
        raise WebSocketError("empty handshake")
    parts = request_line.decode("utf-8", errors="replace").strip().split()
    if len(parts) < 2:
        raise WebSocketError("invalid handshake request line")
    method, path = parts[0].upper(), parts[1]
    if method != "GET":
        raise WebSocketError("handshake requires GET")
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        text = line.decode("utf-8", errors="replace")
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return path, headers


async def accept_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    expected_path: str,
) -> bool:
    path, headers = await _read_http_headers(reader)
    if path != expected_path:
        writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return False
    key = headers.get("sec-websocket-key", "")
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return False
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    return True


async def _read_frame(reader: asyncio.StreamReader, *, max_bytes: int | None = None) -> tuple[int, bytes]:
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    if max_bytes is not None and length > max_bytes:
        raise MessageTooLargeError(f"frame payload {length} exceeds maxMessageBytes={max_bytes}")
    mask = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(length)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


async def _write_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
    fin_opcode = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", fin_opcode, length)
    elif length < (1 << 16):
        header = struct.pack("!BBH", fin_opcode, 126, length)
    else:
        header = struct.pack("!BBQ", fin_opcode, 127, length)
    writer.write(header + payload)
    await writer.drain()


async def read_text_message(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    max_bytes: int | None = None,
) -> str | None:
    while True:
        opcode, payload = await _read_frame(reader, max_bytes=max_bytes)
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            await _write_frame(writer, 0xA, payload)
            continue
        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        if opcode == 0x0:
            continue


async def write_text_message(writer: asyncio.StreamWriter, text: str) -> None:
    await _write_frame(writer, 0x1, text.encode("utf-8"))


async def ws_collect_events(
    host: str,
    port: int,
    path: str,
    *,
    timeout: float = 3.0,
    stop_on_event: str | None = None,
) -> list[dict[str, Any]]:
    import json

    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    key = base64.b64encode(b"trueclaw-smoke!!").decode("ascii")
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    resp = await asyncio.wait_for(reader.readline(), timeout=timeout)
    if b"101" not in resp:
        raise WebSocketError(f"handshake failed: {resp!r}")
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if line in (b"\r\n", b"\n"):
            break
    frames: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(read_text_message(reader, writer), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not raw:
            break
        frame = json.loads(raw)
        frames.append(frame)
        if stop_on_event and frame.get("type") == "event" and frame.get("event") == stop_on_event:
            break
    writer.close()
    await writer.wait_closed()
    return frames


async def ws_json_request(
    host: str,
    port: int,
    path: str,
    frame: dict[str, Any],
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    import json

    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    key = base64.b64encode(b"trueclaw-smoke!!").decode("ascii")
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    resp = await asyncio.wait_for(reader.readline(), timeout=timeout)
    if b"101" not in resp:
        raise WebSocketError(f"handshake failed: {resp!r}")
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if line in (b"\r\n", b"\n"):
            break
    await write_text_message(writer, json.dumps(frame, ensure_ascii=False))
    while True:
        raw = await asyncio.wait_for(read_text_message(reader, writer), timeout=timeout)
        if not raw:
            raise WebSocketError("empty websocket response")
        parsed = json.loads(raw)
        if parsed.get("type") in ("response", "error"):
            writer.close()
            await writer.wait_closed()
            return parsed
