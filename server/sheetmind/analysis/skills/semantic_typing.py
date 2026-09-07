"""
SheetMind — Semantic Typing Skill
======================================
Infers column types and generates canonical names + aliases.
Runs purely rule-based (no LLM), analogous to old SemanticTypingAgent fast_mode=True.

Output: SemanticFieldMap = Dict[str, SemanticFieldInfo]

  {
    "金额（RMB）": {
      "canonical_name": "金额",
      "aliases": ["金额（RMB）", "金额(RMB)", "金额RMB", "金额", "amount", "sales_amount"],
      "type": "numeric",   # "numeric" | "categorical" | "datetime" | "unknown"
    },
    ...
  }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

from ..context import AnalysisContext
from .base import Skill

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

SemanticFieldMap = Dict[str, "SemanticFieldInfo"]


@dataclass
class SemanticFieldInfo:
    canonical_name: str
    aliases: List[str]
    type: str = "unknown"          # "numeric" | "categorical" | "datetime" | "unknown"


# ---------------------------------------------------------------------------
# Keyword banks for fast-mode type detection
# ---------------------------------------------------------------------------

_NUMERIC_KWS = [
    "金额", "数量", "价格", "成本", "收入", "销售额", "利润", "费用", "总额", "单价",
    "amount", "price", "cost", "revenue", "sales", "profit", "total", "qty", "quantity",
    "count", "sum", "avg", "rate", "ratio", "percent", "score", "index",
]

_DATETIME_KWS = [
    "日期", "时间", "年月", "date", "time", "datetime", "month", "year", "周",
    "quarter", "period", "at", "on",
]

_CATEGORICAL_KWS = [
    "sku", "id", "名称", "类别", "类型", "状态", "渠道", "地区", "区域",
    "品牌", "产品", "商品", "客户", "供应商", "部门",
    "name", "category", "type", "status", "channel", "region", "brand",
    "product", "customer", "supplier", "department", "code",
]

# Patterns that signal amount+currency columns (for alias expansion)
_AMOUNT_RMB_PATTERN = re.compile(r"金额.*[（(].*[rR][mM][bB].*[）)]|[rR][mM][bB].*金额")


class SemanticTypingSkill(Skill):
    """
    Infer column types and generate canonical names / aliases (rule-based, no LLM).
    """

    name = "semantic_typing"
    description = "Infer column types and generate canonical name/alias map (rule-based)"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: pd.DataFrame = None,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> SemanticFieldMap:
        if df is None or df.empty:
            return {}

        result: SemanticFieldMap = {}
        for col in df.columns:
            col_str = str(col)
            col_type = self._infer_type(col_str, df[col])
            canonical, aliases = self._generate_names(col_str)
            result[col_str] = SemanticFieldInfo(
                canonical_name=canonical,
                aliases=aliases,
                type=col_type,
            )

        return result

    # ------------------------------------------------------------------
    # Type inference
    # ------------------------------------------------------------------

    def _infer_type(self, col_name: str, series: pd.Series) -> str:
        col_lower = col_name.lower()

        # 1. pandas dtype first
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"

        if pd.api.types.is_numeric_dtype(series):
            unique_ratio = series.nunique() / max(len(series), 1)
            # Low-cardinality numeric → likely categorical (e.g. department code)
            if unique_ratio < 0.05 and series.nunique() <= 20:
                return "categorical"
            return "numeric"

        # 2. Try to infer from column name keywords
        for kw in _NUMERIC_KWS:
            if kw in col_lower:
                return "numeric"

        for kw in _DATETIME_KWS:
            if kw in col_lower:
                # Verify sample actually looks like a date
                sample = series.dropna().head(5)
                try:
                    pd.to_datetime(sample, errors="raise", format="mixed")
                    return "datetime"
                except Exception:
                    pass
                return "datetime"  # trust the name

        for kw in _CATEGORICAL_KWS:
            if kw in col_lower:
                return "categorical"

        # 3. Try parsing as datetime from sample values
        sample = series.dropna().head(10)
        if len(sample) > 0 and sample.dtype == object:
            try:
                pd.to_datetime(sample, errors="raise", format="mixed")
                return "datetime"
            except Exception:
                pass

        # 4. Default
        return "categorical" if series.dtype == object else "unknown"

    # ------------------------------------------------------------------
    # Canonical name + alias generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_names(col_name: str) -> tuple:
        aliases: list = [col_name]

        # Strip brackets and whitespace for canonical name
        canonical = re.sub(r"[（(）)【】\[\]{}「」『』]", "", col_name).strip()
        if canonical != col_name:
            aliases.append(canonical)

        # Add no-space variant
        no_space = col_name.replace(" ", "").replace("　", "")
        if no_space not in aliases:
            aliases.append(no_space)

        col_lower = col_name.lower()

        # Amount + RMB special case
        if "金额" in col_lower and "rmb" in col_lower:
            for alias in ["金额RMB", "金额（RMB）", "金额(RMB)", "金额", "销售金额"]:
                if alias not in aliases:
                    aliases.append(alias)
        if "amount" in col_lower or "金额" in col_lower:
            for alias in ["amount", "sales_amount", "sale_amount"]:
                if alias not in aliases:
                    aliases.append(alias)
        if "rmb" in col_lower:
            for alias in ["rmb", "RMB"]:
                if alias not in aliases:
                    aliases.append(alias)

        return canonical if canonical else col_name, aliases
