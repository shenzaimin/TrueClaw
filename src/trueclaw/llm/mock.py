from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from trueclaw.llm.provider import LLMProvider, LLMResponse, StreamDelta, ToolCall

_READ_FILE_RE = re.compile(r"read_file:([^\s]+)")


class MockOpenAICompatibleProvider(LLMProvider):
    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        if messages and messages[-1].get("role") == "tool":
            content = str(messages[-1].get("content", ""))
            preview = content[:800]
            return LLMResponse(text=f"[mock:{model}] read complete:\n{preview}")

        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        latest = user_msgs[-1] if user_msgs else ""
        match = _READ_FILE_RE.search(latest)
        if match and tools:
            path = match.group(1).strip()
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": path})],
            )
        return LLMResponse(text=f"[mock:{model}] {latest}")

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamDelta]:
        full = await self.complete(messages, model=model, tools=tools)
        if full.has_tool_calls:
            yield StreamDelta(text="", finished=True, tool_calls=full.tool_calls)
            return
        text = full.text
        step = 8
        for i in range(0, max(len(text), 1), step):
            piece = text[i : i + step]
            if piece:
                yield StreamDelta(text=piece, finished=False)
                await asyncio.sleep(0)
        yield StreamDelta(text="", finished=True)
