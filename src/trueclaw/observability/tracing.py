from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> None:
    _trace_id.set(trace_id)


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    token = _trace_id.set(trace_id or new_trace_id())
    try:
        yield _trace_id.get() or ""
    finally:
        _trace_id.reset(token)
