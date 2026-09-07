"""
SheetMind — Insight Writing Skill
======================================
Generates a natural-language summary (SummaryBlock content) using the LLM.

Three scenarios:
  1. "processing"   — brief description of what the data processing did (20-50 chars)
  2. "chart"        — chart trend/highlight analysis (100-300 chars), with structured emojis
  3. "text_insight" — open-ended data analysis (200-500 chars), when RoutingHint == TEXT_ONLY

The structured chart insight format from old TextGenerationAgent:
  **Insight：**

  🟢 最高值：...
  🔴 最低值：...
  📊 集中度：...
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from ..context import AnalysisContext, ChartBlock, TableBlock
from ..models.configs import ModelRole
from .base import Skill

logger = logging.getLogger(__name__)


class InsightWritingSkill(Skill):
    """
    Generate insight text for a result.

    Scenario selection (via `scenario` kwarg):
      "processing"   → brief description of what was processed
      "chart"        → chart analysis with emoji highlights
      "text_insight" → open-ended analysis text
    """

    name = "insight_writing"
    description = "Generate natural-language insight text using LLM"

    _SYSTEM_PROMPT = (
        "你是一个专业的数据分析文本生成专家。\n\n"
        "【输出原则】\n"
        "1. 语言专业、简洁、有针对性\n"
        "2. 基于实际数据，避免空泛描述\n"
        "3. 突出关键发现和变化\n"
        "4. 只输出纯文本或指定结构，禁止输出代码或 Markdown 格式（加粗 **...** 除外）\n"
    )

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        scenario: str = "processing",
        result_df: Optional[pd.DataFrame] = None,
        chart_block: Optional[ChartBlock] = None,
        table_block: Optional[TableBlock] = None,
        **kwargs: Any,
    ) -> str:
        provider = self.router.get_provider(ModelRole.INSIGHT_WRITING)

        if scenario == "chart" and chart_block is not None:
            user_msg = self._chart_insight_prompt(query, chart_block, result_df)
        elif scenario == "text_insight":
            user_msg = self._text_insight_prompt(query, result_df, ctx)
        else:
            # "processing" — brief description
            user_msg = self._processing_prompt(query, result_df, table_block)

        try:
            text = await provider.complete(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM_PROMPT,
                max_tokens=512,
                temperature=0.3,
            )
            return text.strip()
        except Exception as exc:
            logger.warning("[InsightWriting] LLM failed: %s", exc)
            # Graceful degradation — build a minimal rule-based summary
            return self._fallback_summary(scenario, result_df, chart_block)

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    @staticmethod
    def _processing_prompt(
        query: str,
        result_df: Optional[pd.DataFrame],
        table_block: Optional[TableBlock],
    ) -> str:
        rows = len(result_df) if result_df is not None else (len(table_block.rows) if table_block else 0)
        cols = list(result_df.columns[:5]) if result_df is not None else (table_block.columns[:5] if table_block else [])

        # Inject the computed result (post-aggregation) into the prompt so the LLM
        # writes the insight from real numbers, not from guesswork.
        # NOTE: result_df is the *output of the pandas computation*, not the raw
        # source data — the source is loaded in full (up to 100k rows) by
        # DataframeLoaderTool.  After GROUP BY / aggregation the result is
        # typically small (tens of rows), so we cap the prompt injection at 200
        # rows to stay within token limits while still covering realistic outputs.
        data_str = ""
        if result_df is not None and not result_df.empty:
            try:
                data_str = (
                    "\n\n计算结果：\n"
                    + result_df.to_string(index=False, max_cols=10, max_rows=200)
                )
            except Exception:
                data_str = f"\n\n列名：{cols}，共 {rows} 行"

        return (
            f"用户查询：{query}\n"
            f"处理结果：{rows} 行，列：{cols}"
            f"{data_str}\n\n"
            "请严格根据上方实际数据，用一句话（20-50字）说明关键发现（如谁最高/最低），"
            "禁止引用数据中不存在的名称。"
        )

    @staticmethod
    def _chart_insight_prompt(
        query: str,
        chart: ChartBlock,
        result_df: Optional[pd.DataFrame],
    ) -> str:
        # Build basic stats from chart data
        all_values: List[float] = []
        for s in chart.series:
            all_values.extend(v for v in s.values if v is not None)

        max_val = max(all_values) if all_values else 0
        min_val = min(all_values) if all_values else 0
        avg_val = sum(all_values) / len(all_values) if all_values else 0

        # Find label with max/min
        max_label, min_label = "", ""
        if chart.labels and chart.series:
            vals = chart.series[0].values
            if vals:
                max_idx = max(range(len(vals)), key=lambda i: vals[i] or float("-inf"))
                min_idx = min(range(len(vals)), key=lambda i: vals[i] or float("inf"))
                max_label = chart.labels[max_idx] if max_idx < len(chart.labels) else ""
                min_label = chart.labels[min_idx] if min_idx < len(chart.labels) else ""

        # Auto-unit detection
        unit = ""
        if max_val >= 10000:
            unit = "（单位可能为元）"
        elif max_val < 1 and max_val > 0:
            unit = "（可能为比例/百分比）"

        return (
            f"图表类型：{chart.chart_type}\n"
            f"X轴：{chart.x_axis_label or '类别'} | Y轴：{chart.y_axis_label or '数值'}\n"
            f"标签数量：{len(chart.labels)} | 系列数量：{len(chart.series)}\n"
            f"Y轴范围：最大={max_val:.2f}，最小={min_val:.2f}，均值={avg_val:.2f} {unit}\n"
            f"最高值标签：{max_label} | 最低值标签：{min_label}\n"
            f"用户查询：{query}\n\n"
            "请用以下格式输出图表洞察（100-300字）：\n"
            "**Insight：**\n\n"
            "🟢 最高值：...\n\n"
            "🔴 最低值：...\n\n"
            "📊 分析：..."
        )

    @staticmethod
    def _text_insight_prompt(
        query: str,
        result_df: Optional[pd.DataFrame],
        ctx: AnalysisContext,
    ) -> str:
        data_summary = ""
        if result_df is not None and not result_df.empty:
            try:
                desc = result_df.describe(include="all").to_string(max_cols=8)
                head = result_df.head(10).to_string(index=False, max_cols=8)
                data_summary = f"数据统计：\n{desc}\n\n前10行：\n{head}"
            except Exception:
                data_summary = f"列名：{list(result_df.columns)}, 行数：{len(result_df)}"

        conv = ctx.conversation_text(max_turns=4)

        return (
            f"用户查询：{query}\n\n"
            + (f"对话历史：\n{conv}\n\n" if conv else "")
            + (f"数据信息：\n{data_summary}\n\n" if data_summary else "")
            + "请根据以上信息提供专业数据洞察（200-500字），包括关键发现、数据模式和业务建议。"
        )

    # ------------------------------------------------------------------
    # Fallback (no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_summary(
        scenario: str,
        result_df: Optional[pd.DataFrame],
        chart_block: Optional[ChartBlock],
    ) -> str:
        if scenario == "chart" and chart_block:
            all_vals = [v for s in chart_block.series for v in s.values if v is not None]
            if all_vals:
                return (
                    f"**Insight：**\n\n"
                    f"🟢 最高值：{max(all_vals):.2f}\n\n"
                    f"🔴 最低值：{min(all_vals):.2f}"
                )
            return "图表已生成。"

        if result_df is not None:
            return f"已处理数据，共 {len(result_df)} 行。"

        return "分析完成。"
