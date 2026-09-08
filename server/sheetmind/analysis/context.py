"""
SheetMind Runtime — Core Data Structures
============================================
AnalysisContext  holds the full state for one analysis session (one task).
Turn             is one user/assistant exchange in a session.
ResultBlocks     is the native backend output envelope.
RoutingHint      determines which execution path to take (not the output format).
MultiTurnMode    determines whether to use the previous result as input.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingHint(str, Enum):
    """
    Determines the execution path only.
    Output format (table / chart / summary) is decided AFTER execution based
    on what ResultBlocks were actually produced — NOT pre-classified here.

    RULE_ENGINE  — deterministic pandas: date filter, keyword filter, sort.
                   No LLM code generation needed.
    CODE_GEN     — LLM generates pandas code: aggregation, pivot, Top-N,
                   growth rate, chart parameter extraction, complex conditions.
    INSIGHT_ONLY — no new structured computation; LLM writes insight text from
                   existing results or lightweight dataframe context.
                   e.g. "what anomalies are there?", "what does this trend mean?"
    """
    RULE_ENGINE = "rule"
    CODE_GEN    = "code"
    INSIGHT_ONLY = "insight"


class MultiTurnMode(str, Enum):
    """
    Context mode for the current turn.

    NEW_QUERY   — load from original file, start fresh (first question, or
                  unrelated new topic).
    FOLLOW_UP   — use active_result from AnalysisContext as the input df
                  (refine / extend previous result).
    RESET       — user explicitly asked to start over; reload from file
                  and discard active_result.
    """
    NEW_QUERY  = "new"
    FOLLOW_UP  = "follow_up"
    RESET      = "reset"


class ExecutionPlan(BaseModel):
    """Shared, inspectable contract for one routed analysis turn."""

    route: RoutingHint
    mode: MultiTurnMode
    operation_types: List[str] = Field(default_factory=list)
    needs_new_computation: bool = True
    wants_chart: bool = False
    uses_previous_result: bool = False
    target_fields: List[str] = Field(default_factory=list)
    target_sheets: List[str] = Field(default_factory=list)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# File references
# ---------------------------------------------------------------------------

class FileRef(BaseModel):
    """Lightweight reference to an uploaded Excel file."""
    file_id: str
    file_name: str
    sheet_names: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Result Blocks — native result protocol
# ---------------------------------------------------------------------------

class SummaryBlock(BaseModel):
    """Free-form markdown insight text."""
    kind: Literal["summary"] = "summary"
    title: Optional[str] = None
    content: str     # markdown-compatible; frontend renders with markdown support


class MetricBlock(BaseModel):
    """Single key metric (e.g. Total Revenue: 1,280,000 CNY)."""
    kind: Literal["metric"] = "metric"
    label: str
    value: Union[int, float, str]
    unit: Optional[str] = None


class ColumnMeta(BaseModel):
    """Column metadata for the table block."""
    name: str
    type: str = "categorical"           # "numeric" | "categorical" | "datetime"
    should_aggregate: bool = False
    decimal_places: Optional[int] = None


class TableBlock(BaseModel):
    """Tabular data result."""
    kind: Literal["table"] = "table"
    title: Optional[str] = None
    columns: List[str]
    rows: List[Dict[str, Any]]
    total_rows: Optional[int] = None
    totals: Optional[Dict[str, Optional[float]]] = None
    columns_metadata: Optional[List[ColumnMeta]] = None


class ChartSeries(BaseModel):
    """One data series inside a chart."""
    name: str
    values: List[Optional[float]]


class ChartBlock(BaseModel):
    """
    Chart data payload.  Matches the schema the frontend's Recharts components
    expect verbatim (labels / series / type / palette).
    """
    kind: Literal["chart"] = "chart"
    chart_type: str                             # "bar" | "line" | "pie"
    title: Optional[str] = None
    labels: List[str]                           # X-axis categories
    series: List[ChartSeries]                   # [{name, values}]
    palette: Optional[List[str]] = None
    x_axis_label: Optional[str] = None
    y_axis_label: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# ResultBlocks — top-level response envelope
# ---------------------------------------------------------------------------

class ResultBlocks(BaseModel):
    """Stable result protocol produced by the analysis runtime."""

    type: Literal["result_blocks"] = "result_blocks"
    blocks: List[Union[SummaryBlock, MetricBlock, TableBlock, ChartBlock]] = Field(
        default_factory=list
    )

    @property
    def has_table(self) -> bool:
        return any(self._block_kind(b) == "table" for b in self.blocks)

    @property
    def has_chart(self) -> bool:
        return any(self._block_kind(b) == "chart" for b in self.blocks)

    @property
    def has_summary(self) -> bool:
        return any(self._block_kind(b) == "summary" for b in self.blocks)

    @staticmethod
    def _block_kind(b: Any) -> str:
        if isinstance(b, dict):
            return b.get("kind", "")
        return getattr(b, "kind", "")

    def first_table(self) -> Optional[TableBlock]:
        for b in self.blocks:
            if self._block_kind(b) == "table":
                return b if isinstance(b, TableBlock) else None
        return None

    def first_chart(self) -> Optional[ChartBlock]:
        for b in self.blocks:
            if self._block_kind(b) == "chart":
                return b if isinstance(b, ChartBlock) else None
        return None

    def all_summaries(self) -> List[str]:
        texts = []
        for b in self.blocks:
            if self._block_kind(b) == "summary":
                content = b.get("content") if isinstance(b, dict) else getattr(b, "content", "")
                if content:
                    texts.append(content)
        return texts


# ---------------------------------------------------------------------------
# Conversation turn
# ---------------------------------------------------------------------------

class Turn(BaseModel):
    """One user/assistant exchange in an AnalysisContext session."""
    role: str                               # "user" | "assistant"
    content: str
    result: Optional[ResultBlocks] = None
    routing_hint: Optional[RoutingHint] = None
    multiturn_mode: Optional[MultiTurnMode] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# AnalysisContext — unit of state for one analysis session
# ---------------------------------------------------------------------------

class AnalysisContext(BaseModel):
    """
    Carries the complete state for one analysis session (one task).
    Passed to every Skill and updated after each turn.

    The `_active_df` private attribute holds the current pandas DataFrame in
    memory during a request — it is NOT serialized and must be rebuilt from
    files when restoring a context from storage.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: str
    task_id: str
    files: List[FileRef] = Field(default_factory=list)
    selected_sheets: List[str] = Field(default_factory=list)
    conversation: List[Turn] = Field(default_factory=list)
    active_result: Optional[ResultBlocks] = None
    execution_plan: Optional[ExecutionPlan] = None
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    # Runtime-only, not serialized
    _active_df: Any = PrivateAttr(default=None)
    _source_df: Any = PrivateAttr(default=None)
    _result_df: Any = PrivateAttr(default=None)
    _load_report: Any = PrivateAttr(default=None)

    # -------------------------------------------------------------------
    # Conversation helpers
    # -------------------------------------------------------------------

    def add_user_turn(self, query: str) -> None:
        self.conversation.append(Turn(role="user", content=query))

    def add_assistant_turn(
        self,
        summary: str,
        result: Optional[ResultBlocks],
        routing_hint: Optional[RoutingHint] = None,
        multiturn_mode: Optional[MultiTurnMode] = None,
    ) -> None:
        self.conversation.append(Turn(
            role="assistant",
            content=summary,
            result=result,
            routing_hint=routing_hint,
            multiturn_mode=multiturn_mode,
        ))
        if result is not None:
            self.active_result = result

    def last_assistant_result(self) -> Optional[ResultBlocks]:
        for turn in reversed(self.conversation):
            if turn.role == "assistant" and turn.result is not None:
                return turn.result
        return None

    def conversation_text(self, max_turns: int = 10) -> str:
        """
        Return the last `max_turns` turns as a plain-text string for use in
        LLM prompts.  Skips turns with empty content.
        """
        recent = self.conversation[-max_turns * 2:]  # *2 so we get N user+assistant pairs
        lines: List[str] = []
        for t in recent:
            if not t.content.strip():
                continue
            prefix = "User" if t.role == "user" else "Assistant"
            lines.append(f"{prefix}: {t.content}")
        return "\n".join(lines)

    def previous_routing_hint(self) -> Optional[RoutingHint]:
        """Return the RoutingHint used in the last assistant turn, if any."""
        for turn in reversed(self.conversation):
            if turn.role == "assistant" and turn.routing_hint is not None:
                return turn.routing_hint
        return None
