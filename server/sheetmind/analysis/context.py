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

import asyncio
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .language import ResponseLanguage


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


class QueryFilter(BaseModel):
    """A planner-owned filter before it is bound to a physical column."""

    field: str
    operator: Literal[
        "eq", "neq", "contains", "in", "gt", "gte", "lt", "lte", "between"
    ] = "eq"
    value: Any = None


class QueryMetric(BaseModel):
    """A requested metric and aggregation in the semantic query contract."""

    field: str
    aggregation: Literal[
        "sum", "avg", "min", "max", "count", "count_distinct", "last", "none"
    ] = "none"
    alias: Optional[str] = None


class QuerySort(BaseModel):
    """A requested ordering in the semantic query contract."""

    field: str
    direction: Literal["asc", "desc"] = "desc"


class QuerySemantics(BaseModel):
    """The single semantic interpretation consumed by every downstream stage."""

    source_hints: List[str] = Field(default_factory=list)
    source_candidate_ids: List[str] = Field(default_factory=list)
    source_mode: Literal["single", "union", "join", "independent"] = "single"
    dimensions: List[str] = Field(default_factory=list)
    metrics: List[QueryMetric] = Field(default_factory=list)
    filters: List[QueryFilter] = Field(default_factory=list)
    sort: List[QuerySort] = Field(default_factory=list)
    limit: Optional[int] = Field(default=None, ge=1, le=100000)


class SourceBinding(BaseModel):
    """Validated physical source selected for one atomic question."""

    candidate_id: str
    file_id: Optional[str] = None
    file_name: str
    sheet_name: str
    confidence: float = 0.0
    reason: str = ""


class ExecutionReport(BaseModel):
    """Executor-produced provenance; never reconstructed from presentation text."""

    status: Literal["success", "empty", "failed", "skipped"] = "success"
    engine: Optional[Literal["rule", "code", "insight"]] = None
    source_bindings: List[SourceBinding] = Field(default_factory=list)
    source_rows: Optional[int] = None
    result_rows: Optional[int] = None
    fields: List[str] = Field(default_factory=list)
    operations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ExecutionStep(BaseModel):
    """One validated, atomic operation in a query execution plan."""

    step_id: str
    question_id: str = ""
    query: str
    normalized_query: str = ""
    route: RoutingHint
    depends_on: List[str] = Field(default_factory=list)
    input_source: Literal["source", "active_dataframe", "previous_result", "step"] = "source"
    scope_from: Optional[str] = None
    operation_intents: List[str] = Field(default_factory=list)
    output_intents: List[str] = Field(default_factory=lambda: ["auto"])
    output_explicit: bool = False
    needs_new_computation: bool = True
    target_fields: List[str] = Field(default_factory=list)
    semantics: QuerySemantics = Field(default_factory=QuerySemantics)
    source_bindings: List[SourceBinding] = Field(default_factory=list)
    required_source_columns: List[str] = Field(default_factory=list)
    field_resolutions: List["FieldResolutionRecord"] = Field(default_factory=list)
    confidence: float = 0.0
    execution_report: Optional[ExecutionReport] = None


class ExecutionPlan(BaseModel):
    """Shared, inspectable contract for one routed analysis turn."""

    route: RoutingHint
    mode: MultiTurnMode
    response_language: ResponseLanguage = "en"
    original_query: str = ""
    normalized_query: str = ""
    operation_intents: List[str] = Field(default_factory=list)
    output_intents: List[str] = Field(default_factory=lambda: ["auto"])
    output_explicit: bool = False
    needs_new_computation: bool = True
    uses_previous_result: bool = False
    target_fields: List[str] = Field(default_factory=list)
    target_sheets: List[str] = Field(default_factory=list)
    required_source_columns: List[str] = Field(default_factory=list)
    field_resolutions: List["FieldResolutionRecord"] = Field(default_factory=list)
    confidence: float = 0.0
    steps: List[ExecutionStep] = Field(default_factory=list)
    planner_used: bool = False
    planner_source: Literal["single", "llm", "rule_fallback"] = "single"
    reasoning: str = ""


class ResultLineage(BaseModel):
    """Reusable row-scope facts carried independently from display columns."""

    source_scope: str = ""
    filters: Dict[str, List[str]] = Field(default_factory=dict)
    result_filters: Dict[str, List[str]] = Field(default_factory=dict)
    result_columns: List[str] = Field(default_factory=list)


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


class FieldCandidate(BaseModel):
    """One source-column candidate shown when field resolution is uncertain."""

    column: str
    confidence: float
    reason: str


class FieldResolutionRecord(BaseModel):
    """Inspectable field choice stored on execution plans and steps."""

    reference: str
    status: Literal["confirmed", "assumed", "needs_clarification"]
    selected_column: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    candidates: List[FieldCandidate] = Field(default_factory=list)


class FieldResolutionBlock(FieldResolutionRecord):
    """User-visible field assumption or clarification request."""

    kind: Literal["field_resolution"] = "field_resolution"
    message: str


class SheetCandidate(BaseModel):
    """One file/sheet candidate offered for an explicit user decision."""

    candidate_id: str
    file_id: Optional[str] = None
    file_name: str
    sheet_name: str
    columns: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


class SheetResolutionBlock(BaseModel):
    """A low-confidence or user-scope conflict in sheet selection."""

    kind: Literal["sheet_resolution"] = "sheet_resolution"
    status: Literal["scope_conflict", "needs_clarification"]
    message: str
    current_sheets: List[str] = Field(default_factory=list)
    candidates: List[SheetCandidate] = Field(default_factory=list)


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


class CalculationBasis(BaseModel):
    """Concise, user-visible provenance for a computed table result."""

    source_sheets: List[str] = Field(default_factory=list)
    fields: List[str] = Field(default_factory=list)
    operations: List[str] = Field(default_factory=list)
    source_row_count: Optional[int] = None
    result_row_count: Optional[int] = None
    summary: str = ""


class TableBlock(BaseModel):
    """Tabular data result."""
    kind: Literal["table"] = "table"
    title: Optional[str] = None
    columns: List[str]
    rows: List[Dict[str, Any]]
    total_rows: Optional[int] = None
    totals: Optional[Dict[str, Optional[float]]] = None
    columns_metadata: Optional[List[ColumnMeta]] = None
    calculation_basis: Optional[CalculationBasis] = None
    artifact_id: Optional[str] = None
    preview_row_count: Optional[int] = None


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
    x_axis_type: Optional[str] = None
    y_axis_label: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None


class StatusBlock(BaseModel):
    """Typed non-success outcome rendered without pretending it is an insight."""

    kind: Literal["status"] = "status"
    status: Literal["empty", "failed", "partial", "needs_input"]
    message: str
    error_code: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


ResultBlock = Union[
    SummaryBlock,
    FieldResolutionBlock,
    SheetResolutionBlock,
    MetricBlock,
    TableBlock,
    ChartBlock,
    StatusBlock,
]


class QuestionResult(BaseModel):
    """All outputs and provenance for exactly one user sub-question."""

    question_id: str
    query: str
    status: Literal["success", "empty", "failed", "needs_input", "skipped"]
    blocks: List[ResultBlock] = Field(default_factory=list)
    execution_report: Optional[ExecutionReport] = None


# ---------------------------------------------------------------------------
# ResultBlocks — top-level response envelope
# ---------------------------------------------------------------------------

class ResultBlocks(BaseModel):
    """Stable result protocol produced by the analysis runtime."""

    type: Literal["result_blocks"] = "result_blocks"
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    response_language: ResponseLanguage = "en"
    status: Literal[
        "success", "partial", "empty", "failed", "needs_input"
    ] = "success"
    focus_question_id: Optional[str] = None
    output_intents: List[str] = Field(default_factory=lambda: ["auto"])
    questions: List[QuestionResult] = Field(default_factory=list)
    blocks: List[ResultBlock] = Field(default_factory=list)

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

    def focused_table(self) -> Optional[TableBlock]:
        if self.focus_question_id:
            question = next(
                (
                    item for item in self.questions
                    if item.question_id == self.focus_question_id
                ),
                None,
            )
            if question is not None:
                for block in question.blocks:
                    if self._block_kind(block) == "table":
                        return block if isinstance(block, TableBlock) else None
        return self.first_table()

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
    requested_sheet_scope: List[Dict[str, Any]] = Field(default_factory=list)
    conversation: List[Turn] = Field(default_factory=list)
    active_result: Optional[ResultBlocks] = None
    execution_plan: Optional[ExecutionPlan] = None
    active_lineage: Optional[ResultLineage] = None
    active_source_scope: str = ""
    source_scope_changed: bool = False
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    # Runtime-only, not serialized
    _active_df: Any = PrivateAttr(default=None)
    _source_df: Any = PrivateAttr(default=None)
    _result_df: Any = PrivateAttr(default=None)
    _load_report: Any = PrivateAttr(default=None)
    _run_lock: Any = PrivateAttr(default_factory=asyncio.Lock)

    # -------------------------------------------------------------------
    # Conversation helpers
    # -------------------------------------------------------------------

    def add_user_turn(self, query: str) -> None:
        self.conversation.append(Turn(role="user", content=query))

    @staticmethod
    def scope_key(scope: List[Dict[str, Any]]) -> str:
        normalized = []
        for item in scope:
            entry = {
                "fileName": str(item.get("fileName", "")),
                "sheets": sorted(str(sheet) for sheet in item.get("sheets", [])),
            }
            if item.get("fileId"):
                entry["fileId"] = str(item["fileId"])
            normalized.append(entry)
        normalized.sort(key=lambda item: (item.get("fileId", ""), item["fileName"], item["sheets"]))
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)

    def requested_scope_key(self) -> str:
        return self.scope_key(self.requested_sheet_scope)

    def set_requested_sheet_scope(self, scope: List[Dict[str, Any]]) -> None:
        new_scope = [dict(item) for item in scope]
        new_key = self.scope_key(new_scope)
        # The requested scope is the user's candidate pool. active_source_scope
        # is the narrower source selected for the previous question; comparing
        # those two concepts invalidates valid follow-up state on every request.
        old_key = self.requested_scope_key()
        if old_key and old_key != new_key:
            self._active_df = None
            self._source_df = None
            self._result_df = None
            self.active_result = None
            self.active_lineage = None
            self.execution_plan = None
            self.active_source_scope = ""
            self.source_scope_changed = True
            self.selected_sheets = []
        self.requested_sheet_scope = new_scope

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

    def last_tabular_result(self) -> Optional[ResultBlocks]:
        """Return the newest result that still carries reusable row data."""
        for turn in reversed(self.conversation):
            if turn.role == "assistant" and turn.result is not None and turn.result.has_table:
                return turn.result
        if self.active_result is not None and self.active_result.has_table:
            return self.active_result
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
