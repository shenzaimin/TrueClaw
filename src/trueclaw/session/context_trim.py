from __future__ import annotations

from dataclasses import dataclass, field

from trueclaw.session.store import SessionMessage


@dataclass
class ContextPolicy:
    max_messages: int = 40
    max_prompt_tokens_est: int = 8000
    reserved_completion_tokens_est: int = 2000
    max_messages_per_session: int = 200
    max_tool_result_chars: int = 2000


@dataclass
class TrimReport:
    before_count: int = 0
    after_count: int = 0
    before_tokens_est: int = 0
    after_tokens_est: int = 0
    policy: str = "count+budget"
    dropped: int = 0


def estimate_tokens(text: str) -> int:
    # 教学版粗估：英文约 4 字符/token，中文约 1.5 字符/token，取折中
    if not text:
        return 0
    return max(1, len(text) // 3)


def trim_tool_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n...[truncated]"


def trim_messages_for_prompt(
    messages: list[SessionMessage],
    *,
    policy: ContextPolicy,
    system_prompt: str,
    user_text: str,
) -> tuple[list[SessionMessage], TrimReport]:
    report = TrimReport(before_count=len(messages))
    src = list(messages[-policy.max_messages :])
    report.before_tokens_est = (
        sum(estimate_tokens(m.text) for m in src)
        + estimate_tokens(system_prompt)
        + estimate_tokens(user_text)
    )

    normalized: list[SessionMessage] = []
    for m in src:
        text = m.text
        if m.role == "tool":
            text = trim_tool_text(text, max_chars=policy.max_tool_result_chars)
        normalized.append(SessionMessage(role=m.role, text=text))

    budget = max(
        0,
        policy.max_prompt_tokens_est - policy.reserved_completion_tokens_est - estimate_tokens(system_prompt),
    )
    used = 0
    keep: list[SessionMessage] = []
    for m in reversed(normalized):
        cost = estimate_tokens(m.text)
        if keep and used + cost > budget:
            continue
        keep.append(m)
        used += cost
    keep.reverse()

    report.after_count = len(keep)
    report.dropped = max(0, len(normalized) - len(keep))
    report.after_tokens_est = (
        sum(estimate_tokens(m.text) for m in keep) + estimate_tokens(system_prompt) + estimate_tokens(user_text)
    )
    return keep, report


def trim_store_messages(messages: list[SessionMessage], *, max_messages_per_session: int) -> list[SessionMessage]:
    if len(messages) <= max_messages_per_session:
        return messages
    return messages[-max_messages_per_session:]
