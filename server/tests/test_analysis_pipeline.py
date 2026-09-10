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
from datetime import date
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
    ExecutionStep,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
    SummaryBlock,
    TableBlock,
)
from sheetmind.analysis.models.configs import ModelRole
from sheetmind.analysis.models.router import ModelRouter
from sheetmind.analysis.skills.query_planning import QueryPlan, QueryPlanningSkill
from sheetmind.analysis.skills.query_normalization import QueryNormalizationSkill
from sheetmind.analysis.skills.output_planning import OutputPlanningSkill
from sheetmind.analysis.skills import routing_classification as routing_rules_module
from sheetmind.analysis.skills.chart_planning import ChartPlanningSkill
from sheetmind.analysis.skills.code_generation import CodeGenerationSkill
from sheetmind.analysis.skills.data_profiling import DataProfilingSkill
from sheetmind.analysis.skills.field_resolution import FieldResolver
from sheetmind.analysis.skills.routing_classification import (
    OperationIntent,
    OutputIntent,
    RoutingClassificationSkill,
    RoutingResult,
)
from sheetmind.analysis.skills.semantic_typing import SemanticTypingSkill
from sheetmind.analysis.tools.python_executor import PythonExecutorTool
from sheetmind.analysis.tools.data_type_normalizer import DataTypeNormalizationTool
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
    def __init__(self, response: str = '{"operation_intents":[],"output_intents":["auto"],"confidence":0.5,"reasoning":"mock"}'):
        self._response = response
        self.calls = 0

    async def complete(self, messages, system="", max_tokens=512, temperature=0.0,
                       json_mode=False, **kwargs):
        self.calls += 1
        return self._response


class MockRouter(ModelRouter):
    def __init__(self, response: str = '{"operation_intents":[],"output_intents":["auto"],"confidence":0.5,"reasoning":"mock"}'):
        self._mock = MockModelProvider(response)

    def get_provider(self, role: ModelRole):
        return self._mock


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# QueryNormalizationSkill
# ---------------------------------------------------------------------------

class TestQueryNormalizationSkill:
    def setup_method(self):
        self.skill = QueryNormalizationSkill(MockRouter())
        self.ctx = make_ctx()

    def test_normalizes_contextual_chinese_numbers_without_losing_original_text(self):
        result = run(self.skill.run(
            self.ctx,
            "  查看金额（RMB）前十名！！  ",
            reference_date=date(2026, 9, 10),
        ))

        assert result.original_text == "查看金额（RMB）前十名！！"
        assert result.normalized_text == "查看金额(RMB)前10名!!"
        assert result.numbers[0].raw == "十"
        assert result.numbers[0].value == 10

    def test_resolves_relative_month_to_a_structured_time_range(self):
        result = run(self.skill.run(
            self.ctx,
            "查看上个月的销售额",
            reference_date=date(2026, 9, 10),
        ))

        assert "2026-08-01至2026-09-01" in result.normalized_text
        assert result.time_ranges[0].raw == "上个月"
        assert result.time_ranges[0].start == "2026-08-01"
        assert result.time_ranges[0].end == "2026-09-01"

    def test_keeps_clause_punctuation_for_structure_detection(self):
        result = run(self.skill.run(
            self.ctx,
            "先筛选数据；然后汇总金额。",
            reference_date=date(2026, 9, 10),
        ))

        assert ";" in result.normalized_text
        assert result.normalized_text.endswith(".")


# ---------------------------------------------------------------------------
# OutputPlanningSkill
# ---------------------------------------------------------------------------

class TestOutputPlanningSkill:
    def setup_method(self):
        self.skill = OutputPlanningSkill(MockRouter())
        self.ctx = make_ctx()

    def test_auto_computation_defaults_to_table(self):
        plan = run(self.skill.run(
            self.ctx,
            "统计销售额",
            output_intents=["auto"],
            route=RoutingHint.CODE_GEN,
            has_computation=True,
        ))

        assert plan.include_table is True
        assert plan.include_chart is False
        assert plan.include_summary is True

    def test_explicit_chart_does_not_force_table(self):
        plan = run(self.skill.run(
            self.ctx,
            "查看销售额趋势",
            output_intents=["chart"],
            route=RoutingHint.CODE_GEN,
            has_computation=True,
        ))

        assert plan.include_chart is True
        assert plan.include_table is False

    def test_excel_export_keeps_a_table_result_for_export(self):
        plan = run(self.skill.run(
            self.ctx,
            "生成 Excel",
            output_intents=["export_excel"],
            route=RoutingHint.RULE_ENGINE,
            has_computation=True,
        ))

        assert plan.include_table is True
        assert plan.export_excel is True

    def test_mixed_default_and_chart_outputs_keep_both_result_types(self):
        plan = run(self.skill.run(
            self.ctx,
            "汇总销售额，同时展示趋势图",
            output_intents=["auto", "chart"],
            route=RoutingHint.CODE_GEN,
            has_computation=True,
        ))

        assert plan.include_table is True
        assert plan.include_chart is True


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
        assert result.needs_new_computation is False
        assert "explain" in result.operation_intent.types

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

    def test_internal_sequence_does_not_reuse_previous_turn(self):
        ctx = make_ctx(with_active_result=True)
        result = run(self.skill.run(ctx, "先筛选2025年数据，再按店铺汇总金额"))
        assert result.mode == MultiTurnMode.NEW_QUERY
        assert result.structure.requires_planning is True

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

    def test_operation_and_output_intents_for_chart_aggregation(self):
        result = run(self.skill.run(self.ctx, "按地区汇总销售额并画柱状图"))
        assert result.hint == RoutingHint.CODE_GEN
        assert isinstance(result.operation_intent, OperationIntent)
        assert isinstance(result.output_intent, OutputIntent)
        assert "aggregate" in result.operation_intent.types
        assert result.output_intent.formats == ["chart"]
        assert result.needs_new_computation is True
        assert result.output_intent.wants_chart is True
        assert "aggregate" in result.operation_intent.types
        assert "chart_data_prep" in result.operation_intent.types
        assert "销售额" in result.target_fields

    def test_filter_and_sort_signals_combine_into_rule_engine_route(self):
        result = run(self.skill.run(self.ctx, "筛选2025年订单并按金额降序排序"))

        assert result.hint == RoutingHint.RULE_ENGINE
        assert isinstance(result.operation_intent, OperationIntent)
        assert {"filter", "sort"} <= set(result.operation_intent.types)
        assert "aggregate" not in result.operation_intent.types

    def test_chart_explanation_is_not_chart_creation(self):
        result = run(self.skill.run(self.ctx, "这个柱状图说明了什么"))

        assert result.hint == RoutingHint.INSIGHT_ONLY
        assert result.operation_intent.types == ["explain"]
        assert result.output_intent.formats == ["insight"]
        assert result.output_intent.wants_chart is False

    def test_generate_excel_is_export_output_not_chart_operation(self):
        result = run(self.skill.run(self.ctx, "生成 Excel"))

        assert result.hint == RoutingHint.RULE_ENGINE
        assert result.operation_intent.types == ["pass_through"]
        assert result.output_intent.formats == ["export_excel"]
        assert result.output_intent.explicit is True
        assert result.output_intent.wants_chart is False

    def test_view_trend_infers_chart_output(self):
        result = run(self.skill.run(self.ctx, "查看 SKU 销售额趋势"))

        assert result.hint == RoutingHint.CODE_GEN
        assert "trend" in result.operation_intent.types
        assert result.output_intent.formats == ["chart"]

    def test_asking_how_to_read_trend_infers_insight_not_chart_creation(self):
        result = run(self.skill.run(self.ctx, "怎么看 SKU 销售额趋势"))

        assert result.hint == RoutingHint.CODE_GEN
        assert {"trend", "explain"} <= set(result.operation_intent.types)
        assert "chart_data_prep" not in result.operation_intent.types
        assert result.output_intent.formats == ["insight"]
        assert result.structure.needs_semantic_planning is True

    def test_viewing_how_trend_behaves_infers_insight_not_chart_creation(self):
        result = run(self.skill.run(self.ctx, "查看 SKU 销售额趋势如何"))

        assert {"trend", "explain"} <= set(result.operation_intent.types)
        assert "chart_data_prep" not in result.operation_intent.types
        assert result.output_intent.formats == ["insight"]

    def test_visualize_trend_infers_chart_output(self):
        result = run(self.skill.run(self.ctx, "可视化 SKU 销售额趋势"))

        assert result.hint == RoutingHint.CODE_GEN
        assert "trend" in result.operation_intent.types
        assert "chart_data_prep" in result.operation_intent.types
        assert result.output_intent.formats == ["chart"]

    def test_generate_generic_file_does_not_infer_excel_or_chart(self):
        result = run(self.skill.run(self.ctx, "生成文件"))

        assert result.output_intent.formats == ["auto"]
        assert result.output_intent.explicit is False
        assert "chart_data_prep" not in result.operation_intent.types
        assert result.structure.needs_semantic_planning is True

    def test_normalized_relative_month_has_date_filter_operation(self):
        result = run(self.skill.run(
            self.ctx,
            "查看上个月的数据",
            normalized_query=run(QueryNormalizationSkill(MockRouter()).run(
                self.ctx,
                "查看上个月的数据",
                reference_date=date(2026, 9, 10),
            )),
        ))

        assert "date_filter" in result.operation_intent.types
        assert result.hint == RoutingHint.RULE_ENGINE

    def test_show_trend_chart_does_not_create_filter_operation(self):
        result = run(self.skill.run(self.ctx, "展示 SKU 销售额趋势图"))

        assert "filter" not in result.operation_intent.types
        assert "trend" in result.operation_intent.types
        assert result.output_intent.formats == ["chart"]

    def test_generic_analysis_is_semantically_planned_without_forcing_chart(self):
        result = run(self.skill.run(self.ctx, "分析 SKU 销售额"))

        assert "analysis_request" in result.operation_intent.types
        assert result.output_intent.formats == ["auto"]
        assert result.structure.needs_semantic_planning is True

    def test_anomaly_detection_requires_computation(self):
        result = run(self.skill.run(self.ctx, "找出退货率异常的地区"))

        assert result.hint == RoutingHint.CODE_GEN
        assert "anomaly_detect" in result.operation_intent.types
        assert result.needs_new_computation is True

    def test_vague_pass_through_is_a_simple_structure(self):
        result = run(self.skill.run(self.ctx, "看看数据"))

        assert result.hint == RoutingHint.RULE_ENGINE
        assert result.structure.classification == "simple"
        assert result.structure.needs_semantic_planning is False

    def test_llm_can_add_intents_but_cannot_override_route_decision(self):
        router = MockRouter(
            '{"routing":"rule","operation_intents":["aggregate"],'
            '"output_intents":["table"],"confidence":0.92}'
        )
        skill = RoutingClassificationSkill(router)

        result = run(skill.run(make_ctx(), "处理重点记录", atomic=True))

        assert result.operation_intent.source == "hybrid"
        assert "aggregate" in result.operation_intent.types
        assert result.output_intent.formats == ["table"]
        assert result.hint == RoutingHint.CODE_GEN

    def test_ambiguous_long_query_requests_semantic_structure_planning(self):
        query = "请帮我看看不同地区销售表现到底如何，重点关注明显偏离整体水平的地区并解释可能原因"
        result = run(self.skill.run(self.ctx, query))

        assert result.structure.classification == "uncertain"
        assert result.structure.needs_semantic_planning is True
        assert result.structure.requires_planning is False

    def test_mixed_computation_and_explanation_requests_semantic_planning(self):
        result = run(self.skill.run(self.ctx, "计算各地区销售额并解释差异原因"))

        assert result.structure.classification == "uncertain"
        assert result.structure.needs_semantic_planning is True
        assert "mixed_compute_insight" in result.structure.signals

    def test_routing_rules_are_loaded_from_config_file(self):
        assert routing_rules_module._ROUTING_RULES_PATH.name == "routing_rules.json"
        assert routing_rules_module._ROUTING_RULES_PATH.exists()
        assert "level_1" in routing_rules_module._ROUTING_RULES
        assert "level_2" in routing_rules_module._ROUTING_RULES
        assert "route_keywords" not in routing_rules_module._ROUTING_RULES["level_2"]
        assert "signal_terms" not in routing_rules_module._ROUTING_RULES["level_2"]
        assert "汇总" in routing_rules_module._OPERATION_TERMS["aggregate"]
        assert "筛选" in routing_rules_module._OPERATION_TERMS["filter"]
        assert "展示" not in routing_rules_module._OPERATION_TERMS["filter"]
        assert "excel" in routing_rules_module._OUTPUT_TERMS["export_excel"]
        assert "看" not in routing_rules_module._OUTPUT_TERMS["display_action"]
        assert "可视化" in routing_rules_module._OUTPUT_TERMS["visualization_action"]


# ---------------------------------------------------------------------------
# QueryPlanningSkill
# ---------------------------------------------------------------------------

class TestQueryPlanningSkill:
    def test_decomposes_dependent_mixed_route_query(self):
        response = """{
          "steps": [
            {"query": "筛选2025年数据", "depends_on": []},
            {"query": "按店铺汇总人民币金额", "depends_on": ["s1"]},
            {"query": "取金额最高的前5个店铺", "depends_on": ["s2"]},
            {"query": "分析这些店铺金额差异的原因", "depends_on": ["s3"]}
          ],
          "reasoning": "先计算再解释",
          "confidence": 0.93
        }"""
        router = MockRouter(response)
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()
        query = "筛选2025年数据，再按店铺汇总人民币金额，找最高5个，然后分析原因"

        routing = run(routing_skill.run(ctx, query))
        plan = run(planning_skill.run(ctx, query, routing=routing))

        assert plan.is_multi_step is True
        assert plan.source == "llm"
        assert [step.route for step in plan.steps] == [
            RoutingHint.RULE_ENGINE,
            RoutingHint.CODE_GEN,
            RoutingHint.CODE_GEN,
            RoutingHint.INSIGHT_ONLY,
        ]
        assert [step.depends_on for step in plan.steps] == [
            [], ["s1"], ["s2"], ["s3"],
        ]

    def test_simple_query_skips_planning_model(self):
        router = MockRouter("not-json")
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()

        routing = run(routing_skill.run(ctx, "筛选2025年数据"))
        plan = run(planning_skill.run(ctx, "筛选2025年数据", routing=routing))

        assert plan.source == "single"
        assert len(plan.steps) == 1
        assert router._mock.calls == 0

    def test_semantic_structure_detection_can_confirm_one_atomic_step(self):
        query = "请帮我看看不同地区销售表现到底如何，重点关注明显偏离整体水平的地区并解释可能原因"
        response = f'''{{
          "steps": [{{"query": "{query}", "depends_on": []}}],
          "reasoning": "这是一个围绕地区异常的单一分析问题",
          "confidence": 0.91
        }}'''
        router = MockRouter(response)
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()

        routing = run(routing_skill.run(ctx, query))
        plan = run(planning_skill.run(ctx, query, routing=routing))

        assert plan.source == "llm"
        assert plan.is_multi_step is False
        assert len(plan.steps) == 1
        assert plan.steps[0].query == query
        assert router._mock.calls == 1

    def test_semantic_structure_detection_finds_implicit_dependency(self):
        query = "找出销售表现异常的地区，判断这些地区的退货率是否也明显偏高并解释可能原因"
        response = """{
          "steps": [
            {"query": "找出销售表现异常的地区", "depends_on": []},
            {"query": "判断异常地区的退货率是否明显偏高并解释原因", "depends_on": ["s1"]}
          ],
          "reasoning": "第二问需要第一问先确定地区范围",
          "confidence": 0.94
        }"""
        router = MockRouter(response)
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()

        routing = run(routing_skill.run(ctx, query))
        plan = run(planning_skill.run(ctx, query, routing=routing))

        assert routing.structure.classification == "uncertain"
        assert plan.source == "llm"
        assert plan.is_multi_step is True
        assert plan.steps[1].depends_on == ["s1"]

    def test_invalid_llm_plan_falls_back_to_sequential_rules(self):
        router = MockRouter('{"unexpected": true}')
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()
        query = "先筛选2025年数据，然后按店铺汇总金额"

        routing = run(routing_skill.run(ctx, query))
        plan = run(planning_skill.run(ctx, query, routing=routing))

        assert plan.source == "rule_fallback"
        assert [step.route for step in plan.steps] == [
            RoutingHint.RULE_ENGINE,
            RoutingHint.CODE_GEN,
        ]
        assert plan.steps[1].depends_on == ["s1"]

    def test_llm_plan_cannot_drop_qualified_field(self):
        response = """{
          "steps": [
            {"query": "筛选2025年数据", "depends_on": []},
            {"query": "按店铺汇总金额", "depends_on": ["s1"]}
          ],
          "reasoning": "two steps",
          "confidence": 0.9
        }"""
        router = MockRouter(response)
        routing_skill = RoutingClassificationSkill(router)
        planning_skill = QueryPlanningSkill(router, routing_skill)
        ctx = make_ctx()
        query = "先筛选2025年数据，然后按店铺汇总金额（RMB）"

        routing = run(routing_skill.run(ctx, query))
        plan = run(planning_skill.run(ctx, query, routing=routing))

        assert plan.source == "rule_fallback"
        assert "金额（RMB）" in plan.steps[-1].query


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

    def test_date_report_does_not_claim_success_without_a_date_column(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult

        df = pd.DataFrame({"SKU": ["A", "B"], "金额": [10, 20]})
        result = self.tool.run(
            self.ctx,
            query="筛选2025年数据",
            df=df,
            return_report=True,
        )

        assert isinstance(result, RuleResult)
        assert result.matched_rules == ["pass_through"]

    def test_sort_descending(self):
        result = self.tool.run(self.ctx, query="按金额降序排序", df=self.df)
        vals = list(result["金额"])
        assert vals == sorted(vals, reverse=True)

    def test_sort_report_records_a_rule_even_when_data_was_already_sorted(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult

        df = pd.DataFrame({"金额": [30, 20, 10]})
        result = self.tool.run(
            self.ctx,
            query="按金额降序排序",
            df=df,
            return_report=True,
        )

        assert isinstance(result, RuleResult)
        assert result.matched_rules == ["sort"]

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

    def test_filter_report_records_a_rule_when_every_row_matches(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult

        df = pd.DataFrame({"地区": ["华东", "华东"], "金额": [10, 20]})
        result = self.tool.run(
            self.ctx,
            query="筛选华东",
            df=df,
            return_report=True,
        )

        assert isinstance(result, RuleResult)
        assert result.matched_rules == ["keyword_filter"]

    def test_normalized_relative_month_range_is_executed(self):
        normalized = run(QueryNormalizationSkill(MockRouter()).run(
            self.ctx,
            "查看上个月的数据",
            reference_date=date(2026, 9, 10),
        ))
        df = pd.DataFrame({
            "日期": ["2026-07-31", "2026-08-01", "2026-08-31", "2026-09-01"],
            "金额": [1, 2, 3, 4],
        })

        result = self.tool.run(self.ctx, query=normalized.normalized_text, df=df)

        assert result["金额"].tolist() == [2, 3]

    def test_date_filter_uses_the_query_selected_date_column(self):
        df = pd.DataFrame({
            "创建日期": ["2025-01-01", "2024-01-01"],
            "付款日期": ["2024-02-01", "2025-02-01"],
            "金额": [10, 20],
        })
        query = "筛选付款日期在2025年的数据"
        field_map = run(SemanticTypingSkill(MockRouter()).run(self.ctx, query, df=df))

        result = self.tool.run(self.ctx, query=query, df=df, field_map=field_map)

        assert result["金额"].tolist() == [20]


# ---------------------------------------------------------------------------
# RuleResultValidator
# ---------------------------------------------------------------------------

class TestRuleResultValidator:
    def test_date_validation_uses_the_resolved_required_date_column(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({
            "创建日期": pd.to_datetime(["2025-01-01", "2024-01-01"]),
            "付款日期": pd.to_datetime(["2024-02-01", "2025-02-01"]),
            "金额": [10, 20],
        })
        step = ExecutionStep(
            step_id="s1",
            query="筛选付款日期在2025年的数据",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["filter", "date_filter"],
            required_source_columns=["付款日期"],
        )
        rule_result = RuleResult(
            result_df=source.iloc[[1]].copy(),
            matched_rules=["date_filter", "keyword_filter"],
            selected_columns=list(source.columns),
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is True

    def test_filter_request_rejects_a_pass_through_rule_result(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"地区": ["华东", "华南"], "金额": [10, 20]})
        step = ExecutionStep(
            step_id="s1",
            query="筛选华东地区",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["filter"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["pass_through"],
            selected_columns=["地区", "金额"],
            confidence=0.6,
            fallback_reason="No deterministic rule matched.",
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert decision.fallback_to_codegen is True
        assert "filter" in decision.reasons[0]

    def test_rejects_result_that_drops_a_required_qualified_column(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"金额": [10], "金额（USD）": [5]})
        step = ExecutionStep(
            step_id="s1",
            query="按金额（USD）降序排序",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["sort"],
            required_source_columns=["金额（USD）"],
        )
        rule_result = RuleResult(
            result_df=pd.DataFrame({"金额": [10]}),
            matched_rules=["sort"],
            selected_columns=["金额"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "金额（USD）" in decision.reasons[0]

    def test_rejects_a_reported_sort_when_rows_are_not_sorted(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"金额": [10, 30, 20]})
        step = ExecutionStep(
            step_id="s1",
            query="按金额降序排序",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["sort"],
            required_source_columns=["金额"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["sort"],
            selected_columns=["金额"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "descending" in decision.reasons[0]

    def test_rejects_rows_outside_the_requested_date_range(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({
            "日期": ["2026-08-01", "2026-09-01"],
            "金额": [10, 20],
        })
        step = ExecutionStep(
            step_id="s1",
            query="查看2026-08-01至2026-09-01的数据",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["date_filter"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["date_filter"],
            selected_columns=["日期", "金额"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "date range" in decision.reasons[0]

    def test_rejects_row_growth_for_a_non_aggregating_rule(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"地区": ["华东", "华南"]})
        step = ExecutionStep(
            step_id="s1",
            query="筛选华东",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["filter"],
        )
        rule_result = RuleResult(
            result_df=pd.DataFrame({"地区": ["华东", "华东", "华东"]}),
            matched_rules=["keyword_filter"],
            selected_columns=["地区"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "row count" in decision.reasons[0]

    def test_accepts_an_explicit_pass_through_result(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"SKU": ["A", "B"]})
        step = ExecutionStep(
            step_id="s1",
            query="生成 Excel",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["pass_through"],
            output_intents=["export_excel"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["pass_through"],
            selected_columns=["SKU"],
            confidence=0.6,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is True
        assert decision.fallback_to_codegen is False

    def test_rejects_a_pass_through_result_that_changes_rows(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"SKU": ["A", "B"]})
        step = ExecutionStep(
            step_id="s1",
            query="生成 Excel",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["pass_through"],
            output_intents=["export_excel"],
        )
        rule_result = RuleResult(
            result_df=source.iloc[[0]].copy(),
            matched_rules=["pass_through"],
            selected_columns=["SKU"],
            confidence=0.6,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "pass-through" in decision.reasons[0]

    def test_rejects_rows_outside_a_requested_year_and_month(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"日期": ["2024-10-01", "2024-11-01"]})
        step = ExecutionStep(
            step_id="s1",
            query="筛选2024年10月数据",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["filter", "date_filter"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["date_filter"],
            selected_columns=["日期"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is False
        assert "2024-10" in decision.reasons[0]

    def test_sort_with_extreme_word_does_not_require_extreme_aggregation(self):
        from sheetmind.analysis.tools.rule_engine import RuleResult
        from sheetmind.analysis.validators.rule_result_validator import RuleResultValidator

        source = pd.DataFrame({"金额": [30, 20, 10]})
        step = ExecutionStep(
            step_id="s1",
            query="按金额最高排序",
            route=RoutingHint.RULE_ENGINE,
            operation_intents=["sort", "extreme"],
            target_fields=["金额"],
        )
        rule_result = RuleResult(
            result_df=source.copy(),
            matched_rules=["sort"],
            selected_columns=["金额"],
            confidence=0.95,
        )

        decision = RuleResultValidator().validate(
            step=step,
            source_df=source,
            rule_result=rule_result,
        )

        assert decision.valid is True


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
        self.mock_code_gen.run = AsyncMock(side_effect=[
            "bad_code_1 = df",
            "bad_code_2 = df",
            "bad_code_3 = df",
        ])
        self.mock_executor.run = MagicMock(side_effect=[
            (None, "error 1"),
            (None, "error 2"),
            (None, "error 3"),
        ])

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )
        assert result_df is None
        # Should have tried 1 + MAX_REPAIRS times
        from sheetmind.analysis.harness.repair_loop import MAX_REPAIRS
        assert self.mock_code_gen.run.call_count == 1 + MAX_REPAIRS

    def test_stops_before_executing_identical_repaired_code(self):
        self.mock_code_gen.run = AsyncMock(return_value="bad_code = df")
        self.mock_executor.run = MagicMock(return_value=(None, "NameError: bad_code"))

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )

        assert result_df is None
        assert code == "bad_code = df"
        assert repairs == 1
        assert self.mock_code_gen.run.call_count == 2
        assert self.mock_executor.run.call_count == 1

    def test_stops_after_the_same_execution_error_repeats(self):
        self.mock_code_gen.run = AsyncMock(side_effect=[
            "bad_code_1 = df",
            "bad_code_2 = df",
            "bad_code_3 = df",
        ])
        self.mock_executor.run = MagicMock(return_value=(None, "KeyError: missing"))

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )

        assert result_df is None
        assert code == "bad_code_2 = df"
        assert repairs == 2
        assert self.mock_code_gen.run.call_count == 2
        assert self.mock_executor.run.call_count == 2

    def test_stops_before_a_repair_when_time_budget_is_exhausted(self):
        from sheetmind.analysis.harness.repair_loop import RepairLoop

        clock = MagicMock(side_effect=[0.0, 2.0])
        loop = RepairLoop(
            self.mock_code_gen,
            self.mock_executor,
            time_budget_seconds=1.0,
            clock=clock,
        )
        self.mock_code_gen.run = AsyncMock(return_value="bad_code = df")
        self.mock_executor.run = MagicMock(return_value=(None, "NameError: bad_code"))

        result_df, code, repairs = run(loop.run(self.ctx, "test query", self.df))

        assert result_df is None
        assert code == "bad_code = df"
        assert repairs == 1
        assert self.mock_code_gen.run.call_count == 1
        assert self.mock_executor.run.call_count == 1

    def test_stops_when_code_generation_repeats_the_same_error(self):
        self.mock_code_gen.run = AsyncMock(side_effect=RuntimeError("model unavailable"))

        result_df, code, repairs = run(
            self.loop.run(self.ctx, "test query", self.df)
        )

        assert result_df is None
        assert code == ""
        assert repairs == 1
        assert self.mock_code_gen.run.call_count == 2
        assert self.mock_executor.run.call_count == 0

    def test_required_columns_are_validated_before_execution(self):
        self.df = pd.DataFrame({"金额": [10], "金额（USD）": [5]})
        self.mock_code_gen.run = AsyncMock(side_effect=[
            "result_df = pd.DataFrame({'total': [df['金额'].sum()]})",
            "result_df = pd.DataFrame({'total': [df['金额（USD）'].sum()]})",
        ])
        self.mock_executor.run = MagicMock(return_value=(pd.DataFrame({"total": [5]}), None))

        result_df, code, repairs = run(
            self.loop.run(
                self.ctx,
                "金额（USD）是多少",
                self.df,
                required_columns=["金额（USD）"],
            )
        )

        assert result_df is not None
        assert repairs == 1
        assert "金额（USD）" in code
        assert self.mock_executor.run.call_count == 1

    def test_required_column_in_dead_assignment_does_not_satisfy_contract(self):
        code = """unused = df['金额（USD）']
result_df = pd.DataFrame({'total': [df['金额'].sum()]})"""

        error = CodeGenerationSkill.validate_required_columns(
            code,
            ["金额（USD）"],
            available_columns=["金额", "金额（USD）"],
        )

        assert error is not None
        assert "金额（USD）" in error

    def test_required_column_output_label_does_not_satisfy_contract(self):
        code = "result_df = pd.DataFrame({'金额（USD）': [df['金额'].sum()]})"

        error = CodeGenerationSkill.validate_required_columns(
            code,
            ["金额（USD）"],
            available_columns=["金额", "金额（USD）"],
        )

        assert error is not None

    def test_required_column_flowing_through_intermediate_satisfies_contract(self):
        code = """work = df[['店铺', '金额（USD）']].copy()
grouped = work.groupby('店铺', as_index=False)['金额（USD）'].sum()
result_df = grouped.sort_values('金额（USD）', ascending=False)"""

        error = CodeGenerationSkill.validate_required_columns(
            code,
            ["店铺", "金额（USD）"],
            available_columns=["店铺", "金额", "金额（USD）"],
        )

        assert error is None

    def test_required_column_used_as_agg_mapping_key_satisfies_contract(self):
        code = "result_df = df.groupby('店铺', as_index=False).agg({'金额（USD）': 'sum'})"

        error = CodeGenerationSkill.validate_required_columns(
            code,
            ["店铺", "金额（USD）"],
            available_columns=["店铺", "金额", "金额（USD）"],
        )

        assert error is None


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
    def test_ambiguous_field_stops_before_execution_and_returns_choices(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({
            "金额": [10, 20],
            "金额（RMB）": [70, 80],
            "金额（USD）": [5, 6],
        })
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.repair_loop.run = AsyncMock(side_effect=AssertionError("must clarify first"))

        result = run(agent.run(ctx, "汇总金额"))

        block = next(block for block in result.blocks if block.kind == "field_resolution")
        assert block.status == "needs_clarification"
        assert block.reference == "金额"
        assert {item.column for item in block.candidates} == {
            "金额", "金额（RMB）", "金额（USD）",
        }
        assert agent.repair_loop.run.await_count == 0

    def test_assumed_field_is_visible_and_enforced_for_codegen(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({"营业收入": [10, 20]})
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.repair_loop.run = AsyncMock(return_value=(
            pd.DataFrame({"营业收入": [30]}),
            "result_df = pd.DataFrame({'营业收入': [df['营业收入'].sum()]})",
            0,
        ))
        agent.insight_skill.run = AsyncMock(return_value="收入合计为30。")

        result = run(agent.run(ctx, "汇总收入"))

        notice = next(block for block in result.blocks if block.kind == "field_resolution")
        assert notice.status == "assumed"
        assert notice.selected_column == "营业收入"
        assert ctx.execution_plan.required_source_columns == ["营业收入"]
        assert agent.repair_loop.run.call_args.kwargs["required_columns"] == ["营业收入"]

    def test_invalid_rule_result_falls_back_to_codegen_repair_loop(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({"地区": ["华东", "华南"], "金额": [100, 200]})
        generated_result = source_df.iloc[[0]].copy()
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.semantic_skill.run = AsyncMock(return_value={})
        agent.profiling_skill.run = AsyncMock(return_value="profile")
        agent.repair_loop.run = AsyncMock(
            return_value=(generated_result, "result_df = df.iloc[[0]]", 0)
        )
        agent.insight_skill.run = AsyncMock(return_value="已筛选东部地区。")

        result = run(agent.run(ctx, "筛选东部地区"))

        assert agent.repair_loop.run.call_count == 1
        assert ctx.execution_plan.route == RoutingHint.CODE_GEN
        assert ctx.execution_plan.steps[0].route == RoutingHint.CODE_GEN
        assert result.first_table().rows == [{"地区": "华东", "金额": 100}]

    def test_execution_plan_preserves_default_and_explicit_step_outputs(self):
        from sheetmind.analysis.agent import SheetMindAgent

        routing = RoutingResult(
            hint=RoutingHint.CODE_GEN,
            mode=MultiTurnMode.NEW_QUERY,
            confidence=0.9,
            reasoning="mixed outputs",
        )
        plan = QueryPlan(
            steps=[
                ExecutionStep(
                    step_id="s1",
                    query="汇总销售额",
                    route=RoutingHint.CODE_GEN,
                    output_intents=["auto"],
                ),
                ExecutionStep(
                    step_id="s2",
                    query="展示趋势图",
                    route=RoutingHint.CODE_GEN,
                    output_intents=["chart"],
                    output_explicit=True,
                ),
            ],
            is_multi_step=True,
            source="llm",
            confidence=0.9,
        )

        execution_plan = SheetMindAgent._build_execution_plan(plan, routing)

        assert execution_plan.output_intents == ["auto", "chart"]

    def test_export_output_intent_returns_exportable_table(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({"SKU": ["A", "B"], "销售额": [100, 200]})
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.semantic_skill.run = AsyncMock(return_value={})
        agent.profiling_skill.run = AsyncMock(return_value="profile")
        agent.insight_skill.run = AsyncMock(return_value="已准备导出数据。")

        result = run(agent.run(ctx, "生成 Excel"))

        assert result.output_intents == ["export_excel"]
        assert result.has_table is True
        assert result.has_chart is False
        assert ctx.execution_plan.output_intents == ["export_excel"]

    def test_chart_output_intent_does_not_force_table_block(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({"月份": ["2026-01", "2026-02"], "销售额": [100, 200]})
        chart_df = source_df.copy()
        chart = ChartBlock(
            chart_type="line",
            labels=["2026-01", "2026-02"],
            series=[ChartSeries(name="销售额", values=[100.0, 200.0])],
            x_axis_label="月份",
            y_axis_label="销售额",
        )
        agent.sheet_skill.run = AsyncMock(return_value=[
            {"fileName": "sales.xlsx", "sheets": ["Sheet1"]},
        ])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.semantic_skill.run = AsyncMock(return_value={})
        agent.profiling_skill.run = AsyncMock(return_value="profile")
        agent.repair_loop.run = AsyncMock(return_value=(chart_df, "result = df", 0))
        agent.chart_skill.run = AsyncMock(return_value=chart)
        agent.insight_skill.run = AsyncMock(return_value="销售额呈上升趋势。")

        result = run(agent.run(ctx, "查看 SKU 销售额趋势"))

        assert result.output_intents == ["chart"]
        assert result.has_chart is True
        assert result.has_table is False

    def test_executes_each_planned_step_from_its_dependency_result(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({
            "日期": ["2024-01-01", "2025-01-01", "2025-02-01"],
            "店铺": ["A", "A", "B"],
            "金额（RMB）": [999, 100, 300],
        })
        query = "筛选2025年数据，再按店铺汇总人民币金额，找最高5个，然后分析原因"
        plan = QueryPlan(
            steps=[
                ExecutionStep(step_id="s1", query="筛选2025年数据", route=RoutingHint.RULE_ENGINE),
                ExecutionStep(step_id="s2", query="按店铺汇总人民币金额", route=RoutingHint.CODE_GEN, depends_on=["s1"], input_source="step"),
                ExecutionStep(step_id="s3", query="取金额最高的前5个店铺", route=RoutingHint.CODE_GEN, depends_on=["s2"], input_source="step"),
                ExecutionStep(step_id="s4", query="分析金额差异的原因", route=RoutingHint.INSIGHT_ONLY, depends_on=["s3"], input_source="step", needs_new_computation=False),
            ],
            is_multi_step=True,
            source="llm",
            confidence=0.93,
            reasoning="dependent plan",
        )
        seen_inputs = []

        async def execute_code(*args, **kwargs):
            input_df = kwargs["df"]
            seen_inputs.append(input_df.copy())
            if len(seen_inputs) == 1:
                grouped = input_df.groupby("店铺", as_index=False)["金额（RMB）"].sum()
                return grouped, "result = grouped", 0
            return input_df.sort_values("金额（RMB）", ascending=False).head(5), "result = df", 0

        agent.routing_skill.run = AsyncMock(return_value=RoutingResult(
            hint=RoutingHint.CODE_GEN,
            mode=MultiTurnMode.NEW_QUERY,
            confidence=0.9,
            reasoning="compound",
            is_compound=True,
        ))
        agent.planning_skill.run = AsyncMock(return_value=plan)
        agent.sheet_skill.run = AsyncMock(return_value=[{"fileName": "sales.xlsx", "sheets": ["Sheet1"]}])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.repair_loop.run = AsyncMock(side_effect=execute_code)
        agent.insight_skill.run = AsyncMock(return_value="B店金额最高。")

        result = run(agent.run(ctx, query))

        assert len(seen_inputs) == 2
        assert set(seen_inputs[0]["日期"].dt.year) == {2025}
        assert list(seen_inputs[1].columns) == ["店铺", "金额（RMB）"]
        assert result.first_table().rows[0]["店铺"] == "B"
        assert ctx.execution_plan is not None
        assert len(ctx.execution_plan.steps) == 4

    def test_returns_each_independent_terminal_result(self):
        from sheetmind.analysis.agent import SheetMindAgent

        agent = SheetMindAgent(MockRouter())
        ctx = make_ctx()
        source_df = pd.DataFrame({
            "日期": ["2024-01-01", "2025-01-01", "2025-02-01"],
            "金额": [999, 100, 300],
        })
        plan = QueryPlan(
            steps=[
                ExecutionStep(step_id="s1", query="筛选2025年数据", route=RoutingHint.RULE_ENGINE),
                ExecutionStep(step_id="s2", query="按金额降序排序", route=RoutingHint.RULE_ENGINE),
            ],
            is_multi_step=True,
            source="rule_fallback",
            confidence=0.8,
        )
        agent.routing_skill.run = AsyncMock(return_value=RoutingResult(
            hint=RoutingHint.RULE_ENGINE,
            mode=MultiTurnMode.NEW_QUERY,
            confidence=0.8,
            reasoning="parallel",
        ))
        agent.planning_skill.run = AsyncMock(return_value=plan)
        agent.sheet_skill.run = AsyncMock(return_value=[{"fileName": "sales.xlsx", "sheets": ["Sheet1"]}])
        agent.df_loader.run = MagicMock(return_value=source_df)
        agent.insight_skill.run = AsyncMock(return_value="已分别完成筛选和排序。")

        result = run(agent.run(ctx, "筛选2025年数据，同时按金额降序排序"))

        tables = [block for block in result.blocks if block.kind == "table"]
        assert len(tables) == 2
        assert [table.title for table in tables] == ["筛选2025年数据", "按金额降序排序"]
        assert len(tables[0].rows) == 2
        assert tables[1].rows[0]["金额"] == 999

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
            operation_intent=OperationIntent(types=["chart_data_prep"]),
            output_intent=OutputIntent(formats=["chart"], explicit=True),
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
    def test_field_resolver_requires_clarification_for_tied_metric_variants(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"金额": [10], "金额（RMB）": [70], "金额（USD）": [5]})
        field_map = run(skill.run(make_ctx(), "汇总金额", df=df))

        [decision] = FieldResolver().decide_all("汇总金额", field_map, mentions=["金额"])

        assert decision.status == "needs_clarification"
        assert decision.selected is None
        assert {item.column for item in decision.candidates} == set(df.columns)

    def test_field_resolver_confirms_qualified_metric(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"金额": [10], "金额（RMB）": [70], "金额（USD）": [5]})
        field_map = run(skill.run(make_ctx(), "汇总人民币金额", df=df))

        [decision] = FieldResolver().decide_all(
            "汇总人民币金额", field_map, mentions=["人民币金额", "金额"]
        )

        assert decision.status == "confirmed"
        assert decision.selected is not None
        assert decision.selected.column == "金额（RMB）"

    def test_field_resolver_confirms_user_selected_exact_column(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"金额": [10], "金额（RMB）": [70], "金额（USD）": [5]})
        query = "使用列“金额（RMB）”继续：汇总金额"
        field_map = run(skill.run(make_ctx(), query, df=df))

        [decision] = FieldResolver().decide_all(query, field_map, mentions=["金额"])

        assert decision.status == "confirmed"
        assert decision.selected is not None
        assert decision.selected.column == "金额（RMB）"

    def test_field_resolver_marks_partial_unique_match_as_assumed(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"营业收入": [10, 20]})
        field_map = run(skill.run(make_ctx(), "汇总收入", df=df))

        [decision] = FieldResolver().decide_all("汇总收入", field_map, mentions=["收入"])

        assert decision.status == "assumed"
        assert decision.selected is not None
        assert decision.selected.column == "营业收入"

    def test_type_normalizer_converts_string_dates(self):
        ctx = make_ctx()
        df = pd.DataFrame({"日期": ["2025-01-01", "2025-02-01"]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(ctx, "筛选日期", df=df))

        normalized = DataTypeNormalizationTool().run(ctx, df=df, field_map=field_map)

        assert pd.api.types.is_datetime64_any_dtype(normalized.df["日期"])
        assert normalized.converted_columns == ["日期"]

    def test_type_normalizer_converts_integer_year_month(self):
        ctx = make_ctx()
        df = pd.DataFrame({"月份": [202501, 202502]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(ctx, "按月份汇总", df=df))

        normalized = DataTypeNormalizationTool().run(ctx, df=df, field_map=field_map)

        assert normalized.df["月份"].dt.strftime("%Y-%m").tolist() == ["2025-01", "2025-02"]

    def test_type_normalizer_converts_excel_serial_dates(self):
        ctx = make_ctx()
        df = pd.DataFrame({"日期": [45658, 45689]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(ctx, "筛选日期", df=df))

        normalized = DataTypeNormalizationTool().run(ctx, df=df, field_map=field_map)

        assert normalized.df["日期"].dt.strftime("%Y-%m-%d").tolist() == [
            "2025-01-01", "2025-02-01",
        ]

    def test_type_normalizer_does_not_convert_numeric_identifier(self):
        ctx = make_ctx()
        df = pd.DataFrame({"订单号": [20250101, 20250102]})
        field_map = run(SemanticTypingSkill(MockRouter()).run(ctx, "订单号", df=df))

        normalized = DataTypeNormalizationTool().run(ctx, df=df, field_map=field_map)

        assert normalized.converted_columns == []
        assert pd.api.types.is_integer_dtype(normalized.df["订单号"])

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

    def test_semantic_typing_identifies_usd_qualified_metric(self):
        skill = SemanticTypingSkill(MockRouter())
        df = pd.DataFrame({"金额": [10], "金额（USD）": [5]})

        field_map = run(skill.run(make_ctx(), "金额（USD）是多少", df=df))

        assert field_map["金额（USD）"].type == "numeric"
        assert "usd" in field_map["金额（USD）"].qualifiers
        assert "金额USD" in field_map["金额（USD）"].aliases

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
