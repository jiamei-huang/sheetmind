"""
Unit tests for SheetMind analysis skills and tools.
=========================================================
Run from the SheetMind directory:
    python -m pytest tests/test_runtime_phase3.py -v

No real LLM API calls are made.  Tests cover:
  - RoutingClassificationSkill (rule-based path)
  - RuleEngineTool (date filter, sort, keyword filter, pass-through)
  - ChartPlanningSkill (chart type detection, axis selection, negation)
  - DataProfilingSkill (summary string format)
  - SemanticTypingSkill (type detection, alias generation)
  - ResultValidator (table/chart/summary validation)
  - PythonExecutorTool (security scan, basic execution)
  - RepairLoop (unit test with mock code_gen + executor)
  - _build_table_block helper in SheetMindAgent
"""
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

# Make sure the SheetMind package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sheetmind.analysis.context import (
    AnalysisContext,
    ChartBlock,
    ChartSeries,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
    SummaryBlock,
    TableBlock,
)
from sheetmind.analysis.models.configs import ModelRole
from sheetmind.analysis.models.router import ModelRouter
from sheetmind.analysis.skills.chart_planning import ChartPlanningSkill
from sheetmind.analysis.skills.data_profiling import DataProfilingSkill
from sheetmind.analysis.skills.routing_classification import (
    RoutingClassificationSkill,
    RoutingResult,
)
from sheetmind.analysis.skills.semantic_typing import SemanticTypingSkill
from sheetmind.analysis.tools.python_executor import PythonExecutorTool
from sheetmind.analysis.tools.rule_engine import RuleEngineTool
from sheetmind.analysis.validators.result_validator import (
    ResultValidationError,
    validate_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(project_id: str = "test_proj", with_active_result: bool = False) -> AnalysisContext:
    ctx = AnalysisContext(project_id=project_id, task_id="task_001")
    if with_active_result:
        ctx.active_result = ResultBlocks(blocks=[SummaryBlock(content="prev")])
    return ctx


class MockModelProvider:
    """Returns deterministic responses — no real API calls."""
    def __init__(self, response: str = '{"routing":"code","reasoning":"mock"}'):
        self._response = response

    async def complete(self, messages, system="", max_tokens=512, temperature=0.0,
                       json_mode=False, **kwargs):
        return self._response


class MockRouter(ModelRouter):
    def __init__(self, response: str = '{"routing":"code","reasoning":"mock"}'):
        self._mock = MockModelProvider(response)

    def get_provider(self, role: ModelRole):
        return self._mock


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# RoutingClassificationSkill
# ---------------------------------------------------------------------------

class TestRoutingClassificationSkill:
    def setup_method(self):
        self.skill = RoutingClassificationSkill(MockRouter())
        self.ctx = make_ctx()

    def test_rule_engine_date_filter(self):
        result = run(self.skill.run(self.ctx, "筛选2024年10月的订单"))
        assert result.hint == RoutingHint.RULE_ENGINE
        assert result.confidence >= 0.80

    def test_rule_engine_sort(self):
        result = run(self.skill.run(self.ctx, "按销售额降序排序"))
        assert result.hint == RoutingHint.RULE_ENGINE

    def test_code_gen_aggregation(self):
        result = run(self.skill.run(self.ctx, "统计各区域的销售总额"))
        assert result.hint == RoutingHint.CODE_GEN

    def test_code_gen_chart(self):
        result = run(self.skill.run(self.ctx, "画一个柱状图显示月度销售额"))
        assert result.hint == RoutingHint.CODE_GEN

    def test_code_gen_top_n(self):
        result = run(self.skill.run(self.ctx, "前10名销售人员"))
        assert result.hint == RoutingHint.CODE_GEN

    def test_insight_only_analysis(self):
        result = run(self.skill.run(self.ctx, "分析一下这些数据说明什么"))
        assert result.hint == RoutingHint.INSIGHT_ONLY
        assert result.facets.needs_new_computation is False
        assert "explain" in result.facets.operation_types

    def test_mode_new_by_default(self):
        result = run(self.skill.run(self.ctx, "筛选2024年10月的订单"))
        assert result.mode == MultiTurnMode.NEW_QUERY

    def test_mode_follow_up_with_active_result(self):
        ctx = make_ctx(with_active_result=True)
        result = run(self.skill.run(ctx, "继续按金额排序"))
        assert result.mode == MultiTurnMode.FOLLOW_UP

    def test_mode_reset(self):
        ctx = make_ctx(with_active_result=True)
        result = run(self.skill.run(ctx, "重新看全部数据"))
        assert result.mode == MultiTurnMode.RESET

    def test_numeric_operator_triggers_code_gen(self):
        result = run(self.skill.run(self.ctx, "金额 > 10000 的订单"))
        assert result.hint == RoutingHint.CODE_GEN

    def test_which_shop_spends_most_routes_to_rule_engine(self):
        result = run(self.skill.run(self.ctx, "哪个店铺的尾程花费最多"))
        assert result.hint == RoutingHint.RULE_ENGINE

    def test_followup_chart_sentence_is_not_compound(self):
        assert not self.skill._detect_compound("基于上面结果出一个图。更直观看尾程花费")

    def test_result_has_reasoning(self):
        result = run(self.skill.run(self.ctx, "统计销售额"))
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0

    def test_secondary_facets_for_chart_aggregation(self):
        result = run(self.skill.run(self.ctx, "按地区汇总销售额并画柱状图"))
        assert result.hint == RoutingHint.CODE_GEN
        assert result.facets.needs_new_computation is True
        assert result.facets.wants_chart is True
        assert "aggregate" in result.facets.operation_types
        assert "chart" in result.facets.operation_types
        assert "销售额" in result.facets.target_fields


# ---------------------------------------------------------------------------
# RuleEngineTool
# ---------------------------------------------------------------------------

class TestRuleEngineTool:
    def setup_method(self):
        self.tool = RuleEngineTool()
        self.ctx = make_ctx()

        self.df = pd.DataFrame({
            "日期": ["2024-10-01", "2024-10-15", "2024-09-01", "2024-11-01"],
            "金额": [1000, 2000, 500, 3000],
            "区域": ["华东", "华南", "华东", "华北"],
        })

    def test_date_filter_year_month(self):
        result = self.tool.run(self.ctx, query="筛选2024年10月的数据", df=self.df)
        assert len(result) == 2
        assert all("2024-10" in str(d) for d in result["日期"])

    def test_date_filter_year_only(self):
        # All rows in self.df are in 2024; add a 2023 row to verify filter
        df_with_2023 = pd.DataFrame({
            "日期": ["2024-10-01", "2024-09-01", "2023-12-01"],
            "金额": [1000, 500, 800],
            "区域": ["华东", "华东", "华南"],
        })
        result = self.tool.run(self.ctx, query="2024年的数据", df=df_with_2023)
        assert len(result) == 2  # 2023-12-01 excluded

    def test_sort_descending(self):
        result = self.tool.run(self.ctx, query="按金额降序排序", df=self.df)
        vals = list(result["金额"])
        assert vals == sorted(vals, reverse=True)

    def test_sort_ascending(self):
        result = self.tool.run(self.ctx, query="按金额升序排序", df=self.df)
        vals = list(result["金额"])
        assert vals == sorted(vals)

    def test_pass_through_empty_query(self):
        result = self.tool.run(self.ctx, query="", df=self.df)
        assert len(result) == len(self.df)

    def test_pass_through_no_keywords(self):
        result = self.tool.run(self.ctx, query="看看数据", df=self.df)
        assert len(result) == len(self.df)

    def test_returns_dataframe(self):
        result = self.tool.run(self.ctx, query="筛选2024年10月", df=self.df)
        assert isinstance(result, pd.DataFrame)

    def test_shop_tail_cost_extreme_uses_column_names_not_values(self):
        df = pd.DataFrame({
            "平台": ["速卖通", "美国官网", "乐天", "乐天"],
            "店铺": ["速卖通", "美国官网", "乐天", "乐天"],
            "费用金额": [1573.0, 95291.0571641788, 800000.0, 481015.0],
        })

        result = self.tool.run(self.ctx, query="哪个店铺的尾程花费最多", df=df)

        assert list(result.columns) == ["店铺", "费用金额"]
        assert result.iloc[0]["店铺"] == "乐天"
        assert result.iloc[0]["费用金额"] == pytest.approx(1281015.0)
        assert "速卖通" not in result.columns

    def test_filter_by_value_without_column_name(self):
        df = pd.DataFrame({
            "平台": ["速卖通", "美国官网", "美国官网", "乐天"],
            "费用金额": [1573.0, 90000.0, 5291.0571641788, 10.0],
        })

        result = self.tool.run(self.ctx, query="筛选美国官网", df=df)

        assert len(result) == 2
        assert result["费用金额"].sum() == pytest.approx(95291.0571641788)


# ---------------------------------------------------------------------------
# ChartPlanningSkill
# ---------------------------------------------------------------------------

class TestChartPlanningSkill:
    def setup_method(self):
        self.skill = ChartPlanningSkill(MockRouter())
        self.ctx = make_ctx()

    def _make_df(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(kwargs)

    def test_returns_none_for_empty_df(self):
        result = run(self.skill.run(self.ctx, "图表", result_df=pd.DataFrame()))
        assert result is None

    def test_returns_none_for_no_df(self):
        result = run(self.skill.run(self.ctx, "图表"))
        assert result is None

    def test_bar_chart_keyword(self):
        df = self._make_df(区域=["华东", "华南", "华北"], 金额=[1000, 2000, 3000])
        result = run(self.skill.run(self.ctx, "画柱状图", result_df=df))
        assert result is not None
        assert result.chart_type == "bar"

    def test_pie_chart_keyword(self):
        df = self._make_df(区域=["华东", "华南", "华北"], 金额=[1000, 2000, 3000])
        result = run(self.skill.run(self.ctx, "各区域占比饼图", result_df=df))
        assert result is not None
        assert result.chart_type == "pie"

    def test_line_chart_datetime_x(self):
        df = self._make_df(
            日期=pd.to_datetime(["2024-01", "2024-02", "2024-03"]),
            金额=[100, 200, 150],
        )
        result = run(self.skill.run(self.ctx, "趋势图", result_df=df))
        assert result is not None
        assert result.chart_type == "line"

    def test_returns_chart_block(self):
        df = self._make_df(区域=["A", "B", "C"], 金额=[10, 20, 30])
        result = run(self.skill.run(self.ctx, "柱图", result_df=df))
        assert isinstance(result, ChartBlock)
        assert len(result.labels) == 3
        assert len(result.series) >= 1

    def test_series_length_matches_labels(self):
        df = self._make_df(区域=["A", "B", "C"], 金额=[10, 20, 30])
        result = run(self.skill.run(self.ctx, "图", result_df=df))
        assert result is not None
        for s in result.series:
            assert len(s.values) == len(result.labels)

    def test_negation_excludes_column(self):
        df = self._make_df(
            区域=["A", "B"],
            金额=[10, 20],
            数量=[5, 8],
        )
        result = run(self.skill.run(self.ctx, "不用数量列，按区域画图", result_df=df))
        # 数量 should not appear as y-axis
        assert result is not None
        y_names = [s.name for s in result.series]
        assert "数量" not in y_names


# ---------------------------------------------------------------------------
# DataProfilingSkill
# ---------------------------------------------------------------------------

class TestDataProfilingSkill:
    def setup_method(self):
        self.skill = DataProfilingSkill(MockRouter())
        self.ctx = make_ctx()

    def test_returns_string(self):
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        result = run(self.skill.run(self.ctx, "test", df=df))
        assert isinstance(result, str)

    def test_contains_row_count(self):
        df = pd.DataFrame({"A": range(50)})
        result = run(self.skill.run(self.ctx, "test", df=df))
        assert "50" in result

    def test_contains_column_names(self):
        df = pd.DataFrame({"销售额": [1], "区域": ["东"]})
        result = run(self.skill.run(self.ctx, "test", df=df))
        assert "销售额" in result
        assert "区域" in result

    def test_handles_empty_df(self):
        df = pd.DataFrame()
        result = run(self.skill.run(self.ctx, "test", df=df))
        assert isinstance(result, str)  # should not crash


# ---------------------------------------------------------------------------
# SemanticTypingSkill
# ---------------------------------------------------------------------------

class TestSemanticTypingSkill:
    def setup_method(self):
        self.skill = SemanticTypingSkill(MockRouter())
        self.ctx = make_ctx()

    def test_detects_numeric_column(self):
        df = pd.DataFrame({"金额": [100, 200], "区域": ["东", "西"]})
        fm = run(self.skill.run(self.ctx, "test", df=df))
        assert fm["金额"].type == "numeric"

    def test_detects_categorical_column(self):
        df = pd.DataFrame({"金额": [100], "区域": ["东"]})
        fm = run(self.skill.run(self.ctx, "test", df=df))
        assert fm["区域"].type == "categorical"

    def test_generates_aliases(self):
        df = pd.DataFrame({"销售额（元）": [100]})
        fm = run(self.skill.run(self.ctx, "test", df=df))
        col = "销售额（元）"
        assert col in fm
        # Should have at least the original + stripped version
        assert len(fm[col].aliases) >= 1

    def test_returns_dict(self):
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        fm = run(self.skill.run(self.ctx, "test", df=df))
        assert isinstance(fm, dict)
        assert "A" in fm and "B" in fm


# ---------------------------------------------------------------------------
# ResultValidator
# ---------------------------------------------------------------------------

class TestResultValidator:
    def _make_result(self, blocks) -> ResultBlocks:
        return ResultBlocks(blocks=blocks)

    def test_valid_table_passes(self):
        tb = TableBlock(
            columns=["A", "B"],
            rows=[{"A": 1, "B": "x"}, {"A": 2, "B": "y"}],
        )
        result = self._make_result([tb])
        validated = validate_result(result)
        assert validated is result

    def test_empty_columns_raises(self):
        tb = TableBlock(columns=[], rows=[])
        result = self._make_result([tb])
        with pytest.raises(ResultValidationError):
            validate_result(result)

    def test_valid_chart_passes(self):
        cb = ChartBlock(
            chart_type="bar",
            labels=["A", "B"],
            series=[ChartSeries(name="s", values=[1.0, 2.0])],
        )
        result = self._make_result([cb])
        validate_result(result)  # should not raise

    def test_invalid_chart_type_raises(self):
        cb = ChartBlock(
            chart_type="radar",  # not in allowed set
            labels=["A"],
            series=[ChartSeries(name="s", values=[1.0])],
        )
        result = self._make_result([cb])
        with pytest.raises(ResultValidationError):
            validate_result(result)

    def test_series_length_mismatch_raises(self):
        cb = ChartBlock(
            chart_type="bar",
            labels=["A", "B", "C"],
            series=[ChartSeries(name="s", values=[1.0, 2.0])],  # 2 values, 3 labels
        )
        result = self._make_result([cb])
        with pytest.raises(ResultValidationError):
            validate_result(result)

    def test_empty_summary_does_not_raise(self):
        sb = SummaryBlock(content="")
        result = self._make_result([sb])
        validate_result(result)  # should warn but not raise

    def test_empty_blocks_does_not_raise(self):
        result = ResultBlocks(blocks=[])
        validate_result(result)  # should warn but not raise


# ---------------------------------------------------------------------------
# PythonExecutorTool — security scan
# ---------------------------------------------------------------------------

class TestPythonExecutorTool:
    def setup_method(self):
        self.executor = PythonExecutorTool()
        self.ctx = make_ctx()
        self.df = pd.DataFrame({"A": [1, 2, 3], "B": [10, 20, 30]})

    def test_security_scan_blocks_import_os(self):
        code = "import os\nresult_df = df"
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert result_df is None
        assert error is not None
        assert "security" in error.lower() or "import" in error.lower()

    def test_security_scan_blocks_exec(self):
        code = "exec('print(1)')\nresult_df = df"
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert result_df is None
        assert error is not None

    def test_security_scan_blocks_eval(self):
        code = "eval('1+1')\nresult_df = df"
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert result_df is None
        assert error is not None

    def test_valid_code_returns_dataframe(self):
        code = "result_df = df[df['A'] > 1]"
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert error is None
        assert result_df is not None
        assert isinstance(result_df, pd.DataFrame)
        assert len(result_df) == 2

    def test_aggregation_code(self):
        code = "result_df = df.groupby('A').sum().reset_index()"
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert error is None
        assert result_df is not None

    def test_missing_result_df_returns_error(self):
        code = "x = 1 + 1"  # no result_df assigned
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert result_df is None
        assert error is not None

    def test_syntax_error_returns_error(self):
        code = "result_df = df[["  # syntax error
        result_df, error = self.executor.run(self.ctx, code=code, df=self.df)
        assert result_df is None
        assert error is not None


# ---------------------------------------------------------------------------
# RepairLoop (mocked code_gen + executor)
# ---------------------------------------------------------------------------

class TestRepairLoop:
    def setup_method(self):
        from sheetmind.analysis.harness.repair_loop import RepairLoop

        self.ctx = make_ctx()
        self.df = pd.DataFrame({"A": [1, 2, 3]})

        # Happy-path mock: code gen returns valid code, executor returns df
        self.mock_code_gen = MagicMock()
        self.mock_executor = MagicMock()

        self.loop = RepairLoop(self.mock_code_gen, self.mock_executor)

    def test_success_on_first_attempt(self):
        self.mock_code_gen.run = AsyncMock(return_value="result_df = df")
        self.mock_executor.run = MagicMock(return_value=(self.df, None))

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )
        assert result_df is not None
        assert repairs == 0
        assert self.mock_code_gen.run.call_count == 1

    def test_repair_on_first_failure(self):
        error_df = None
        # First call fails, second succeeds
        self.mock_code_gen.run = AsyncMock(side_effect=[
            "bad_code = df",
            "result_df = df",
        ])
        self.mock_executor.run = MagicMock(side_effect=[
            (None, "NameError: name 'bad_code' is not defined"),
            (self.df, None),
        ])

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )
        assert result_df is not None
        assert repairs > 0
        assert self.mock_code_gen.run.call_count == 2

    def test_all_attempts_fail_returns_none(self):
        self.mock_code_gen.run = AsyncMock(return_value="bad_code = df")
        self.mock_executor.run = MagicMock(return_value=(None, "error"))

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )
        assert result_df is None
        # Should have tried 1 + MAX_REPAIRS times
        from sheetmind.analysis.harness.repair_loop import MAX_REPAIRS
        assert self.mock_code_gen.run.call_count == 1 + MAX_REPAIRS


# ---------------------------------------------------------------------------
# SheetMindAgent._build_table_block helper
# ---------------------------------------------------------------------------

class TestBuildTableBlock:
    def test_basic_conversion(self):
        from sheetmind.analysis.agent import SheetMindAgent
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        block = SheetMindAgent._build_table_block(df)
        assert block is not None
        assert block.columns == ["A", "B"]
        assert len(block.rows) == 2
        assert block.rows[0]["A"] == 1

    def test_empty_df_returns_none(self):
        from sheetmind.analysis.agent import SheetMindAgent
        block = SheetMindAgent._build_table_block(pd.DataFrame())
        assert block is None

    def test_total_rows_set(self):
        from sheetmind.analysis.agent import SheetMindAgent
        df = pd.DataFrame({"A": range(10)})
        block = SheetMindAgent._build_table_block(df)
        assert block is not None
        assert block.total_rows == 10

    def test_column_metadata_numeric(self):
        from sheetmind.analysis.agent import SheetMindAgent
        df = pd.DataFrame({"金额": [1.5, 2.5], "名称": ["a", "b"]})
        block = SheetMindAgent._build_table_block(df)
        assert block is not None
        meta = {m.name: m for m in (block.columns_metadata or [])}
        assert meta["金额"].type == "numeric"
        assert meta["名称"].type == "categorical"

    def test_nan_converted_to_none(self):
        import math
        from sheetmind.analysis.agent import SheetMindAgent
        df = pd.DataFrame({"A": [1.0, float("nan")]})
        block = SheetMindAgent._build_table_block(df)
        assert block is not None
        assert block.rows[1]["A"] is None


# ---------------------------------------------------------------------------
# SheetMindAgent pipeline regressions
# ---------------------------------------------------------------------------

class TestSheetMindAgentPipeline:
    def test_insight_only_loads_dataframe_for_insight_without_table_output(self):
        from sheetmind.analysis.agent import SheetMindAgent
        from sheetmind.analysis.tracing.trace import Trace

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        df = pd.DataFrame({"地区": ["华东", "华北"], "销售额": [100, 80]})
        seen = {}

        async def capture_insight(*args, **kwargs):
            seen["scenario"] = kwargs.get("scenario")
            seen["result_df"] = kwargs.get("result_df")
            return "华东销售额更高。"

        agent.routing_skill.run = AsyncMock(return_value=RoutingResult(
            hint=RoutingHint.INSIGHT_ONLY,
            mode=MultiTurnMode.NEW_QUERY,
            confidence=0.9,
            reasoning="insight",
        ))
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=df)
        agent.semantic_skill.run = AsyncMock(return_value={})
        agent.profiling_skill.run = AsyncMock(return_value="profile")
        agent.insight_skill.run = AsyncMock(side_effect=capture_insight)

        result, hint, mode = run(agent._run_pipeline(ctx, "这份数据说明什么", Trace(), None))

        assert hint == RoutingHint.INSIGHT_ONLY
        assert mode == MultiTurnMode.NEW_QUERY
        assert agent.df_loader.run.call_count == 1
        assert seen["scenario"] == "insight_only"
        assert seen["result_df"] is df
        assert result.has_summary
        assert not result.has_table

    def test_result_dataframe_only_replaces_active_df_for_same_columns(self):
        from sheetmind.analysis.agent import SheetMindAgent

        ctx = make_ctx()
        source_df = pd.DataFrame({"地区": ["华东", "华北"], "销售额": [100, 80]})
        filtered_df = source_df.iloc[:1].copy()
        aggregated_df = pd.DataFrame({"地区": ["华东"], "销售额合计": [100]})

        ctx._active_df = source_df
        SheetMindAgent._remember_result_dataframe(ctx, source_df, filtered_df)
        assert ctx._result_df is filtered_df
        assert ctx._active_df is filtered_df

        ctx._active_df = source_df
        SheetMindAgent._remember_result_dataframe(ctx, source_df, aggregated_df)
        assert ctx._result_df is aggregated_df
        assert ctx._active_df is source_df

    def test_followup_chart_uses_previous_result_without_codegen(self):
        from sheetmind.analysis.agent import SheetMindAgent
        from sheetmind.analysis.tracing.trace import Trace

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx(with_active_result=True)
        previous_df = pd.DataFrame({
            "店铺": ["乐天", "日本官网", "雅虎"],
            "费用金额": [1281015.0, 568528.1, 337681.3],
        })
        ctx._active_df = pd.DataFrame({
            "店铺": ["raw"],
            "费用金额": [1],
            "月份": [45992],
        })
        ctx._result_df = previous_df
        ctx.active_result = ResultBlocks(blocks=[
            SummaryBlock(content="prev"),
            TableBlock(
                columns=["店铺", "费用金额"],
                rows=previous_df.to_dict("records"),
            ),
        ])

        chart = ChartBlock(
            chart_type="bar",
            labels=["乐天", "日本官网", "雅虎"],
            series=[
                ChartSeries(
                    name="费用金额",
                    values=[1281015.0, 568528.1, 337681.3],
                )
            ],
            x_axis_label="店铺",
            y_axis_label="费用金额",
        )
        agent.routing_skill.run = AsyncMock(return_value=RoutingResult(
            hint=RoutingHint.CODE_GEN,
            mode=MultiTurnMode.FOLLOW_UP,
            confidence=0.9,
            reasoning="follow-up chart",
            is_compound=True,
        ))
        agent.df_loader.run = MagicMock(side_effect=AssertionError("should not load source data"))
        agent.repair_loop.run = AsyncMock(side_effect=AssertionError("should not codegen"))
        agent.chart_skill.run = AsyncMock(return_value=chart)
        agent.insight_skill.run = AsyncMock(return_value="基于上一次结果生成图表。")

        result, hint, mode = run(agent._run_pipeline(
            ctx,
            "基于上面结果出一个图。更直观看尾程花费",
            Trace(),
            None,
        ))

        assert hint == RoutingHint.CODE_GEN
        assert mode == MultiTurnMode.FOLLOW_UP
        assert result.has_chart
        assert not result.has_table
        assert agent.df_loader.run.call_count == 0
        assert agent.repair_loop.run.await_count == 0
        assert agent.chart_skill.run.call_args.kwargs["result_df"] is previous_df
        assert all(getattr(block, "kind", "") != "table" for block in result.blocks)


# ---------------------------------------------------------------------------
# Data source selection regressions
# ---------------------------------------------------------------------------

class TestDataSourceSelection:
    def test_sheet_selector_prefers_query_matched_sheet(self):
        from sheetmind.services.sheet_selection import SheetSelector

        selector = SheetSelector()
        sheets = selector._select_relevant_sheets(
            ["尾程", "仓储", "Sheet1", "平台匹配"],
            "哪个店铺的尾程花费最多",
        )

        assert sheets == ["尾程"]

    def test_excel_service_get_file_by_name_uses_latest_upload(self, tmp_path, monkeypatch):
        import sqlite3
        from sheetmind.services import excel as excel_service_module
        from sheetmind.services.excel import ExcelService

        db_path = tmp_path / "sheetmind-test.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE files (
                file_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_data BLOB,
                created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
            ("old", "p1", "same.xlsx", b"old-bytes", "2026-01-01 00:00:00"),
        )
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
            ("new", "p1", "same.xlsx", b"new-bytes", "2026-09-07 10:00:00"),
        )
        conn.commit()
        conn.close()

        def get_test_connection():
            test_conn = sqlite3.connect(db_path)
            test_conn.row_factory = sqlite3.Row
            return test_conn

        monkeypatch.setattr(excel_service_module, "get_db_connection", get_test_connection)

        assert ExcelService().get_file_by_name("p1", "same.xlsx") == b"new-bytes"

    def test_dataframe_loader_detects_duplicate_business_header(self):
        import io
        from sheetmind.analysis.tools.dataframe_loader import (
            DataframeLoaderTool,
            MAX_ROWS_PER_SHEET,
        )

        raw = pd.DataFrame([
            [None, None, None, 49862.72, None, None],
            ["月份", "平台", "店铺", "费用金额", "平台", "店铺"],
            [45992, "速卖通", "速卖通应急电源店", 433, "aliexpress", "aliexpress_shop"],
            [45992, "美国官网", "美国官网", 95291.0571641788, "official", "us_site"],
        ])
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            raw.to_excel(writer, sheet_name="尾程", header=False, index=False)
        blob = buf.getvalue()

        assert MAX_ROWS_PER_SHEET is None
        assert DataframeLoaderTool._detect_header_row(blob, "尾程") == 1

        [df] = DataframeLoaderTool()._load_sheets(blob, "tail.xlsx", ["尾程"])
        assert list(df.columns) == ["月份", "平台", "店铺", "费用金额", "平台.1", "店铺.1"]
        assert df.iloc[0]["平台"] == "速卖通"


# ---------------------------------------------------------------------------
# Enhancement acceptance regressions
# ---------------------------------------------------------------------------

class TestEnhancementAcceptance:
    def test_semantic_typing_identifies_period_and_identifier(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({
            "月份": ["2025-01", "2025-02"],
            "订单号": ["000001234567", "000001234568"],
            "金额": [12.5, 20.0],
        })

        field_map = run(skill.run(make_ctx(), "按月份汇总金额", df=df))

        assert field_map["月份"].type == "datetime-like"
        assert field_map["订单号"].type == "identifier"
        assert field_map["订单号"].should_aggregate is False
        assert field_map["金额"].should_aggregate is True
        assert field_map["金额"].confidence > 0.8

    def test_field_resolver_prefers_rmb_qualified_metric(self):
        from sheetmind.analysis.skills.field_resolution import FieldResolver

        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"金额": [10], "金额（RMB）": [70], "金额（USD）": [5]})
        field_map = run(skill.run(make_ctx(), "人民币金额是多少", df=df))

        match = FieldResolver().resolve("人民币金额是多少", field_map, aggregate_only=True)

        assert match is not None
        assert match.column == "金额（RMB）"

    def test_profile_exposes_structured_column_metadata(self):
        skill = DataProfilingSkill(MockRouter())
        df = pd.DataFrame({"日期": pd.to_datetime(["2025-01-01", "2025-01-02"]), "销售额": [10, 20]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(make_ctx(), "销售额", df=df))

        profile = run(skill.run(make_ctx(), "销售额", df=df, field_map=field_map))

        assert isinstance(profile, str)
        assert profile.row_count == 2
        assert "销售额" in profile.metric_candidates
        sales_profile = next(column for column in profile.columns if column.name == "销售额")
        assert sales_profile.numeric_stats["sum"] == 30.0

    def test_chart_planning_does_not_use_identifier_as_y_axis(self):
        skill = ChartPlanningSkill(MockRouter())
        df = pd.DataFrame({"SKU": [10001, 10002, 10003], "地区": ["东", "西", "南"], "销售额": [30, 20, 10]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(make_ctx(), "按地区画图", df=df))

        chart = run(skill.run(make_ctx(), "按地区画柱状图", result_df=df, field_map=field_map))

        assert chart is not None
        assert [series.name for series in chart.series] == ["销售额"]
        assert chart.confidence is not None
        assert chart.reason

    def test_executor_returns_structured_safety_result(self):
        executor = PythonExecutorTool()
        detail = executor.run_detailed(make_ctx(), code="import os\nresult_df = df", df=pd.DataFrame({"A": [1]}))

        assert detail.success is False
        assert detail.safety_violation is True
        assert detail.error_type == "safety_violation"

    def test_invalid_chart_degrades_to_table_and_summary(self):
        table = TableBlock(columns=["A"], rows=[{"A": 1}])
        invalid_chart = ChartBlock(chart_type="bar", labels=["A", "B"], series=[ChartSeries(name="x", values=[1.0])])
        result = validate_result(ResultBlocks(blocks=[table, invalid_chart]), degrade_invalid_charts=True)

        assert result.has_table
        assert result.has_chart is False
        assert result.has_summary
