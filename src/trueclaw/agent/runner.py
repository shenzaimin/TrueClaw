from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import asyncio

from trueclaw.agent.context_builder import build_messages
from trueclaw.agent.policies import SYSTEM_PROMPT
from trueclaw.bus.events import InboundMessageEvent, OutboundMessageEvent
from trueclaw.llm.provider import LLMProvider
from trueclaw.routing.router import Router
from trueclaw.session.activity_tracker import MemoryActivityTracker
from trueclaw.session.context_trim import ContextPolicy, trim_store_messages
from trueclaw.session.store import SessionMessage, SessionStore
from trueclaw.observability.metrics import get_metrics
from trueclaw.observability.tracing import current_trace_id, trace_scope
from trueclaw.tools.executor import ToolExecutor
from trueclaw.tools.registry import ToolRegistry

OnOutbound = Callable[[OutboundMessageEvent], Awaitable[None]]


class AgentRunner:
    def __init__(
        self,
        *,
        store: SessionStore,
        router: Router,
        provider: LLMProvider,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        max_turns: int = 8,
        max_tool_calls_per_turn: int = 4,
        model: str = "gpt-4.1-mini",
        stream_replies: bool = True,
        context_policy: ContextPolicy | None = None,
        activity_tracker: MemoryActivityTracker | None = None,
    ) -> None:
        self.store = store
        self.router = router
        self.provider = provider
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.max_turns = max_turns
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.model = model
        self.stream_replies = stream_replies
        self.context_policy = context_policy or ContextPolicy()
        self.activity_tracker = activity_tracker
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    def _tools_payload(self, inbound: InboundMessageEvent | None = None) -> list[dict] | None:
        if self.tool_registry is None or not self.tool_registry.names():
            return None
        profile = None
        if inbound is not None:
            profile = inbound.metadata.get("tool_profile")
            if profile is None:
                wake = inbound.metadata.get("wake")
                if isinstance(wake, dict):
                    profile = wake.get("tool_profile")
        names = self.tool_registry.names()
        if profile == "readonly":
            names = [n for n in names if n == "read_file" or n.startswith("mcp__")]
        if not names:
            return None
        return self.tool_registry.openapi_tools(filter_names=names)

    def _base_metadata(self, inbound: InboundMessageEvent) -> dict:
        trace_id = inbound.metadata.get("trace_id") or current_trace_id() or ""
        return {
            "trace_id": trace_id,
            "delivery": inbound.metadata.get("delivery"),
        }

    async def _emit_outbound(
        self,
        inbound: InboundMessageEvent,
        content: str,
        *,
        on_outbound: OnOutbound | None,
        stream_phase: str | None = None,
    ) -> None:
        if on_outbound is None:
            return
        meta = self._base_metadata(inbound)
        if stream_phase:
            meta["stream_phase"] = stream_phase
        await on_outbound(
            OutboundMessageEvent(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=content,
                metadata=meta,
            )
        )

    async def _run_tool_calls(self, tool_calls, working_messages: list[dict]) -> None:
        if self.tool_executor is None:
            return
        for call in tool_calls[: self.max_tool_calls_per_turn]:
            result = await self.tool_executor.execute(call.name, call.arguments)
            working_messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )

    async def _complete_turn(
        self,
        working_messages: list[dict],
        tools: list[dict] | None,
        inbound: InboundMessageEvent,
        on_outbound: OnOutbound | None,
    ) -> tuple[str, bool]:
        if self.stream_replies:
            accumulated = ""
            async for delta in self.provider.stream(working_messages, model=self.model, tools=tools):
                if delta.has_tool_calls:
                    await self._run_tool_calls(delta.tool_calls, working_messages)
                    return "", True
                if delta.text:
                    accumulated += delta.text
                    await self._emit_outbound(
                        inbound, delta.text, on_outbound=on_outbound, stream_phase="delta"
                    )
                if delta.finished:
                    return accumulated, False
            return accumulated, False

        resp = await self.provider.complete(working_messages, model=self.model, tools=tools)
        if resp.has_tool_calls:
            await self._run_tool_calls(resp.tool_calls, working_messages)
            return "", True
        return resp.text, False

    async def handle_event(
        self,
        inbound: InboundMessageEvent,
        *,
        on_outbound: OnOutbound | None = None,
    ) -> OutboundMessageEvent:
        trace_id = inbound.metadata.get("trace_id")
        with trace_scope(trace_id if isinstance(trace_id, str) else None):
            get_metrics().inc("agent.events")
            intent = self.router.route_event(inbound)
            async with self._session_lock(intent.session_id):
                return await self._handle_event_locked(inbound, intent, on_outbound=on_outbound)

    async def _handle_event_locked(
        self,
        inbound: InboundMessageEvent,
        intent,
        *,
        on_outbound: OnOutbound | None = None,
    ) -> OutboundMessageEvent:
        if self.activity_tracker is not None:
            self.activity_tracker.record(
                principal=inbound.sender_id,
                session_id=intent.session_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                thread_id=inbound.metadata.get("thread_id"),
            )
        state = await self.store.get_or_create(intent.session_id)
        working_messages = build_messages(
            SYSTEM_PROMPT,
            state,
            inbound.content,
            policy=self.context_policy,
        )
        tools = self._tools_payload(inbound)
        final_text = ""

        for _ in range(self.max_turns):
            text, need_continue = await self._complete_turn(
                working_messages, tools, inbound, on_outbound
            )
            if need_continue:
                continue
            final_text = text
            break

        if not final_text:
            final_text = "[trueclaw] no response produced"

        state.messages.append(SessionMessage(role="user", text=inbound.content))
        state.messages.append(SessionMessage(role="assistant", text=final_text))
        state.messages = trim_store_messages(
            state.messages,
            max_messages_per_session=self.context_policy.max_messages_per_session,
        )

        streamed = self.stream_replies and on_outbound is not None
        get_metrics().inc("agent.completed")
        return OutboundMessageEvent(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            content="" if streamed else final_text,
            metadata=self._base_metadata(inbound) | ({"stream_phase": "final"} if streamed else {}),
        )

    async def list_sessions(self) -> list[dict]:
        states = await self.store.list_sessions()
        return [
            {
                "session_id": s.session_id,
                "message_count": len(s.messages),
                "last_role": (s.messages[-1].role if s.messages else None),
            }
            for s in states
        ]
