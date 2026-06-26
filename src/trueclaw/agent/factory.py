from __future__ import annotations

from trueclaw.agent.runner import AgentRunner
from trueclaw.config.schema import AppConfig
from trueclaw.llm.factory import make_provider
from trueclaw.routing.router import Router
from trueclaw.session.activity_tracker import MemoryActivityTracker
from trueclaw.session.context_trim import ContextPolicy
from trueclaw.session.memory_store import MemorySessionStore
from trueclaw.session.store import SessionStore
from trueclaw.tools.bootstrap import build_tool_registry
from trueclaw.tools.executor import ToolExecutor


def make_context_policy(cfg: AppConfig) -> ContextPolicy:
    s = cfg.session
    return ContextPolicy(
        max_messages=s.maxMessages,
        max_prompt_tokens_est=s.maxPromptTokensEst,
        reserved_completion_tokens_est=s.reservedCompletionTokensEst,
        max_messages_per_session=s.maxMessagesPerSession,
        max_tool_result_chars=s.maxToolResultChars,
    )


def make_agent_runner(
    cfg: AppConfig,
    *,
    store: SessionStore | None = None,
    activity_tracker: MemoryActivityTracker | None = None,
) -> AgentRunner:
    registry, _router = build_tool_registry(cfg)
    executor = ToolExecutor(registry) if registry.names() else None
    defaults = cfg.agents["defaults"]
    return AgentRunner(
        store=store or MemorySessionStore(),
        router=Router(),
        provider=make_provider(cfg),
        tool_registry=registry if registry.names() else None,
        tool_executor=executor,
        max_turns=defaults.maxTurns,
        max_tool_calls_per_turn=cfg.tools.maxToolCallsPerTurn,
        model=defaults.model,
        stream_replies=defaults.streamReplies,
        context_policy=make_context_policy(cfg),
        activity_tracker=activity_tracker,
    )
