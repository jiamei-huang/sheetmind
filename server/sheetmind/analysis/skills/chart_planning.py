"""
SheetMind — Chart Planning Skill
=====================================
Rule-based chart type + axis selection (no LLM).
Produces a ChartSpec that is then assembled into a ChartBlock.

Chart type decision priority:
  1. Explicit chart-type keywords in query (柱状图→bar, 折线图→line, 饼图→pie)
  2. Semantic keywords (对比/排名→bar, 占比/比例→pie, 趋势/变化→line)
  3. X-axis column type (datetime→line, categorical→bar)

X-axis selection priority:
  1. User-mentioned categorical column
  2. First datetime column
  3. First categorical/string column

Y-axis selection priority:
  1. User-mentioned numeric column (excluding negated columns)
  2. Numeric column name keywords (金额, revenue, sales, count, …)
  3. First numeric column
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pandas as pd

from ..context import AnalysisContext, ChartBlock, ChartSeries
from .base import Skill

# ---------------------------------------------------------------------------
# Chart type keywords
# ---------------------------------------------------------------------------

_BAR_KWS = ["柱状图", "柱图", "bar", "柱状", "对比", "比较", "排名", "各个", "各", "每个"]
_LINE_KWS = ["折线图", "折线", "line", "趋势", "走势", "变化", "trend", "时序", "时间序列"]
_PIE_KWS = ["饼图", "pie", "占比", "比例", "份额", "percentage", "composition", "构成"]

# Negation patterns (用户排除某列): "不是X" / "不用X" / "换成Y不是X"
_NEGATION_PATTERN = re.compile(
    r"(?:不是|不用|不要|换掉|替换|remove|not)\s*([^\s，,。.！!？?]{1,20})"
)


@dataclass
class ChartSpec:
    """Intermediate representation before building ChartBlock."""
    chart_type: str             # "bar" | "line" | "pie"
    x_col: str
    y_cols: List[str]           # typically 1, but support multi-series
    x_axis_label: Optional[str] = None
    y_axis_label: Optional[str] = None
    title: Optional[str] = None


class ChartPlanningSkill(Skill):
    """
    Determine chart type, x_col, y_col(s) from result_df + query.
    Returns a ChartBlock ready to add to ResultBlocks.
    """

    name = "chart_planning"
    description = "Rule-based chart type + axis selection; builds ChartBlock"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        result_df: Optional[pd.DataFrame] = None,
        **kwargs: Any,
    ) -> Optional[ChartBlock]:
        if result_df is None or result_df.empty:
            return None

        # Find negated columns
        negated = self._extract_negated_cols(query, result_df)

        # Detect axes
        x_col = self._select_x_col(query, result_df, negated)
        y_cols = self._select_y_cols(query, result_df, x_col, negated)

        if not x_col or not y_cols:
            return None  # can't build chart

        # Determine chart type
        chart_type = self._determine_chart_type(query, result_df, x_col)

        # Build ChartBlock
        try:
            return self._build_chart_block(result_df, x_col, y_cols, chart_type, query)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Chart type
    # ------------------------------------------------------------------

    def _determine_chart_type(
        self, query: str, df: pd.DataFrame, x_col: str
    ) -> str:
        q_lower = query.lower()

        # 1. Explicit keywords
        if any(kw in q_lower for kw in _PIE_KWS):
            return "pie"
        if any(kw in q_lower for kw in _LINE_KWS):
            return "line"
        if any(kw in q_lower for kw in _BAR_KWS):
            return "bar"

        # 2. x_col is datetime → line
        if x_col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[x_col]):
                return "line"
            # Check if x_col looks like date strings
            sample = df[x_col].dropna().head(5).astype(str)
            date_pattern = re.compile(r"^\d{4}[-/年]\d{1,2}")
            if all(date_pattern.match(str(v)) for v in sample):
                return "line"

        return "bar"  # default

    # ------------------------------------------------------------------
    # X-axis selection
    # ------------------------------------------------------------------

    def _select_x_col(
        self, query: str, df: pd.DataFrame, negated: set
    ) -> Optional[str]:
        # 1. User-mentioned categorical column
        for col in df.columns:
            if col in negated:
                continue
            col_clean = re.sub(r"[（(）)\[\]【】]", "", col)
            if (col in query or col_clean in query) and not pd.api.types.is_numeric_dtype(df[col]):
                return col

        # 2. First datetime column
        for col in df.columns:
            if col not in negated and pd.api.types.is_datetime64_any_dtype(df[col]):
                return col

        # 3. First string/object column
        for col in df.columns:
            if col not in negated and df[col].dtype == object:
                return col

        # 4. First non-numeric
        for col in df.columns:
            if col not in negated and not pd.api.types.is_numeric_dtype(df[col]):
                return col

        # 5. First column (even if numeric — edge case with all-numeric df)
        for col in df.columns:
            if col not in negated:
                return col

        return None

    # ------------------------------------------------------------------
    # Y-axis selection
    # ------------------------------------------------------------------

    _Y_KEYWORDS = [
        "金额", "销售额", "收入", "利润", "数量", "费用", "价格",
        "amount", "revenue", "sales", "profit", "count", "qty",
    ]

    def _select_y_cols(
        self,
        query: str,
        df: pd.DataFrame,
        x_col: Optional[str],
        negated: set,
    ) -> List[str]:
        candidates = [
            c for c in df.columns
            if c != x_col
            and c not in negated
            and pd.api.types.is_numeric_dtype(df[c])
        ]

        if not candidates:
            return []

        # 1. User-mentioned numeric column
        for col in candidates:
            col_clean = re.sub(r"[（(）)\[\]【】]", "", col)
            if col in query or col_clean in query:
                return [col]

        # 2. Name-based keyword match
        for kw in self._Y_KEYWORDS:
            for col in candidates:
                if kw in col.lower():
                    return [col]

        # 3. All numeric columns (multi-series if ≤ 5 cols)
        return candidates[:5]

    # ------------------------------------------------------------------
    # Negation detection
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_negated_cols(query: str, df: pd.DataFrame) -> set:
        negated = set()
        for m in _NEGATION_PATTERN.finditer(query):
            token = m.group(1).strip()
            for col in df.columns:
                if token in col or col in token:
                    negated.add(col)
        return negated

    # ------------------------------------------------------------------
    # ChartBlock construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_chart_block(
        df: pd.DataFrame,
        x_col: str,
        y_cols: List[str],
        chart_type: str,
        query: str,
    ) -> ChartBlock:
        # Truncate to max 500 points for display
        df_plot = df[[x_col] + y_cols].head(500)

        labels = [str(v) for v in df_plot[x_col].tolist()]
        series = []
        for y_col in y_cols:
            values = []
            for v in df_plot[y_col].tolist():
                try:
                    values.append(float(v) if v is not None and str(v) not in ("nan", "NaN", "None") else None)
                except (TypeError, ValueError):
                    values.append(None)
            series.append(ChartSeries(name=y_col, values=values))

        return ChartBlock(
            chart_type=chart_type,
            title=None,
            labels=labels,
            series=series,
            x_axis_label=x_col,
            y_axis_label=y_cols[0] if y_cols else None,
        )
