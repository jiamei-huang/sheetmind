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

_EXTREME_MAX_KWS = [
    "最多", "最高", "最大", "最贵", "花费最多", "费用最高", "金额最高",
    "费用高", "花费高", "成本高", "金额高", "占比高", "占比最高", "物流费用高",
]
_EXTREME_MIN_KWS = ["最低", "最少", "最小", "最便宜", "费用低", "花费低", "成本低", "金额低"]
_EXTREME_SUBJECT_KWS = ["哪个", "哪些", "哪家", "哪一个", "哪类", "哪种", "who", "which"]
_SHARE_KWS = ["占比", "比例", "份额", "percent", "percentage", "ratio", "share"]
_AVERAGE_KWS = ["平均", "均值", "avg", "average", "mean"]

_MONEY_QUERY_KWS = [
    "尾程花费", "仓储费", "物流费", "尾程费", "运费", "花费", "花钱", "花的",
    "物流用", "费用", "金额", "成本", "最贵", "cost", "spend", "expense", "amount",
]
_MONEY_COL_KWS = ["费用金额", "人民币金额", "金额", "花费", "费用", "成本", "销售额", "收入", "amount", "cost", "expense"]

_CURRENCY_COLUMN_NAMES = {
    "币别", "币种", "货币", "货币类型", "currency", "currencycode",
}
_CURRENCY_ALIASES = {
    "CNY": ("人民币", "cny", "rmb"),
    "USD": ("美元", "usd", "美金"),
    "EUR": ("欧元", "eur"),
    "JPY": ("日元", "jpy", "日币"),
    "GBP": ("英镑", "gbp"),
    "HKD": ("港币", "港元", "hkd"),
}
_NORMALIZED_MONEY_PRIORITY = ("CNY", "USD", "EUR", "JPY", "GBP", "HKD")


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
            selected_sheets=ctx.selected_sheets,
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
        selected_sheets: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        q_lower = query.lower()
        if not (
            any(kw in q_lower for kw in _EXTREME_SUBJECT_KWS)
            and any(kw in q_lower for kw in [*_EXTREME_MAX_KWS, *_EXTREME_MIN_KWS])
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

        filtered = self._apply_context_value_filters(
            query,
            df,
            excluded_columns={group_col, value_col},
            field_map=field_map,
            selected_sheets=selected_sheets,
        )

        currency_col = self._find_currency_column(filtered)
        explicit_currency = self._query_currency_code(query)
        if currency_col is not None and explicit_currency is not None:
            currency_codes = filtered[currency_col].map(self._currency_code)
            matching_currency = currency_codes == explicit_currency
            if matching_currency.any():
                filtered = filtered[matching_currency]

        if (
            currency_col is not None
            and self._is_money_query(query, value_col)
            and explicit_currency is None
        ):
            populated_currencies = (
                filtered[currency_col]
                .dropna()
                .astype(str)
                .str.strip()
            )
            populated_currencies = populated_currencies[populated_currencies != ""]
            if populated_currencies.nunique() > 1:
                normalized_value_col = self._find_complete_normalized_money_column(
                    filtered,
                    raw_value_col=value_col,
                    group_col=group_col,
                    field_map=field_map,
                )
                if normalized_value_col is not None:
                    value_col = normalized_value_col
                else:
                    return self._aggregate_extreme_by_currency(
                        query,
                        filtered,
                        currency_col=currency_col,
                        group_col=group_col,
                        value_col=value_col,
                    )

        work = filtered[[group_col, value_col]].copy()
        work = work[work[group_col].notna()]
        work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
        work = work.dropna(subset=[value_col])
        if work.empty:
            return None

        aggregation = "mean" if any(kw in q_lower for kw in _AVERAGE_KWS) else "sum"
        ascending = any(kw in q_lower for kw in _EXTREME_MIN_KWS)
        grouped = (
            work.groupby(group_col, dropna=True)[value_col]
            .agg(aggregation)
            .sort_values(ascending=ascending)
            .reset_index()
        )
        if any(kw in q_lower for kw in _SHARE_KWS):
            total = grouped[value_col].sum()
            if total:
                grouped[f"{value_col}占比"] = grouped[value_col] / total

        top_n_match = re.search(r"(?:前|top\s*)(\d+)", q_lower, re.IGNORECASE)
        if top_n_match:
            return grouped.head(max(1, int(top_n_match.group(1))))
        if "哪些" in q_lower:
            superlative = any(kw in q_lower for kw in ["最高", "最多", "最大", "最贵", "最低", "最少", "最小"])
            if superlative:
                return grouped
            if not grouped.empty:
                benchmark = grouped[value_col].mean()
                return grouped[
                    grouped[value_col] <= benchmark
                    if ascending
                    else grouped[value_col] >= benchmark
                ]
        return grouped

    @classmethod
    def _aggregate_extreme_by_currency(
        cls,
        query: str,
        df: pd.DataFrame,
        *,
        currency_col: str,
        group_col: str,
        value_col: str,
    ) -> Optional[pd.DataFrame]:
        """Return comparable winners per currency instead of mixing raw amounts."""
        q_lower = query.lower()
        work = df[[currency_col, group_col, value_col]].copy()
        work = work[work[group_col].notna()]
        work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
        work = work.dropna(subset=[value_col])
        if work.empty:
            return None

        currency_values = work[currency_col].astype("string").str.strip()
        work[currency_col] = currency_values.mask(
            currency_values.isna() | (currency_values == ""),
            "未标注币种",
        )
        aggregation = "mean" if any(kw in q_lower for kw in _AVERAGE_KWS) else "sum"
        ascending = any(kw in q_lower for kw in _EXTREME_MIN_KWS)
        grouped = (
            work.groupby([currency_col, group_col], dropna=False)[value_col]
            .agg(aggregation)
            .reset_index()
            .sort_values(
                [currency_col, value_col],
                ascending=[True, ascending],
                kind="stable",
            )
        )

        if any(kw in q_lower for kw in _SHARE_KWS):
            totals = grouped.groupby(currency_col)[value_col].transform("sum")
            grouped[f"{value_col}占比"] = grouped[value_col] / totals.where(totals != 0)

        top_n_match = re.search(r"(?:前|top\s*)(\d+)", q_lower, re.IGNORECASE)
        if top_n_match:
            limit = max(1, int(top_n_match.group(1)))
            return grouped.groupby(currency_col, sort=False, group_keys=False).head(limit)

        if "哪些" in q_lower:
            superlative = any(
                kw in q_lower
                for kw in ["最高", "最多", "最大", "最贵", "最低", "最少", "最小"]
            )
            if superlative:
                return grouped
            benchmark = grouped.groupby(currency_col)[value_col].transform("mean")
            return grouped[
                grouped[value_col] <= benchmark
                if ascending
                else grouped[value_col] >= benchmark
            ]

        return grouped

    @staticmethod
    def _find_group_column(
        query: str,
        df: pd.DataFrame,
        field_map: Optional[SemanticFieldMap] = None,
        required_columns: Optional[List[str]] = None,
    ) -> Optional[str]:
        if field_map:
            match = FieldResolver().resolve(
                query,
                field_map,
                allowed_types={"categorical", "datetime", "datetime-like", "identifier"},
            )
            if match and match.column in df.columns:
                return match.column

        for column in required_columns or []:
            if column not in df.columns:
                continue
            info = field_map.get(column) if field_map else None
            if info and info.type in {"categorical", "datetime", "datetime-like", "identifier"}:
                return column
            if info is None and not pd.api.types.is_numeric_dtype(df[column]):
                return column
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
    def _apply_context_value_filters(
        query: str,
        df: pd.DataFrame,
        *,
        excluded_columns: set[str],
        field_map: Optional[SemanticFieldMap] = None,
        selected_sheets: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Filter by categorical values mentioned as context before aggregating."""
        result = df
        for col in df.columns:
            if col in excluded_columns or pd.api.types.is_numeric_dtype(df[col]):
                continue
            info = field_map.get(col) if field_map else None
            if info and info.type not in {"categorical", "identifier", "datetime-like"}:
                continue

            values = (
                result[col]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .sort_values(key=lambda series: series.str.len(), ascending=False)
                .head(1000)
            )
            for value in values:
                token = value.strip()
                if len(token) < 2 or token not in query:
                    continue
                if (
                    str(col) not in query
                    and (
                        re.search(
                            rf"{re.escape(token)}(?:表格|表|sheet)",
                            query,
                            re.IGNORECASE,
                        )
                        or RuleEngineTool._matches_selected_sheet(token, selected_sheets)
                    )
                ):
                    # A worksheet/domain name is context, not a row predicate.
                    # An explicit column reference ("费用类型为仓储费") still filters.
                    continue
                mask = result[col].astype(str).str.contains(re.escape(token), case=False, na=False)
                if mask.any() and mask.sum() < len(result):
                    result = result[mask]
                break
        return result

    @staticmethod
    def _normalise_domain_name(value: str) -> str:
        return re.sub(
            r"(?:worksheet|sheet|工作表|表格|表)$|[\s_\-（()）【】\[\]]",
            "",
            str(value).lower(),
        )

    @classmethod
    def _matches_selected_sheet(
        cls,
        value: str,
        selected_sheets: Optional[List[str]],
    ) -> bool:
        value_name = cls._normalise_domain_name(value)
        if len(value_name) < 2:
            return False
        for sheet in selected_sheets or []:
            sheet_name = cls._normalise_domain_name(sheet)
            if len(sheet_name) >= 2 and (
                sheet_name in value_name or value_name in sheet_name
            ):
                return True
        return False

    @staticmethod
    def _normalise_currency_text(value: Any) -> str:
        return re.sub(r"[\s_\-（()）【】\[\].]", "", str(value).lower())

    @classmethod
    def _currency_code(cls, value: Any) -> Optional[str]:
        normalized = cls._normalise_currency_text(value)
        for code, aliases in _CURRENCY_ALIASES.items():
            if normalized == code.lower() or any(
                cls._normalise_currency_text(alias) in normalized
                for alias in aliases
            ):
                return code
        return None

    @classmethod
    def _query_currency_code(cls, query: str) -> Optional[str]:
        normalized = cls._normalise_currency_text(query)
        for code, aliases in _CURRENCY_ALIASES.items():
            if code.lower() in normalized or any(
                cls._normalise_currency_text(alias) in normalized
                for alias in aliases
            ):
                return code
        return None

    @classmethod
    def _find_currency_column(cls, df: pd.DataFrame) -> Optional[str]:
        for column in df.columns:
            normalized = cls._normalise_currency_text(column)
            if normalized in _CURRENCY_COLUMN_NAMES:
                return str(column)
        return None

    @staticmethod
    def _is_money_query(query: str, value_col: str) -> bool:
        q_lower = query.lower()
        value_name = str(value_col).lower()
        return (
            any(keyword in q_lower for keyword in _MONEY_QUERY_KWS)
            and any(keyword in value_name for keyword in _MONEY_COL_KWS)
        )

    @classmethod
    def _find_complete_normalized_money_column(
        cls,
        df: pd.DataFrame,
        *,
        raw_value_col: str,
        group_col: str,
        field_map: Optional[SemanticFieldMap],
    ) -> Optional[str]:
        required_rows = df[group_col].notna() & pd.to_numeric(
            df[raw_value_col], errors="coerce"
        ).notna()
        required_count = int(required_rows.sum())
        if required_count == 0:
            return None

        candidates: List[Tuple[int, str]] = []
        for column in df.columns:
            column_name = str(column)
            if column_name in {raw_value_col, group_col}:
                continue
            currency_code = cls._currency_code(column_name)
            info = field_map.get(column_name) if field_map else None
            if currency_code is None and info is not None:
                for code in _NORMALIZED_MONEY_PRIORITY:
                    if code.lower() in info.qualifiers:
                        currency_code = code
                        break
            if currency_code is None or not any(
                keyword in column_name.lower() for keyword in _MONEY_COL_KWS
            ):
                continue
            converted = pd.to_numeric(df.loc[required_rows, column_name], errors="coerce")
            if int(converted.notna().sum()) != required_count:
                continue
            priority = _NORMALIZED_MONEY_PRIORITY.index(currency_code)
            candidates.append((priority, column_name))

        return min(candidates)[1] if candidates else None

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
