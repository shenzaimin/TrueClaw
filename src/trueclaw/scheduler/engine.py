from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from trueclaw.bus.queue import MessageBus
from trueclaw.config.schema import SchedulerConfig, SchedulerTaskConfig, WakeTaskWakeConfig
from trueclaw.scheduler.dispatch import wake_to_inbound
from trueclaw.scheduler.leader import SchedulerLeaderLock
from trueclaw.scheduler.triggers import cron_matches, cron_wake_id, interval_wake_id
from trueclaw.scheduler.wake_context import WakeContext
from trueclaw.scheduler.wake_id_store import WakeIdStore
from trueclaw.session.activity_tracker import MemoryActivityTracker


@dataclass
class TaskRuntime:
    task: SchedulerTaskConfig
    lock: asyncio.Lock
    last_fire_at: float | None = None


class SchedulerEngine:
    def __init__(
        self,
        config: SchedulerConfig,
        bus: MessageBus,
        *,
        publish_wake: Callable[[WakeContext, str], Any] | None = None,
        activity_tracker: MemoryActivityTracker | None = None,
        leader: SchedulerLeaderLock | None = None,
        config_path: str = "",
        wake_id_store: WakeIdStore | None = None,
    ) -> None:
        self.config = config
        self.bus = bus
        self._publish_wake = publish_wake
        self._activity_tracker = activity_tracker
        self._leader = leader
        self._config_path = config_path
        backend = str(getattr(config, "wakeIdBackend", "file") or "file").lower()
        if wake_id_store is not None:
            self._wake_id_store = wake_id_store
        elif config_path:
            self._wake_id_store = WakeIdStore(
                config_path=config_path,
                backend=backend,
                redis_url=str(getattr(config, "redisUrl", "") or ""),
                ttl_sec=float(getattr(config, "wakeIdTtlSec", 3600.0)),
            )
        else:
            self._wake_id_store = None
        self._log = logging.getLogger(__name__)
        self._tasks: list[asyncio.Task] = []
        self._leader_task: asyncio.Task | None = None
        self._leader_cancel = asyncio.Event()
        self._running = False
        self._seen_wake_ids: set[str] = set()
        self._runtimes: dict[str, TaskRuntime] = {}

    def _wake_cfg(self, task: SchedulerTaskConfig) -> WakeTaskWakeConfig:
        return task.wake

    def _task_content(self, task: SchedulerTaskConfig) -> str:
        wake = self._wake_cfg(task)
        if wake.prompt:
            return wake.prompt
        return f"[scheduled] Run task: {task.name}"

    def _build_wake(
        self,
        task: SchedulerTaskConfig,
        *,
        source: str,
        wake_id: str,
        schedule: str | None,
    ) -> WakeContext:
        wake = self._wake_cfg(task)
        return WakeContext(
            wake_id=wake_id,
            source=source,  # type: ignore[arg-type]
            name=task.name,
            schedule=schedule,
            requested_at=time.time(),
            target_channel=wake.target_channel,
            target_chat_id=wake.target_chat_id,
            target_thread_id=wake.target_thread_id,
            target_session_id=wake.target_session_id,
            delivery=wake.delivery,  # type: ignore[arg-type]
            tool_profile=wake.tool_profile,
            allow_user_visible_reply=wake.allow_user_visible_reply,
            meta={"task": task.name},
        )

    def _apply_last_active(self, wake: WakeContext) -> WakeContext:
        if wake.delivery != "last_active" or self._activity_tracker is None:
            return wake
        principal = str(wake.meta.get("principal", "")).strip() or None
        last = self._activity_tracker.resolve_last_active(principal)
        if last is None:
            self._log.warning("scheduler last_active unresolved task=%s", wake.name)
            return wake
        wake.target_channel = last.channel
        wake.target_chat_id = last.chat_id
        wake.target_session_id = last.session_id
        return wake

    def _remember_wake_id(self, wake_id: str) -> bool:
        if self._wake_id_store is not None:
            if not self._wake_id_store.try_claim(wake_id):
                return False
        if wake_id in self._seen_wake_ids:
            return False
        self._seen_wake_ids.add(wake_id)
        if len(self._seen_wake_ids) > 1000:
            self._seen_wake_ids.clear()
        return True

    def _in_quiet_hours(self) -> bool:
        qh = self.config.quiet_hours or {}
        if not qh:
            return False
        start = str(qh.get("start", "")).strip()
        end = str(qh.get("end", "")).strip()
        if not start or not end:
            return False
        now = time.localtime()
        cur = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end

    async def _emit(self, wake: WakeContext, content: str) -> None:
        if self._publish_wake is not None:
            await self._publish_wake(wake, content)
            return
        await self.bus.publish_inbound(wake_to_inbound(wake, content))

    def prepare_tasks(self) -> None:
        for task in self.config.tasks:
            if task.name not in self._runtimes:
                self._runtimes[task.name] = TaskRuntime(task=task, lock=asyncio.Lock())

    async def fire_task(self, name: str, *, source: str = "manual") -> bool:
        self.prepare_tasks()
        runtime = self._runtimes.get(name)
        if runtime is None:
            raise KeyError(f"scheduler task not found: {name}")
        return await self._fire_runtime(runtime, source=source) is not None

    async def _fire_runtime(self, runtime: TaskRuntime, *, source: str) -> WakeContext | None:
        task = runtime.task
        if not task.enabled:
            return None
        if self._in_quiet_hours() and (self.config.quiet_hours or {}).get("behavior") == "drop":
            self._log.info("scheduler quiet hours drop task=%s", task.name)
            return None

        async with runtime.lock:
            now = time.time()
            if task.intervalSec:
                wake_id = interval_wake_id(task.name, interval_sec=task.intervalSec, now=now)
                schedule = f"every {task.intervalSec}s"
            else:
                wake_id = cron_wake_id(task.name, now)
                schedule = task.cron
            if not self._remember_wake_id(wake_id):
                self._log.info("scheduler dedup skip task=%s wake_id=%s", task.name, wake_id)
                return None
            wake = self._build_wake(task, source=source, wake_id=wake_id, schedule=schedule)
            wake = self._apply_last_active(wake)
            content = self._task_content(task)
            await self._emit(wake, content)
            runtime.last_fire_at = now
            self._log.info("scheduler fired task=%s wake_id=%s", task.name, wake_id)
            return wake

    async def _interval_loop(self, runtime: TaskRuntime) -> None:
        task = runtime.task
        interval = float(task.intervalSec or 60.0)
        interval = max(interval, 1.0)
        while self._running:
            try:
                await self._fire_runtime(runtime, source="interval")
            except Exception as e:  # noqa: BLE001
                self._log.warning("scheduler interval task=%s error=%s", task.name, e)
            await asyncio.sleep(interval)

    async def _cron_loop(self, runtime: TaskRuntime) -> None:
        task = runtime.task
        expr = str(task.cron or "").strip()
        fired_minute: str | None = None
        while self._running:
            try:
                from datetime import datetime

                now_dt = datetime.now().replace(second=0, microsecond=0)
                minute_key = now_dt.strftime("%Y-%m-%d-%H-%M")
                if cron_matches(now_dt, expr) and fired_minute != minute_key:
                    fired_minute = minute_key
                    await self._fire_runtime(runtime, source="cron")
            except Exception as e:  # noqa: BLE001
                self._log.warning("scheduler cron task=%s error=%s", task.name, e)
            await asyncio.sleep(1.0)

    def list_tasks(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, runtime in sorted(self._runtimes.items()):
            task = runtime.task
            rows.append(
                {
                    "name": name,
                    "enabled": task.enabled,
                    "intervalSec": task.intervalSec,
                    "cron": task.cron,
                    "delivery": task.wake.delivery,
                    "target_channel": task.wake.target_channel,
                    "target_chat_id": task.wake.target_chat_id,
                    "last_fire_at": runtime.last_fire_at,
                }
            )
        return rows

    async def start(self) -> None:
        if self.config.mode != "inprocess":
            self._log.info("scheduler mode=%s, skip start", self.config.mode)
            return
        if self.config.leaderLock:
            if self._leader is None:
                self._log.warning("scheduler leader lock enabled but no lock instance provided; skip")
                return
            if not self._leader.try_acquire():
                holder = SchedulerLeaderLock.read_holder(self._leader.path)
                self._log.warning(
                    "scheduler skipped: not leader (lock=%s holder=%s)",
                    self._leader.path,
                    holder,
                )
                return
            self._leader_cancel.clear()
            self._leader_task = asyncio.create_task(self._leader.renew_loop(self._leader_cancel))
            self._log.info("scheduler leader acquired path=%s", self._leader.path)
        self._running = True
        self.prepare_tasks()
        for task in self.config.tasks:
            if not task.enabled:
                continue
            if not task.intervalSec and not task.cron:
                self._log.warning("scheduler task=%s has no intervalSec/cron, skip", task.name)
                continue
            runtime = self._runtimes[task.name]
            if task.intervalSec:
                self._tasks.append(asyncio.create_task(self._interval_loop(runtime)))
            else:
                self._tasks.append(asyncio.create_task(self._cron_loop(runtime)))
        self._log.info("scheduler started tasks=%s", len(self._tasks))

    async def stop(self) -> None:
        self._running = False
        self._leader_cancel.set()
        if self._leader_task is not None:
            self._leader_task.cancel()
            try:
                await self._leader_task
            except asyncio.CancelledError:
                pass
            self._leader_task = None
        if self._leader is not None and self._leader.is_leader:
            self._leader.release()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._runtimes.clear()
