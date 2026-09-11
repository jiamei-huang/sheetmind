"""Public interface for the SheetMind analysis runtime.

Import these from route handlers and tests:

    from sheetmind.analysis import (
        SheetMindAgent,
        AnalysisContext,
        FileRef,
        ResultBlocks,
        RoutingHint,
        MultiTurnMode,
        StreamEmitter,
        ContextStore,
        get_context_store,
        get_trace_store,
    )
"""
from .agent import SheetMindAgent
from .context import (
    AnalysisContext,
    ChartBlock,
    ChartSeries,
    ColumnMeta,
    ExecutionPlan,
    ExecutionStep,
    FieldCandidate,
    FieldResolutionBlock,
    FieldResolutionRecord,
    FileRef,
    MetricBlock,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
    SheetCandidate,
    SheetResolutionBlock,
    SummaryBlock,
    TableBlock,
    Turn,
)
from .context_store import ContextStore, get_context_store
from .models.router import ModelRouter
from .streaming.emitter import StreamEmitter
from .tracing.storage import get_trace_store
from .tracing.trace import Trace

__all__ = [
    # Agent
    "SheetMindAgent",
    # Context + data models
    "AnalysisContext",
    "FileRef",
    "Turn",
    "ResultBlocks",
    "SummaryBlock",
    "MetricBlock",
    "TableBlock",
    "ChartBlock",
    "ChartSeries",
    "ColumnMeta",
    "ExecutionPlan",
    "ExecutionStep",
    "FieldCandidate",
    "FieldResolutionBlock",
    "FieldResolutionRecord",
    "SheetCandidate",
    "SheetResolutionBlock",
    # Routing enums
    "RoutingHint",
    "MultiTurnMode",
    # Context store
    "ContextStore",
    "get_context_store",
    # Infrastructure
    "ModelRouter",
    "StreamEmitter",
    "Trace",
    "get_trace_store",
]
