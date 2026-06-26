from __future__ import annotations

import asyncio

from trueclaw.agent.factory import make_agent_runner
from trueclaw.bus.events import InboundMessageEvent
from trueclaw.config.loader import load_config
from trueclaw.config.validate import validate_config
from trueclaw.observability.logging import setup_logging


async def run_agent_chat(
    config_path: str,
    *,
    message: str | None = None,
    no_tools: bool = False,
    log_level: str = "WARNING",
) -> int:
    cfg = load_config(config_path)
    validate_config(cfg)
    setup_logging(log_level)

    runner = make_agent_runner(cfg)
    if no_tools:
        runner.tool_registry = None
        runner.tool_executor = None

    session_chat = "cli-local"

    async def _once(content: str) -> None:
        inbound = InboundMessageEvent(
            channel="cli",
            sender_id="local-user",
            chat_id=session_chat,
            content=content,
            metadata={"source": "agent.chat"},
        )
        out = await runner.handle_event(inbound)
        if out.content:
            print(out.content)

    if message is not None:
        await _once(message)
        return 0

    print("TrueClaw agent chat (session=cli-local). Ctrl-D to exit.")
    if no_tools:
        print("tools: disabled")
    else:
        print("tools:", ", ".join(runner.tool_registry.names()) if runner.tool_registry else "-")

    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except EOFError:
            print()
            break
        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            break
        await _once(text)
    return 0
