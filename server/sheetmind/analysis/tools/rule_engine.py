"""
SheetMind — Rule Engine Tool
================================
Deterministic filter / sort / pass-through.
Used when RoutingHint == RULE_ENGINE.  No LLM calls.

Handles:
  1. Aggregate max:  "哪个店铺花费最多" → group by 店铺, sum 费用金额
  2. Date filter:    "2024年10月" → filter by year+month
  3. Keyword filter: "筛选华东区域" → find column + filter by keyword value
  4. Numeric filter: handled in CODE_GEN path (rule engine uses CODE_GEN for > < conditions)
  5. Sort:           "按销售额降序" → sort_values
  6. Pass-through:   "看看数据" → return df as-is

Returns a DataFrame by default or a structured RuleResult report. When no
deterministic operation matches, the report identifies pass-through; the caller
uses RuleResultValidator to decide whether pass-through satisfies the request.
"""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

import pandas as pd

from ..context import AnalysisContext
from ..skills.field_resolution import FieldResolver
from ..skills.semantic_typing import SemanticFieldMap
from .base import Tool, ToolError

logger = logging.getLogger(__name__)


@dataclass
class RuleResult:
    result_df: pd.DataFrame
    matched_rules: List[str]
    selected_columns: List[str]
    confidence: float
    fallback_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Date range emitted by QueryNormalizationSkill. The end is exclusive.
_DATE_RANGE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})"
)
# Date patterns: "2024年10月", "2025年3月", "2024-10", "2024/10"
_DATE_YEAR_MONTH = re.compile(
    r"(\d{4})[年\-/](\d{1,2})[月]?"
)
# Year only: "2024年" (without month)
_DATE_YEAR_ONLY = re.compile(r"(\d{4})年(?![0-9])")

# Sort direction keywords
_SORT_DESC_KWS = ["降序", "从高到低", "从大到小", "倒序", "desc", "descending", "最高"]
_SORT_ASC_KWS = ["升序", "从低到高", "从小到大", "asc", "ascending", "最低"]
_SORT_TRIGGER_KWS = ["排序", "sort", "order", "排", "按"]

# Filter trigger keywords
_FILTER_TRIGGER_KWS = ["筛选", "过滤", "filter", "where", "只看", "只要"]

_EXTREME_MAX_KWS = ["最多", "最高", "最大", "最贵", "花费最多", "费用最高", "金额最高"]
_EXTREME_SUBJECT_KWS = ["哪个", "哪家", "哪一个", "哪类", "哪种", "who", "which"]

_MONEY_QUERY_KWS = ["尾程花费", "花费", "费用", "金额", "成本", "cost", "spend", "expense", "amount"]
_MONEY_COL_KWS = ["费用金额", "人民币金额", "金额", "花费", "费用", "成本", "销售额", "收入", "amount", "cost", "expense"]


class RuleEngineTool(Tool):
    """
    Apply deterministic filter/sort rules.

    Input kwargs:
        query: str         — the user's query
        df: pd.DataFrame   — input DataFrame

    Returns:
        pd.DataFrame — result after applying rules
    """

    name = "rule_engine"
    description = "Apply deterministic filter/sort/date rules (no LLM)"

    def run(
        self,
        ctx: AnalysisContext,
        query: str = "",
        df: Optional[pd.DataFrame] = None,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
        return_report: bool = False,
        **kwargs: Any,
    ) -> pd.DataFrame:
        if df is None or df.empty:
            raise ToolError("Rule engine received an empty DataFrame", retryable=False)

        if not query.strip():
            return self._result(df, ["pass_through"], return_report)

        result = df.copy()

        # 0. Deterministic aggregate extrema:
        #    "哪个店铺的尾程花费最多" should be answered by real groupby+sum,
        #    not by LLM-generated code that can confuse column names and values.
        aggregate_result = self._apply_aggregate_extreme(
            query,
            result,
            field_map,
            required_columns,
        )
        if aggregate_result is not None:
            return self._result(aggregate_result.reset_index(drop=True), ["aggregate_extreme"], return_report)

        # 1. Apply date filters
        before_date_result = result
        result = self._apply_date_filters(
            query,
            result,
            field_map,
            required_columns,
        )
        date_filter_applied = result is not before_date_result

        # 2. Apply keyword filters (if filter trigger present)
        before_keyword_result = result
        result = self._apply_keyword_filters(
            query,
            result,
            df,
            required_columns,
            field_map,
        )
        keyword_filter_applied = result is not before_keyword_result

        # 3. Apply sort
        sorted_result = self._apply_sort(
            query,
            result,
            field_map,
            required_columns,
        )
        sort_applied = sorted_result is not result
        applied_rules = []
        if date_filter_applied:
            applied_rules.append("date_filter")
        if keyword_filter_applied:
            applied_rules.append("keyword_filter")
        if sort_applied:
            applied_rules.append("sort")
        result = sorted_result

        # If result is empty after filtering, return empty df (not an error)
        logger.debug(
            "[RuleEngine] query=%r → %d rows (was %d rows)",
            query[:60],
            len(result),
            len(df),
        )
        return self._result(result.reset_index(drop=True), applied_rules or ["pass_through"], return_report)

    @staticmethod
    def _result(
        result_df: pd.DataFrame,
        matched_rules: List[str],
        return_report: bool,
    ) -> Union[pd.DataFrame, RuleResult]:
        report = RuleResult(
            result_df=result_df,
            matched_rules=matched_rules,
            selected_columns=[str(column) for column in result_df.columns],
            confidence=0.95 if matched_rules != ["pass_through"] else 0.6,
            fallback_reason=None if matched_rules != ["pass_through"] else "No deterministic rule matched.",
        )
        return report if return_report else result_df

    # ------------------------------------------------------------------
    # Aggregate extrema
    # ------------------------------------------------------------------

    def _apply_aggregate_extreme(
        self,
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        q_lower = query.lower()
        if not (
            any(kw in q_lower for kw in _EXTREME_SUBJECT_KWS)
            and any(kw in q_lower for kw in _EXTREME_MAX_KWS)
        ):
            return None

        group_col = self._find_group_column(
            query,
            df,
            field_map,
            required_columns,
        )
        value_col = self._find_value_column(
            query,
            df,
            field_map,
            required_columns,
        )
        if group_col is None or value_col is None:
            return None

        work = df[[group_col, value_col]].copy()
        work = work[work[group_col].notna()]
        work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
        work = work.dropna(subset=[value_col])
        if work.empty:
            return None

        grouped = (
            work.groupby(group_col, dropna=True)[value_col]
            .sum()
            .sort_values(ascending=False)
            .reset_index()
        )
        return grouped.head(20)

    @staticmethod
    def _find_group_column(
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[str]:
        for column in required_columns or []:
            if column not in df.columns:
                continue
            info = field_map.get(column) if field_map else None
            if info and info.type in {"categorical", "datetime", "datetime-like"}:
                return column
            if info is None and not pd.api.types.is_numeric_dtype(df[column]):
                return column

        if field_map:
            match = FieldResolver().resolve(
                query,
                field_map,
                allowed_types={"categorical", "datetime", "datetime-like"},
            )
            if match and match.column in df.columns:
                return match.column
        # Exact user-mentioned non-numeric column wins.
        for col in df.columns:
            col_clean = re.sub(r"[（(）)\[\]【】]", "", str(col))
            if (str(col) in query or col_clean in query) and not pd.api.types.is_numeric_dtype(df[col]):
                return col

        # Common business dimensions for finance workbooks.
        preferred = ["店铺", "平台", "物流商", "渠道", "国家", "地区", "产品", "产品名称", "产品大类", "费用类型"]
        for kw in preferred:
            if kw in query:
                for col in df.columns:
                    col_clean = re.sub(r"[（(）)\[\]【】]", "", str(col))
                    if kw == str(col) or kw == col_clean:
                        return col

        return None

    @staticmethod
    def _find_value_column(
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[str]:
        q_lower = query.lower()

        for column in required_columns or []:
            if column not in df.columns:
                continue
            info = field_map.get(column) if field_map else None
            if info and info.should_aggregate:
                return column
            if info is None and pd.api.types.is_numeric_dtype(df[column]):
                return column

        if field_map:
            match = FieldResolver().resolve(query, field_map, aggregate_only=True)
            if match and match.column in df.columns:
                return match.column

        if any(kw in q_lower for kw in _MONEY_QUERY_KWS):
            for kw in _MONEY_COL_KWS:
                for col in df.columns:
                    if kw in str(col).lower():
                        return col

        # User-mentioned numeric column wins for non-money questions.
        for col in df.columns:
            col_clean = re.sub(r"[（(）)\[\]【】]", "", str(col))
            if (str(col) in query or col_clean in query) and pd.api.types.is_numeric_dtype(df[col]):
                return col

        for col in df.columns:
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().any():
                return col

        return None

    # ------------------------------------------------------------------
    # Date filter
    # ------------------------------------------------------------------

    def _apply_date_filters(
        self,
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        range_match = _DATE_RANGE.search(query)
        if range_match:
            date_col = self._find_date_column(
                query, df, field_map, required_columns
            )
            if date_col:
                start = pd.Timestamp(range_match.group(1))
                end = pd.Timestamp(range_match.group(2))
                dt = self._coerce_datetime(df[date_col])
                return df[(dt >= start) & (dt < end)]
            return df

        # Detect year+month
        ym_match = _DATE_YEAR_MONTH.search(query)
        if ym_match:
            year = int(ym_match.group(1))
            month = int(ym_match.group(2))
            date_col = self._find_date_column(
                query, df, field_map, required_columns
            )
            if date_col:
                return self._filter_year_month(df, date_col, year, month)
            # No date column found — skip silently
            return df

        # Detect year only
        year_match = _DATE_YEAR_ONLY.search(query)
        if year_match:
            year = int(year_match.group(1))
            date_col = self._find_date_column(
                query, df, field_map, required_columns
            )
            if date_col:
                return self._filter_year(df, date_col, year)

        return df

    @staticmethod
    def _find_date_column(
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Find the most likely date column in df."""
        for column in required_columns or []:
            if column not in df.columns:
                continue
            info = field_map.get(column) if field_map else None
            if info and info.type in {"datetime", "datetime-like"}:
                return column
            if info is None and (
                pd.api.types.is_datetime64_any_dtype(df[column])
                or any(
                    term in str(column).lower()
                    for term in ("日期", "时间", "年月", "date", "time")
                )
            ):
                return column

        if field_map:
            match = FieldResolver().resolve(
                query,
                {column: info for column, info in field_map.items() if column in df.columns},
                allowed_types={"datetime", "datetime-like"},
            )
            if match is not None:
                return match.column

        # Priority 1: datetime dtype
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                return col

        # Priority 2: column name contains date keywords
        date_kws = ["日期", "时间", "date", "time", "year", "month", "年月"]
        for col in df.columns:
            col_lower = col.lower()
            if any(kw in col_lower for kw in date_kws):
                return col

        # Priority 3: first object column that can be parsed as datetime
        for col in df.columns:
            if df[col].dtype == object:
                sample = df[col].dropna().head(5)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        pd.to_datetime(sample, errors="raise")
                    return col
                except Exception:
                    pass

        return None

    @staticmethod
    def _coerce_datetime(series: pd.Series) -> pd.Series:
        if pd.api.types.is_datetime64_any_dtype(series):
            return series
        return pd.to_datetime(series, errors="coerce")

    def _filter_year_month(
        self, df: pd.DataFrame, col: str, year: int, month: int
    ) -> pd.DataFrame:
        dt = self._coerce_datetime(df[col])
        mask = (dt.dt.year == year) & (dt.dt.month == month)
        return df[mask]

    def _filter_year(
        self, df: pd.DataFrame, col: str, year: int
    ) -> pd.DataFrame:
        dt = self._coerce_datetime(df[col])
        mask = dt.dt.year == year
        return df[mask]

    # ------------------------------------------------------------------
    # Keyword filter
    # ------------------------------------------------------------------

    def _apply_keyword_filters(
        self,
        query: str,
        df: pd.DataFrame,
        original_df: pd.DataFrame,
        required_columns: Optional[List[str]] = None,
        field_map: Optional[SemanticFieldMap] = None,
    ) -> pd.DataFrame:
        """
        Try to extract column + value from the query and filter accordingly.
        E.g. "筛选华东区域" → find column with '区域' → filter by '华东'
        """
        q_lower = query.lower()
        if not any(kw in q_lower for kw in _FILTER_TRIGGER_KWS):
            return df

        # Try to identify column and value from query tokens
        contract_columns = [
            column
            for column in required_columns or []
            if column in df.columns
            and (
                (
                    field_map is not None
                    and field_map.get(column) is not None
                    and field_map[column].type in {"categorical", "identifier"}
                )
                or (
                    (field_map is None or field_map.get(column) is None)
                    and not pd.api.types.is_numeric_dtype(df[column])
                )
            )
        ]
        candidate_columns = contract_columns or list(df.columns)
        for col in candidate_columns:
            col_clean = re.sub(r"[（(）)【】\[\]]", "", col)
            # Check if column name appears in query
            if col not in query and col_clean not in query:
                continue
            # Value: text that appears right before the column name
            # or between filter keyword and column name
            value = self._extract_filter_value(query, col)
            if value and value in df[col].astype(str).values:
                mask = df[col].astype(str).str.contains(
                    re.escape(value), case=False, na=False
                )
                if mask.any():
                    df = df[mask]
                    logger.debug("[RuleEngine] keyword filter: %r == %r → %d rows", col, value, mask.sum())
                    return df

        # If the user says "筛选美国官网" without naming the column, find a
        # categorical value that appears verbatim in the query. This handles
        # product-manager-style filters while still keeping the operation
        # deterministic.
        value_filter = self._filter_by_mentioned_value(
            query,
            df,
            candidate_columns=contract_columns or None,
        )
        if value_filter is not None:
            return value_filter

        return df

    @staticmethod
    def _filter_by_mentioned_value(
        query: str,
        df: pd.DataFrame,
        candidate_columns: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        best_match = None
        best_len = 0

        for col in candidate_columns or list(df.columns):
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            values = df[col].dropna().astype(str).unique()
            for value in values[:1000]:
                token = value.strip()
                if len(token) < 2:
                    continue
                if token in query and len(token) > best_len:
                    mask = df[col].astype(str).str.contains(re.escape(token), case=False, na=False)
                    if mask.any():
                        best_match = df[mask]
                        best_len = len(token)

        return best_match

    @staticmethod
    def _extract_filter_value(query: str, col_name: str) -> Optional[str]:
        """
        Extract the filter value from the query.
        E.g. query="筛选华东区域", col_name="区域" → "华东"
        Heuristic: take the token immediately before the column name.
        """
        # Find position of column name in query
        pos = query.find(col_name)
        if pos <= 0:
            return None

        # Take up to 10 chars before col_name
        prefix = query[:pos].strip()
        if not prefix:
            return None

        # Remove filter trigger keywords from prefix
        for kw in _FILTER_TRIGGER_KWS:
            prefix = prefix.replace(kw, "").strip()

        # Return last token (simplistic — works for "筛选华东区域")
        prefix = prefix.strip()
        if prefix:
            # Take last 10 chars max
            return prefix[-10:].strip() or None
        return None

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _apply_sort(
        self,
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        q_lower = query.lower()
        has_sort_trigger = any(kw in q_lower for kw in _SORT_TRIGGER_KWS)
        if not has_sort_trigger:
            return df

        ascending = not any(kw in q_lower for kw in _SORT_DESC_KWS)
        # If no direction specified at all, default to desc
        has_direction = (
            any(kw in q_lower for kw in _SORT_DESC_KWS)
            or any(kw in q_lower for kw in _SORT_ASC_KWS)
        )
        if not has_direction:
            ascending = False  # default desc for "排序" without direction

        # Find the sort column: look for numeric column mentioned in query
        sort_col = self._find_sort_column(
            query,
            df,
            field_map,
            required_columns,
        )
        if sort_col is None:
            return df

        try:
            return df.sort_values(by=sort_col, ascending=ascending)
        except Exception as exc:
            logger.warning("[RuleEngine] sort failed: %s", exc)
            return df

    @staticmethod
    def _find_sort_column(
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Find which column the user wants to sort by."""
        for column in required_columns or []:
            if column not in df.columns:
                continue
            info = field_map.get(column) if field_map else None
            if info and info.should_aggregate:
                return column
            if info is None and pd.api.types.is_numeric_dtype(df[column]):
                return column

        if field_map:
            match = FieldResolver().resolve(query, field_map, aggregate_only=True)
            if match and match.column in df.columns:
                return match.column
        # 1. Check if any numeric column name appears in the query
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                col_clean = re.sub(r"[（(）)\[\]【】]", "", col)
                if col in query or col_clean in query:
                    return col

        # 2. Fall back to first numeric column
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                return col

        return None
