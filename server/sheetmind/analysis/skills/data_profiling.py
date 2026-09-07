"""
SheetMind — Data Profiling Skill
=====================================
Builds a structured data summary string for injection into LLM prompts.
No LLM call — pure pandas inspection.

The output is a concise text block with:
  - Row/column counts
  - Column name, dtype, nunique, sample values
  - Numeric column stats (min/max/mean)

Kept under ~1500 chars to avoid bloating prompts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..context import AnalysisContext
from .base import Skill
from .semantic_typing import SemanticFieldMap


class DataProfilingSkill(Skill):
    """
    Build a data summary string suitable for injection into LLM prompts.
    Returns a plain text str.
    """

    name = "data_profiling"
    description = "Build data summary string for LLM prompt injection (no LLM)"

    # Maximum number of sample values shown per column
    MAX_SAMPLE_VALUES = 4
    # Maximum columns detailed in the output
    MAX_COLS_DETAIL = 20

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: pd.DataFrame = None,  # type: ignore[assignment]
        field_map: Optional[SemanticFieldMap] = None,
        **kwargs: Any,
    ) -> str:
        if df is None or df.empty:
            return "（数据为空）"

        lines: List[str] = []
        lines.append(f"数据规模：{len(df)} 行 × {len(df.columns)} 列")

        cols = list(df.columns)[: self.MAX_COLS_DETAIL]
        lines.append(f"列信息（前 {len(cols)} 列）：")

        for col in cols:
            series = df[col]
            dtype = str(series.dtype)
            n_unique = series.nunique()
            n_null = series.isnull().sum()

            # Sample values (non-null, up to MAX_SAMPLE_VALUES unique)
            sample = series.dropna().unique()[: self.MAX_SAMPLE_VALUES]
            sample_str = ", ".join(str(v) for v in sample)

            # Numeric stats — include sum so LLM can identify the primary metric column
            # (when multiple numeric columns exist, sum magnitude reveals the "money" column)
            stats_str = ""
            if pd.api.types.is_numeric_dtype(series):
                stats_str = (
                    f" | min={series.min():.2f} max={series.max():.2f} "
                    f"mean={series.mean():.2f} sum={series.sum():.2f}"
                )

            info = (
                f"  {col!r}: dtype={dtype}, unique={n_unique}"
                f"{', nulls=' + str(n_null) if n_null > 0 else ''}"
                f"{stats_str}"
                f" | 示例: [{sample_str}]"
            )
            lines.append(info)

        if len(df.columns) > self.MAX_COLS_DETAIL:
            lines.append(f"  ... （省略了 {len(df.columns) - self.MAX_COLS_DETAIL} 列）")

        # Field map alias hint
        if field_map:
            aliases_note = []
            for col, info in list(field_map.items())[: 5]:
                if len(info.aliases) > 1:
                    aliases_note.append(f"  '{col}' 可用别名: {info.aliases[1:3]}")
            if aliases_note:
                lines.append("列别名提示（用于模糊匹配）：")
                lines.extend(aliases_note)

        return "\n".join(lines)
