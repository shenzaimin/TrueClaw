from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class Metrics:
    """进程内计数器与直方图占位（第 16/17 章可观测性 MVP）。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics
