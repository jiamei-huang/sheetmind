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
        "You are a professional data analysis writer for SheetMind.\n\n"
        "Output rules:\n"
        "1. Always write user-facing prose in English.\n"
        "2. Keep original column names, sheet names, and data values unchanged, even when they are not English.\n"
        "3. Be concise, specific, and grounded only in the computed data.\n"
        "4. Highlight the key finding, change, or risk instead of giving generic commentary.\n"
        "5. Use concise Markdown with short headings, paragraphs, numbered lists, or bullets only. Do not output code or raw HTML.\n"
        "6. Use one main idea per paragraph.\n"
        "7. If the user asks to directly edit, save back, or write formulas into the original Excel workbook, state that SheetMind cannot directly edit the original workbook yet, then offer to design the transformation, preview the result, or export a clean table.\n"
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
                    "\n\nComputed result:\n"
                    + result_df.to_string(index=False, max_cols=10, max_rows=200)
                )
            except Exception:
                data_str = f"\n\nColumns: {cols}, rows: {rows}"

        currency_instruction = ""
        if InsightWritingSkill._currency_column(result_df) is not None:
            currency_instruction = (
                "\nThe result is partitioned by currency. Answer per currency. "
                "Do not compare raw amounts across currencies, claim a global highest platform, or compute cross-currency totals."
            )

        return (
            f"User query: {query}\n"
            f"Processed result: {rows} rows, columns: {cols}"
            f"\n\nComputed facts:\n{InsightWritingSkill._facts_block(result_df)}"
            f"{data_str}{currency_instruction}\n\n"
            "Write one concise English sentence explaining the key finding, such as the highest or lowest item. "
            "Only mention names that appear in the computed data."
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
                    "No result"
                    if result_df.empty
                    else result_df.to_string(index=False, max_cols=10, max_rows=max_rows)
                )
            except Exception:
                data = f"Columns: {list(result_df.columns)}, rows: {len(result_df)}"
            sections.append(
                f"Sub-question {index}: {subquery}\n"
                f"Computed facts:\n{InsightWritingSkill._facts_block(result_df)}\n"
                f"Computed result:\n{data}"
            )

        count = len(sections)
        return (
            f"Full user query: {query}\n\n"
            + "\n\n".join(sections)
            + f"\n\nAnswer all {count} sub-questions in English and do not skip any. "
            "Use numbered items in the original order. Each item should directly give the relevant name and value. "
            "Use only the computed result under that sub-question."
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
            unit = "(unit may be currency)"
        elif max_val < 1 and max_val > 0:
            unit = "(may be a ratio or percentage)"

        return (
            f"Chart type: {chart.chart_type}\n"
            f"X-axis: {chart.x_axis_label or 'Category'} | Y-axis: {chart.y_axis_label or 'Value'}\n"
            f"Label count: {len(chart.labels)} | Series count: {len(chart.series)}\n"
            f"Y-axis range: max={max_val:.2f}, min={min_val:.2f}, avg={avg_val:.2f} {unit}\n"
            f"Highest label: {max_label} | Lowest label: {min_label}\n"
            f"Computed facts:\n{InsightWritingSkill._facts_block(result_df)}\n"
            f"User query: {query}\n\n"
            "Write chart insight in English using this Markdown format. Do not use emoji:\n"
            "### Key Takeaways\n\n"
            "- **Highest:** ...\n"
            "- **Lowest:** ...\n\n"
            "### Analysis\n\n"
            "Use one or two short paragraphs to explain differences, share, or trend."
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
                data_summary = f"Data statistics:\n{desc}\n\nFirst 10 rows:\n{head}"
            except Exception:
                data_summary = f"Columns: {list(result_df.columns)}, rows: {len(result_df)}"

        conv = ctx.conversation_text(max_turns=4)

        return (
            f"User query: {query}\n\n"
            + (f"Conversation history:\n{conv}\n\n" if conv else "")
            + f"Computed facts:\n{InsightWritingSkill._facts_block(result_df)}\n\n"
            + (f"Data information:\n{data_summary}\n\n" if data_summary else "")
            + "Provide professional data insight in English using only the computed facts and data information. "
            "Use 2-4 short Markdown headings for key findings, data patterns, and business recommendations. "
            "Use short paragraphs or bullets under each heading. Put each recommendation on its own bullet. Do not use emoji."
        )

    @staticmethod
    def _facts_block(result_df: Optional[pd.DataFrame]) -> str:
        """Create a compact, deterministic facts block that anchors LLM prose."""
        if result_df is None or result_df.empty:
            return "No structured computed result is available."

        facts = [f"rows={len(result_df)}, columns={len(result_df.columns)}"]
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
                    answer = "No matching data."
                elif len(frame) == 1:
                    row = frame.iloc[0]
                    answer = ", ".join(
                        f"{column}={row[column]}"
                        for column in list(frame.columns)[:4]
                    )
                else:
                    answer = InsightWritingSkill._facts_block(frame).replace("\n", "; ")
                lines.append(f"{index}. {subquery}: {answer}")
            return "\n".join(lines)

        if scenario == "chart" and chart_block:
            all_vals = [v for s in chart_block.series for v in s.values if v is not None]
            if all_vals:
                return (
                    f"### Key Takeaways\n\n"
                    f"- **Highest:** {max(all_vals):.2f}\n"
                    f"- **Lowest:** {min(all_vals):.2f}"
                )
            return "The chart is ready."

        if result_df is not None:
            return f"Data processed. {InsightWritingSkill._facts_block(result_df)}"

        return "Analysis complete."
