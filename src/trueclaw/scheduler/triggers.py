from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone


def interval_wake_id(name: str, *, interval_sec: float, now: float | None = None) -> str:
    ts = now if now is not None else time.time()
    bucket = math.floor(ts / interval_sec) * interval_sec
    return f"{name}:{int(bucket)}"


def cron_wake_id(name: str, fire_ts: float) -> str:
    return f"{name}:{int(fire_ts)}"


def _field_matches(value: int, field: str, *, min_v: int, max_v: int) -> bool:
    field = field.strip()
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "," in field:
        return value in {int(x) for x in field.split(",")}
    return value == int(field)


def cron_matches(dt: datetime, expr: str) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"invalid cron expression (need 5 fields): {expr}")
    minute, hour, dom, month, dow = parts
    return (
        _field_matches(dt.minute, minute, min_v=0, max_v=59)
        and _field_matches(dt.hour, hour, min_v=0, max_v=23)
        and _field_matches(dt.day, dom, min_v=1, max_v=31)
        and _field_matches(dt.month, month, min_v=1, max_v=12)
        and _field_matches(dt.weekday(), dow, min_v=0, max_v=6)
    )


def next_cron_fire(expr: str, after: datetime | None = None) -> datetime:
    after = after or datetime.now(timezone.utc)
    probe = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 48):
        if cron_matches(probe, expr):
            return probe
        probe += timedelta(minutes=1)
    raise ValueError(f"no cron fire found within 48h for: {expr}")
