from __future__ import annotations

import json
import time
from typing import Any


class ProtocolError(ValueError):
    pass


def decode_frame(data: str) -> dict[str, Any]:
    try:
        frame = json.loads(data)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid json: {e.msg}") from e
    if not isinstance(frame, dict):
        raise ProtocolError("frame must be a JSON object")
    return frame


def validate_request(frame: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if frame.get("type") != "request":
        raise ProtocolError("frame.type must be 'request'")
    req_id = frame.get("id")
    action = frame.get("action")
    payload = frame.get("payload", {})
    if not isinstance(req_id, str) or not req_id:
        raise ProtocolError("frame.id must be non-empty string")
    if not isinstance(action, str) or not action:
        raise ProtocolError("frame.action must be non-empty string")
    if not isinstance(payload, dict):
        raise ProtocolError("frame.payload must be object")
    return req_id, action, payload


def response_frame(req_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "response",
        "id": req_id,
        "payload": payload,
        "ts": int(time.time()),
    }


def error_frame(req_id: str | None, code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "type": "error",
        "id": req_id,
        "code": code,
        "message": message,
        "retryable": retryable,
        "ts": int(time.time()),
    }


def event_frame(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "event",
        "event": event,
        "payload": payload,
        "ts": int(time.time()),
    }
