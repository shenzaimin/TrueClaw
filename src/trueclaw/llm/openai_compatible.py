from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from trueclaw.llm.errors import LLMError, map_http_error
from trueclaw.llm.provider import LLMProvider, LLMResponse, StreamDelta, ToolCall
from trueclaw.llm.stream_parser import (
    ToolCallStreamAccumulator,
    extract_delta_text,
    extract_finish_reason,
    is_finish_chunk,
    parse_sse_json,
)


def parse_chat_completion(body: dict[str, Any]) -> LLMResponse:
    choices = body.get("choices") or []
    if not choices:
        raise LLMError("LLM response missing choices", body=body)
    message = choices[0].get("message") or {}
    text = message.get("content") or ""
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {"raw": raw_args}
        tool_calls.append(
            ToolCall(
                id=str(tc.get("id", "")),
                name=str(fn.get("name", "")),
                arguments=args,
            )
        )
    return LLMResponse(text=text, tool_calls=tool_calls)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_base: str, api_key: str, timeout_sec: float = 60.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self._log = logging.getLogger(__name__)

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.api_base}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            raise map_http_error(e.code, parsed) from e
        except urllib.error.URLError as e:
            raise LLMError(f"LLM network error: {e.reason}") from e

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        import asyncio

        body = await asyncio.to_thread(self._post_json, payload)
        self._log.debug("llm completion model=%s usage=%s", model, body.get("usage"))
        return parse_chat_completion(body)

    def _iter_stream_chunks(self, payload: dict[str, Any]):
        url = f"{self.api_base}/chat/completions"
        body = json.dumps({**payload, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310
                buf = ""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            if data == "[DONE]":
                                return
                            continue
                        yield parse_sse_json(data)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            raise map_http_error(e.code, parsed) from e
        except urllib.error.URLError as e:
            raise LLMError(f"LLM network error: {e.reason}") from e

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
    ):
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        import asyncio

        def _run():
            return list(self._iter_stream_chunks(payload))

        chunks = await asyncio.to_thread(_run)
        accumulator = ToolCallStreamAccumulator()
        for chunk in chunks:
            text = extract_delta_text(chunk)
            accumulator.feed(chunk)
            if text:
                yield StreamDelta(text=text, finished=False)
            if is_finish_chunk(chunk):
                reason = extract_finish_reason(chunk)
                if reason == "tool_calls" or accumulator.has_calls():
                    yield StreamDelta(text="", finished=True, tool_calls=accumulator.finalize())
                else:
                    yield StreamDelta(text="", finished=True)
                return
        if accumulator.has_calls():
            yield StreamDelta(text="", finished=True, tool_calls=accumulator.finalize())
        else:
            yield StreamDelta(text="", finished=True)
