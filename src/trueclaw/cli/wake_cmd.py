from __future__ import annotations

import asyncio

from trueclaw.agent.factory import make_agent_runner
from trueclaw.bus.queue import MessageBus
from trueclaw.config.loader import load_config
from trueclaw.config.validate import validate_config
from trueclaw.observability.logging import setup_logging
from trueclaw.scheduler.engine import SchedulerEngine
from trueclaw.session.activity_tracker import MemoryActivityTracker


def cmd_wake_list(config_path: str) -> int:
    cfg = load_config(config_path)
    if not cfg.scheduler.tasks:
        print("No scheduler tasks configured")
        return 0
    for t in cfg.scheduler.tasks:
        sched = t.intervalSec and f"every {t.intervalSec}s" or (t.cron or "-")
        print(
            f"{t.name}\tenabled={t.enabled}\tschedule={sched}\t"
            f"delivery={t.wake.delivery}\ttarget={t.wake.target_channel}:{t.wake.target_chat_id}"
        )
    return 0


async def cmd_wake_run(config_path: str, name: str, log_level: str) -> int:
    cfg = load_config(config_path)
    validate_config(cfg)
    setup_logging(log_level or cfg.gateway.logLevel)

    bus = MessageBus()
    activity = MemoryActivityTracker()
    runner = make_agent_runner(cfg, activity_tracker=activity)
    scheduler = SchedulerEngine(cfg.scheduler, bus, activity_tracker=activity, config_path=config_path)
    fired = await scheduler.fire_task(name, source="manual")
    if not fired:
        print(f"Task not fired: {name} (disabled, deduped, or quiet hours)")
        return 1
    inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=2.0)
    outbound = await runner.handle_event(inbound)
    print(outbound.content)
    return 0
