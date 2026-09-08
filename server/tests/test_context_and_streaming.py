"""
Unit tests for analysis context, tracing, model routing, and streaming.
====================================================
Run from the SheetMind directory:
    python -m pytest tests/test_runtime_phase2.py -v

No model API calls are made.  Tests cover:
  - AnalysisContext construction, multi-turn helpers
  - ResultBlocks block detection properties
  - RoutingHint / MultiTurnMode enum values
  - Turn serialization round-trip
  - Trace add_event / finish
  - TraceStore save / get / list_recent
  - StreamEmitter frame format
  - ModelRouter summary + env override
"""
import asyncio
import json
import os
import sys

# Make sure the SheetMind package is importable when running from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from sheetmind.analysis.context import (
    AnalysisContext,
    ChartBlock,
    ChartSeries,
    FileRef,
    MetricBlock,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
    SummaryBlock,
    TableBlock,
    Turn,
)
from sheetmind.analysis.models.configs import ModelRole
from sheetmind.analysis.models.router import ModelRouter
from sheetmind.analysis.streaming.emitter import (
    StreamEmitter,
    done_frame,
    error_frame,
    progress_frame,
    thinking_frame,
)
from sheetmind.analysis.tracing.storage import TraceStore
from sheetmind.analysis.tracing.trace import (
    EVT_MODEL_CALL,
    EVT_RESULT_ASSEMBLED,
    Trace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(**kwargs) -> AnalysisContext:
    defaults = {"project_id": "proj-1", "task_id": "task-1"}
    defaults.update(kwargs)
    return AnalysisContext(**defaults)


# ---------------------------------------------------------------------------
# RoutingHint + MultiTurnMode
# ---------------------------------------------------------------------------

class TestRoutingEnums:
    def test_routing_hint_values(self):
        assert RoutingHint.RULE_ENGINE.value == "rule"
        assert RoutingHint.CODE_GEN.value == "code"
        assert RoutingHint.INSIGHT_ONLY.value == "insight"

    def test_routing_hint_from_string(self):
        assert RoutingHint("rule") is RoutingHint.RULE_ENGINE
        assert RoutingHint("code") is RoutingHint.CODE_GEN
        assert RoutingHint("insight") is RoutingHint.INSIGHT_ONLY

    def test_multiturn_mode_values(self):
        assert MultiTurnMode.NEW_QUERY.value == "new"
        assert MultiTurnMode.FOLLOW_UP.value == "follow_up"
        assert MultiTurnMode.RESET.value == "reset"


# ---------------------------------------------------------------------------
# ResultBlocks
# ---------------------------------------------------------------------------

class TestResultBlocks:
    def _summary_result(self) -> ResultBlocks:
        return ResultBlocks(blocks=[SummaryBlock(content="insight text")])

    def _table_result(self) -> ResultBlocks:
        return ResultBlocks(blocks=[
            TableBlock(columns=["A", "B"], rows=[{"A": 1, "B": 2}]),
        ])

    def _chart_result(self) -> ResultBlocks:
        return ResultBlocks(blocks=[
            ChartBlock(
                chart_type="bar",
                labels=["Jan", "Feb"],
                series=[ChartSeries(name="Sales", values=[100.0, 200.0])],
            )
        ])

    def _combo_result(self) -> ResultBlocks:
        return ResultBlocks(blocks=[
            SummaryBlock(content="overview"),
            TableBlock(columns=["X"], rows=[{"X": 1}]),
            ChartBlock(chart_type="line", labels=["Q1"], series=[
                ChartSeries(name="Rev", values=[1000.0])
            ]),
        ])

    def test_has_table_false(self):
        assert self._summary_result().has_table is False

    def test_has_table_true(self):
        assert self._table_result().has_table is True

    def test_has_chart_false(self):
        assert self._table_result().has_chart is False

    def test_has_chart_true(self):
        assert self._chart_result().has_chart is True

    def test_has_summary(self):
        assert self._summary_result().has_summary is True
        assert self._table_result().has_summary is False

    def test_combo_properties(self):
        r = self._combo_result()
        assert r.has_table
        assert r.has_chart
        assert r.has_summary

    def test_all_summaries(self):
        r = self._summary_result()
        assert r.all_summaries() == ["insight text"]

    def test_first_table(self):
        r = self._combo_result()
        t = r.first_table()
        assert t is not None
        assert t.columns == ["X"]

    def test_first_chart(self):
        r = self._combo_result()
        c = r.first_chart()
        assert c is not None
        assert c.chart_type == "line"

    def test_result_blocks_default_type(self):
        r = ResultBlocks()
        assert r.type == "result_blocks"


# ---------------------------------------------------------------------------
# AnalysisContext
# ---------------------------------------------------------------------------

class TestAnalysisContext:
    def test_construction_minimal(self):
        ctx = make_ctx()
        assert ctx.project_id == "proj-1"
        assert ctx.task_id == "task-1"
        assert ctx.conversation == []
        assert ctx.active_result is None
        assert ctx.trace_id  # non-empty uuid hex

    def test_add_user_turn(self):
        ctx = make_ctx()
        ctx.add_user_turn("按月份汇总销售额")
        assert len(ctx.conversation) == 1
        assert ctx.conversation[0].role == "user"
        assert ctx.conversation[0].content == "按月份汇总销售额"

    def test_add_assistant_turn_updates_active_result(self):
        ctx = make_ctx()
        result = ResultBlocks(blocks=[SummaryBlock(content="done")])
        ctx.add_assistant_turn(
            summary="done",
            result=result,
            routing_hint=RoutingHint.CODE_GEN,
            multiturn_mode=MultiTurnMode.NEW_QUERY,
        )
        assert ctx.active_result is result
        assert ctx.conversation[-1].routing_hint == RoutingHint.CODE_GEN

    def test_add_assistant_turn_none_result_does_not_clear_active(self):
        ctx = make_ctx()
        result = ResultBlocks(blocks=[SummaryBlock(content="first")])
        ctx.add_assistant_turn("first", result)
        ctx.add_assistant_turn("second", None)
        assert ctx.active_result is result  # not overwritten

    def test_last_assistant_result(self):
        ctx = make_ctx()
        ctx.add_user_turn("q1")
        r1 = ResultBlocks(blocks=[SummaryBlock(content="r1")])
        ctx.add_assistant_turn("r1", r1)
        ctx.add_user_turn("q2")
        r2 = ResultBlocks(blocks=[SummaryBlock(content="r2")])
        ctx.add_assistant_turn("r2", r2)
        assert ctx.last_assistant_result() is r2

    def test_conversation_text(self):
        ctx = make_ctx()
        ctx.add_user_turn("show me sales")
        ctx.add_assistant_turn("here are the sales", None)
        text = ctx.conversation_text()
        assert "User: show me sales" in text
        assert "Assistant: here are the sales" in text

    def test_previous_routing_hint(self):
        ctx = make_ctx()
        ctx.add_user_turn("q")
        ctx.add_assistant_turn("a", None, routing_hint=RoutingHint.RULE_ENGINE)
        assert ctx.previous_routing_hint() == RoutingHint.RULE_ENGINE

    def test_with_file_ref(self):
        ctx = AnalysisContext(
            project_id="p",
            task_id="t",
            files=[FileRef(file_id="f1", file_name="sales.xlsx", sheet_names=["Sheet1"])],
            selected_sheets=["Sheet1"],
        )
        assert ctx.files[0].file_name == "sales.xlsx"


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_add_event(self):
        trace = Trace(project_id="p", task_id="t", query="test")
        evt = trace.add_event(EVT_RESULT_ASSEMBLED, output_summary="3 blocks")
        assert evt.event_type == EVT_RESULT_ASSEMBLED
        assert len(trace.events) == 1

    def test_model_calls_counter(self):
        trace = Trace()
        trace.add_event(EVT_MODEL_CALL, model_role="routing")
        trace.add_event(EVT_MODEL_CALL, model_role="code_generation")
        assert trace.model_calls == 2

    def test_finish_sets_timestamps_and_duration(self):
        trace = Trace()
        trace.finish(success=True)
        assert trace.finished_at is not None
        assert trace.success is True
        assert trace.total_duration_ms is not None
        assert trace.total_duration_ms >= 0

    def test_finish_error(self):
        trace = Trace()
        trace.finish(success=False, error="something went wrong")
        assert trace.success is False
        assert trace.error == "something went wrong"

    def test_summary_line(self):
        trace = Trace(task_id="t1")
        trace.routing_hint = "code"
        trace.finish(success=True)
        line = trace.summary_line()
        assert "t1" in line
        assert "code" in line
        assert "OK" in line


# ---------------------------------------------------------------------------
# TraceStore
# ---------------------------------------------------------------------------

class TestTraceStore:
    def test_save_and_get(self):
        store = TraceStore()
        trace = Trace(task_id="t1")
        store.save(trace)
        assert store.get(trace.trace_id) is trace

    def test_get_missing_returns_none(self):
        store = TraceStore()
        assert store.get("nonexistent") is None

    def test_list_recent_order(self):
        store = TraceStore()
        traces = [Trace(task_id=f"t{i}") for i in range(5)]
        for t in traces:
            store.save(t)
        recent = store.list_recent(limit=3)
        assert len(recent) == 3
        # most recent first
        assert recent[0].task_id == "t4"
        assert recent[1].task_id == "t3"
        assert recent[2].task_id == "t2"

    def test_eviction_at_capacity(self):
        store = TraceStore(max_traces=3)
        traces = [Trace(task_id=f"t{i}") for i in range(4)]
        for t in traces:
            store.save(t)
        assert store.count == 3
        # oldest (t0) should be evicted
        assert store.get(traces[0].trace_id) is None
        assert store.get(traces[3].trace_id) is not None

    def test_update_in_place(self):
        store = TraceStore()
        trace = Trace(task_id="t1")
        store.save(trace)
        trace.success = True
        store.save(trace)  # update
        assert store.count == 1
        assert store.get(trace.trace_id).success is True


# ---------------------------------------------------------------------------
# StreamEmitter
# ---------------------------------------------------------------------------

class TestStreamEmitter:
    def _frame_event(self, frame_str: str) -> str:
        # frames are "data: {...}\n\n"
        line = frame_str.strip()
        assert line.startswith("data: ")
        payload = json.loads(line[len("data: "):])
        return payload["event"]

    def test_thinking_frame(self):
        frame = thinking_frame()
        assert self._frame_event(frame) == "thinking"

    def test_progress_frame(self):
        frame = progress_frame("正在查询...")
        assert self._frame_event(frame) == "progress"
        payload = json.loads(frame.strip()[len("data: "):])
        assert payload["message"] == "正在查询..."

    def test_progress_frame_includes_stable_step_id(self):
        frame = progress_frame("正在加载数据...", step_id="data_loading")
        payload = json.loads(frame.strip()[len("data: "):])
        assert payload["step_id"] == "data_loading"

    def test_done_frame_contains_result(self):
        result = {"type": "result_blocks", "blocks": []}
        frame = done_frame(result)
        payload = json.loads(frame.strip()[len("data: "):])
        assert payload["event"] == "done"
        assert payload["result"] == result

    def test_error_frame(self):
        frame = error_frame("出错了")
        payload = json.loads(frame.strip()[len("data: "):])
        assert payload["event"] == "error"
        assert "出错了" in payload["message"]

    def test_stream_yields_frames_then_exits(self):
        async def run():
            emitter = StreamEmitter()
            await emitter.emit_thinking()
            await emitter.emit_progress("step 1")
            await emitter.emit_done({"type": "result_blocks", "blocks": []})

            frames = []
            async for frame in emitter.stream():
                frames.append(frame)
            return frames

        frames = asyncio.get_event_loop().run_until_complete(run())
        events = [
            json.loads(f.strip()[len("data: "):])["event"]
            for f in frames
        ]
        assert events == ["thinking", "progress", "done"]

    def test_emit_after_close_is_ignored(self):
        async def run():
            emitter = StreamEmitter()
            await emitter.emit_done({"type": "result_blocks", "blocks": []})
            # after done, further emits should be no-ops
            await emitter.emit_progress("too late")

            frames = []
            async for frame in emitter.stream():
                frames.append(frame)
            return frames

        frames = asyncio.get_event_loop().run_until_complete(run())
        events = [json.loads(f.strip()[len("data: "):])["event"] for f in frames]
        # "too late" should NOT appear
        assert "progress" not in events
        assert events == ["done"]


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class TestModelRouter:
    def test_summary_contains_all_roles(self):
        router = ModelRouter()
        summary = router.summary()
        for role in ModelRole:
            assert role.value in summary

    def test_get_provider_returns_provider(self):
        router = ModelRouter()
        from sheetmind.analysis.models.provider import ModelProvider
        p = router.get_provider(ModelRole.ROUTING)
        assert isinstance(p, ModelProvider)

    def test_get_provider_caches(self):
        router = ModelRouter()
        p1 = router.get_provider(ModelRole.ROUTING)
        p2 = router.get_provider(ModelRole.ROUTING)
        assert p1 is p2

    def test_override_via_dict(self):
        from sheetmind.analysis.models.configs import ModelConfig
        overrides = {
            ModelRole.CODE_GENERATION: ModelConfig(
                provider="openai", model_id="gpt-4o-custom"
            )
        }
        router = ModelRouter(overrides=overrides)
        assert router.config_for(ModelRole.CODE_GENERATION).model_id == "gpt-4o-custom"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SHEETMIND_MODEL_ROUTING_ID", "gpt-3.5-turbo")
        router = ModelRouter()
        assert router.config_for(ModelRole.ROUTING).model_id == "gpt-3.5-turbo"
