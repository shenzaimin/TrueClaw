from __future__ import annotations

import logging

from trueclaw.session.context_trim import ContextPolicy, TrimReport, trim_messages_for_prompt
from trueclaw.session.store import SessionState


def build_messages(
    system_prompt: str,
    session: SessionState,
    user_text: str,
    *,
    policy: ContextPolicy | None = None,
) -> list[dict]:
    policy = policy or ContextPolicy()
    trimmed, report = trim_messages_for_prompt(
        session.messages,
        policy=policy,
        system_prompt=system_prompt,
        user_text=user_text,
    )
    if report.dropped > 0:
        logging.getLogger(__name__).info(
            "context_trim session_id=%s before=%s after=%s dropped=%s tokens_est=%s->%s",
            session.session_id,
            report.before_count,
            report.after_count,
            report.dropped,
            report.before_tokens_est,
            report.after_tokens_est,
        )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m.role, "content": m.text} for m in trimmed)
    messages.append({"role": "user", "content": user_text})
    return messages
