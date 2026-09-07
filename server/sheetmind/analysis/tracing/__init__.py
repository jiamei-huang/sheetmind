"""Tracing package — Trace, TraceEvent, TraceStore."""
from .storage import TraceStore, get_trace_store
from .trace import (
    EVT_CODE_EXECUTED,
    EVT_CODE_GENERATED,
    EVT_ERROR,
    EVT_MODEL_CALL,
    EVT_REPAIR,
    EVT_RESULT_ASSEMBLED,
    EVT_ROUTING,
    EVT_SKILL_END,
    EVT_SKILL_START,
    EVT_TOOL_END,
    EVT_TOOL_START,
    Trace,
    TraceEvent,
)

__all__ = [
    "Trace",
    "TraceEvent",
    "TraceStore",
    "get_trace_store",
    # Event type constants
    "EVT_ROUTING",
    "EVT_SKILL_START",
    "EVT_SKILL_END",
    "EVT_TOOL_START",
    "EVT_TOOL_END",
    "EVT_MODEL_CALL",
    "EVT_CODE_GENERATED",
    "EVT_CODE_EXECUTED",
    "EVT_REPAIR",
    "EVT_RESULT_ASSEMBLED",
    "EVT_ERROR",
]
