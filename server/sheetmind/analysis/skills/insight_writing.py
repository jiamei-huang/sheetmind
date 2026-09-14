"""
SheetMind — Insight Writing Skill
======================================
Generates a natural-language summary (SummaryBlock content) using the LLM.

Three scenarios:
  1. "processing"   — brief description of what the data processing did (20-50 chars)
  2. "chart"        — chart trend/highlight analysis (100-300 chars), with structured emojis
  3. "insight_only" — open-ended data analysis (200-500 chars), when RoutingHint == INSIGHT_ONLY
  4. "multi_result" — answer every independent terminal result in one response

Chart insights use short Markdown sections and bullets so the client can render
them as readable conversation replies.
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
      "insight_only" → open-ended analysis text
    """

    name = "insight_writing"
    description = "Generate natural-language insight text using LLM"

    _SYSTEM_PROMPT = (
        "你是一个专业的数据分析文本生成专家。\n\n"
        "【输出原则】\n"
        "1. 语言专业、简洁、有针对性\n"
        "2. 基于实际数据，避免空泛描述\n"
        "3. 突出关键发现和变化\n"
        "4. 使用简洁 Markdown 排版，只允许短标题、段落、编号或项目列表；禁止代码和原始 HTML\n"
        "5. 每段只表达一个要点，避免把多个结论挤在同一个长段落中\n"
    )

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        scenario: str = "processing",
        result_df: Optional[pd.DataFrame] = None,
        chart_block: Optional[ChartBlock] = None,
        table_block: Optional[TableBlock] = None,
        result_sets: Optional[List[tuple[str, pd.DataFrame]]] = None,
        **kwargs: Any,
    ) -> str:
        provider = self.router.get_provider(ModelRole.INSIGHT_WRITING)

        if scenario == "multi_result" and result_sets:
            user_msg = self._multi_result_prompt(query, result_sets)
        elif scenario == "chart" and chart_block is not None:
            user_msg = self._chart_insight_prompt(query, chart_block, result_df)
        elif scenario in {"insight_only", "text_insight"}:
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
            return self._fallback_summary(
                scenario,
                result_df,
                chart_block,
                result_sets=result_sets,
            )

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

        currency_instruction = ""
        if InsightWritingSkill._currency_column(result_df) is not None:
            currency_instruction = (
                "\n结果按币种分区，必须逐币种回答；不同币种金额不可直接比较，"
                "不得声称存在一个全局最高平台或计算跨币种总计。"
            )

        return (
            f"用户查询：{query}\n"
            f"处理结果：{rows} 行，列：{cols}"
            f"\n\n已计算事实：\n{InsightWritingSkill._facts_block(result_df)}"
            f"{data_str}{currency_instruction}\n\n"
            "请严格根据上方实际数据，用一句话（20-50字）说明关键发现（如谁最高/最低），"
            "禁止引用数据中不存在的名称。"
        )

    @staticmethod
    def _multi_result_prompt(
        query: str,
        result_sets: List[tuple[str, pd.DataFrame]],
    ) -> str:
        sections: List[str] = []
        included_results = result_sets[:6]
        max_rows = max(10, 120 // len(included_results))
        for index, (subquery, result_df) in enumerate(included_results, start=1):
            try:
                data = (
                    "无结果"
                    if result_df.empty
                    else result_df.to_string(index=False, max_cols=10, max_rows=max_rows)
                )
            except Exception:
                data = f"列名：{list(result_df.columns)}，共 {len(result_df)} 行"
            sections.append(
                f"子问题{index}：{subquery}\n"
                f"已计算事实：\n{InsightWritingSkill._facts_block(result_df)}\n"
                f"计算结果：\n{data}"
            )

        count = len(sections)
        return (
            f"用户完整查询：{query}\n\n"
            + "\n\n".join(sections)
            + f"\n\n必须逐项回答全部{count}个子问题，不得遗漏任何一项。"
            "按原顺序使用“1. …”“2. …”编号，每项直接给出名称和对应数值；"
            "只允许使用该子问题下的计算结果，禁止串用其他子问题的字段或数据。"
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
            f"已计算事实：\n{InsightWritingSkill._facts_block(result_df)}\n"
            f"用户查询：{query}\n\n"
            "请用以下 Markdown 格式输出图表洞察（100-300字），不要使用 emoji：\n"
            "### 关键结论\n\n"
            "- **最高值：** ...\n"
            "- **最低值：** ...\n\n"
            "### 分析\n\n"
            "用一到两个短段落解释差异、占比或趋势。"
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
            + f"已计算事实：\n{InsightWritingSkill._facts_block(result_df)}\n\n"
            + (f"数据信息：\n{data_summary}\n\n" if data_summary else "")
            + "请只根据已计算事实和数据信息提供专业数据洞察（200-500字）。"
            "使用2-4个简短 Markdown 小标题，分别说明关键发现、数据模式和业务建议；"
            "每个小标题下使用短段落或项目列表；业务建议必须逐条使用项目列表，"
            "每条建议单独一行，不要输出 emoji。"
        )

    @staticmethod
    def _facts_block(result_df: Optional[pd.DataFrame]) -> str:
        """Create a compact, deterministic facts block that anchors LLM prose."""
        if result_df is None or result_df.empty:
            return "无可用的结构化计算结果。"

        facts = [f"行数={len(result_df)}，列数={len(result_df.columns)}"]
        numeric_cols = list(result_df.select_dtypes(include="number").columns[:3])
        currency_col = InsightWritingSkill._currency_column(result_df)
        if currency_col is not None:
            for currency, currency_rows in result_df.groupby(currency_col, dropna=False, sort=True):
                for col in numeric_cols:
                    values = pd.to_numeric(currency_rows[col], errors="coerce").dropna()
                    if not values.empty:
                        facts.append(
                            f"{currency} / {col}: total={values.sum():.2f}, "
                            f"max={values.max():.2f}, min={values.min():.2f}"
                        )
        else:
            for col in numeric_cols:
                values = pd.to_numeric(result_df[col], errors="coerce").dropna()
                if not values.empty:
                    facts.append(
                        f"{col}: total={values.sum():.2f}, max={values.max():.2f}, min={values.min():.2f}"
                    )

        category_cols = list(result_df.select_dtypes(exclude="number").columns[:2])
        for col in category_cols:
            values = result_df[col].dropna().astype(str)
            if not values.empty:
                top = values.value_counts().head(3)
                facts.append(f"{col} top={', '.join(f'{name}({count})' for name, count in top.items())}")
        return "\n".join(facts)

    @staticmethod
    def _currency_column(result_df: Optional[pd.DataFrame]) -> Optional[str]:
        if result_df is None:
            return None
        normalized_names = {"币别", "币种", "货币", "货币类型", "currency", "currencycode"}
        for column in result_df.columns:
            normalized = "".join(
                character.lower()
                for character in str(column)
                if character.isalnum()
            )
            if normalized in normalized_names:
                return str(column)
        return None

    # ------------------------------------------------------------------
    # Fallback (no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_summary(
        scenario: str,
        result_df: Optional[pd.DataFrame],
        chart_block: Optional[ChartBlock],
        result_sets: Optional[List[tuple[str, pd.DataFrame]]] = None,
    ) -> str:
        if scenario == "multi_result" and result_sets:
            lines: List[str] = []
            for index, (subquery, frame) in enumerate(result_sets, start=1):
                if frame.empty:
                    answer = "没有符合条件的数据。"
                elif len(frame) == 1:
                    row = frame.iloc[0]
                    answer = "，".join(
                        f"{column}={row[column]}"
                        for column in list(frame.columns)[:4]
                    )
                else:
                    answer = InsightWritingSkill._facts_block(frame).replace("\n", "；")
                lines.append(f"{index}. {subquery}：{answer}")
            return "\n".join(lines)

        if scenario == "chart" and chart_block:
            all_vals = [v for s in chart_block.series for v in s.values if v is not None]
            if all_vals:
                return (
                    f"### 关键结论\n\n"
                    f"- **最高值：** {max(all_vals):.2f}\n"
                    f"- **最低值：** {min(all_vals):.2f}"
                )
            return "图表已生成。"

        if result_df is not None:
            return f"已处理数据。{InsightWritingSkill._facts_block(result_df)}"

        return "分析完成。"
