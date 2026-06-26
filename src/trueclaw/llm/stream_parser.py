from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from trueclaw.llm.provider import ToolCall


def iter_sse_data_lines(raw: str) -> Iterator[str]:
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data and data != "[DONE]":
            yield data


def parse_sse_json(data: str) -> dict[str, Any]:
    return json.loads(data)


def extract_delta_text(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def is_finish_chunk(chunk: dict[str, Any]) -> bool:
    return extract_finish_reason(chunk) is not None


class ToolCallStreamAccumulator:
    """合并 OpenAI 兼容 SSE 流式 tool_calls 分片。"""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, chunk: dict[str, Any]) -> None:
        choices = chunk.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            idx = int(tc.get("index", 0))
            slot = self._calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = str(tc["id"])
            fn = tc.get("function") or {}
            if isinstance(fn, dict):
                if fn.get("name"):
                    slot["name"] = str(fn["name"])
                if fn.get("arguments"):
                    slot["arguments"] += str(fn["arguments"])

    def has_calls(self) -> bool:
        return bool(self._calls)

    def finalize(self) -> list[ToolCall]:
        out: list[ToolCall] = []
        for idx in sorted(self._calls):
            slot = self._calls[idx]
            raw_args = slot.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {"raw": raw_args}
            if not isinstance(args, dict):
                args = {"raw": raw_args}
            out.append(
                ToolCall(
                    id=slot.get("id") or f"call-{idx}",
                    name=slot.get("name") or "",
                    arguments=args,
                )
            )
        return out


def extract_finish_reason(chunk: dict[str, Any]) -> str | None:
    choices = chunk.get("choices") or []
    if not choices:
        return None
    reason = choices[0].get("finish_reason")
    if reason is None or reason == "":
        return None
    return str(reason)
