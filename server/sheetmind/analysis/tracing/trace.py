"""
SheetMind Runtime — Trace Structure
========================================
One Trace per query (not per session).
Multiple Traces share a session_trace_id (= AnalysisContext.trace_id).

TraceEvent records each step in the pipeline:
  routing, skill_start/end, tool_start/end, model_call,
  code_generated, code_executed, repair, result_assembled.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """One step / observation inside a Trace."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    event_type: str          # see constants below
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # Context fields — filled in by whoever creates the event
    skill_name: Optional[str] = None
    tool_name: Optional[str] = None
    model_role: Optional[str] = None    # ModelRole.value
    model_id: Optional[str] = None

    # Human-readable summaries for quick inspection (keep short)
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None

    duration_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# TraceEvent.event_type constants
# ---------------------------------------------------------------------------
EVT_ROUTING          = "routing"
EVT_SKILL_START      = "skill_start"
EVT_SKILL_END        = "skill_end"
EVT_TOOL_START       = "tool_start"
EVT_TOOL_END         = "tool_end"
EVT_MODEL_CALL       = "model_call"
EVT_CODE_GENERATED   = "code_generated"
EVT_CODE_EXECUTED    = "code_executed"
EVT_REPAIR           = "repair"
EVT_RESULT_ASSEMBLED = "result_assembled"
EVT_ERROR            = "error"


class Trace(BaseModel):
    """
    Full audit trail for one SheetMind analysis query.
    Stored in TraceStore after each run.
    """

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_trace_id: str = ""    # links all traces in one AnalysisContext
    project_id: str = ""
    task_id: str = ""
    query: str = ""

    routing_hint: Optional[str] = None    # RoutingHint.value
    multiturn_mode: Optional[str] = None  # MultiTurnMode.value

    events: List[TraceEvent] = Field(default_factory=list)

    started_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: Optional[str] = None

    success: bool = False
    error: Optional[str] = None
    total_duration_ms: Optional[float] = None

    # Convenience: model calls summary for billing / latency analysis
    model_calls: int = 0
    total_input_tokens: int = 0   # filled in by provider if the API returns usage
    total_output_tokens: int = 0

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_event(self, event_type: str, **kwargs: Any) -> TraceEvent:
        event = TraceEvent(event_type=event_type, **kwargs)
        self.events.append(event)
        if event_type == EVT_MODEL_CALL:
            self.model_calls += 1
        return event

    def finish(self, success: bool = True, error: Optional[str] = None) -> None:
        self.finished_at = datetime.utcnow().isoformat()
        self.success = success
        self.error = error
        try:
            t0 = datetime.fromisoformat(self.started_at)
            t1 = datetime.fromisoformat(self.finished_at)
            self.total_duration_ms = (t1 - t0).total_seconds() * 1000
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def summary_line(self) -> str:
        status = "OK" if self.success else f"ERR:{self.error or '?'}"
        ms = f"{self.total_duration_ms:.0f}ms" if self.total_duration_ms else "?"
        return (
            f"[{self.trace_id[:8]}] "
            f"task={self.task_id} "
            f"routing={self.routing_hint or '?'} "
            f"status={status} "
            f"duration={ms} "
            f"model_calls={self.model_calls}"
        )
