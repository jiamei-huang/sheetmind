"""Request-local trace binding for model providers and nested skills."""
from contextvars import ContextVar, Token
from typing import Optional

from .trace import Trace


_current_trace: ContextVar[Optional[Trace]] = ContextVar(
    "sheetmind_current_trace", default=None
)


def current_trace() -> Optional[Trace]:
    return _current_trace.get()


def bind_trace(trace: Trace) -> Token:
    return _current_trace.set(trace)


def reset_trace(token: Token) -> None:
    _current_trace.reset(token)
