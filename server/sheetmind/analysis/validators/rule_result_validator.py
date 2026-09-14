"""Validate that a deterministic rule result satisfies its execution step."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import pandas as pd

from ..context import ExecutionStep
from ..tools.rule_engine import RuleResult


_OPERATION_RULE_COVERAGE: Dict[str, Set[str]] = {
    "filter": {"keyword_filter", "date_filter"},
    "date_filter": {"date_filter"},
    "sort": {"sort"},
    "pass_through": {"pass_through"},
}
_ASCENDING_TERMS = ("升序", "从低到高", "从小到大", "ascending", "asc")
_DATE_RANGE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})"
)
_DATE_YEAR_MONTH = re.compile(r"(\d{4})[年\-/](\d{1,2})[月]?")
_DATE_YEAR_ONLY = re.compile(r"(\d{4})年(?![0-9])")


@dataclass(frozen=True)
class RuleValidationDecision:
    valid: bool
    fallback_to_codegen: bool
    reasons: List[str] = field(default_factory=list)


class RuleResultValidator:
    """Enforce deterministic operation coverage before accepting rule output."""

    def validate(
        self,
        *,
        step: ExecutionStep,
        source_df: pd.DataFrame,
        rule_result: RuleResult,
    ) -> RuleValidationDecision:
        matched = set(rule_result.matched_rules)
        reasons: List[str] = []

        for operation in step.operation_intents:
            accepted_rules = _OPERATION_RULE_COVERAGE.get(operation)
            if accepted_rules is not None and not matched.intersection(accepted_rules):
                reasons.append(
                    f"operation {operation!r} was not covered by matched rules "
                    f"{sorted(matched)}"
                )

        operation_set = set(step.operation_intents)
        requires_extreme_aggregation = {"which", "extreme"} <= operation_set
        if requires_extreme_aggregation and "aggregate_extreme" not in matched:
            reasons.append(
                "operations 'which' + 'extreme' were not covered by aggregate_extreme"
            )

        actionable_operations = set(step.operation_intents).intersection(
            _OPERATION_RULE_COVERAGE
        )
        if requires_extreme_aggregation:
            actionable_operations.add("aggregate_extreme")
        if (
            not actionable_operations
            and "vague" in step.operation_intents
            and "pass_through" not in matched
        ):
            reasons.append(
                f"vague data request expected pass-through, got {sorted(matched)}"
            )

        result_columns = {str(column) for column in rule_result.result_df.columns}
        if "aggregate_extreme" in matched:
            # Field resolution may only identify the grouping dimension; the
            # deterministic aggregate engine selects its numeric metric itself.
            # Validate the result shape directly instead of requiring every
            # source/filter field to survive a groupby projection.
            numeric_columns = list(
                rule_result.result_df.select_dtypes(include="number").columns
            )
            if len(result_columns) < 2 or not numeric_columns:
                reasons.append(
                    "aggregate result does not retain both a dimension and metric"
                )
        else:
            missing_required = [
                column
                for column in step.required_source_columns
                if column not in result_columns
            ]
            if missing_required:
                reasons.append(
                    f"required columns are missing from rule result: {missing_required}"
                )

        non_aggregating = {"filter", "date_filter", "sort", "pass_through", "vague"}
        if (
            set(step.operation_intents).intersection(non_aggregating)
            and len(rule_result.result_df) > len(source_df)
        ):
            reasons.append(
                "rule result row count exceeds its source for a non-aggregating operation"
            )

        pass_through_expected = (
            "pass_through" in step.operation_intents
            or (
                not actionable_operations
                and "vague" in step.operation_intents
            )
        )
        if "pass_through" in matched and pass_through_expected:
            source = source_df.reset_index(drop=True)
            result = rule_result.result_df.reset_index(drop=True)
            if list(result.columns) != list(source.columns) or not result.equals(source):
                reasons.append("pass-through rule result changed source rows or columns")

        if "sort" in step.operation_intents and "sort" in matched:
            sort_column = self._resolve_sort_column(step, rule_result.result_df)
            if sort_column is not None:
                values = rule_result.result_df[sort_column].dropna()
                ascending = any(term in step.query.lower() for term in _ASCENDING_TERMS)
                ordered = (
                    values.is_monotonic_increasing
                    if ascending
                    else values.is_monotonic_decreasing
                )
                if not ordered:
                    direction = "ascending" if ascending else "descending"
                    reasons.append(
                        f"rule result is not {direction} by {sort_column!r}"
                    )

        if (
            "date_filter" in step.operation_intents
            and "date_filter" in matched
        ):
            date_reason = self._validate_date_period(
                step,
                rule_result.result_df,
            )
            if date_reason:
                reasons.append(date_reason)

        return RuleValidationDecision(
            valid=not reasons,
            fallback_to_codegen=bool(reasons),
            reasons=reasons,
        )

    @staticmethod
    def _resolve_sort_column(
        step: ExecutionStep,
        result_df: pd.DataFrame,
    ) -> Optional[str]:
        result_columns = {str(column) for column in result_df.columns}
        for column in [*step.required_source_columns, *step.target_fields]:
            if column in result_columns:
                return column
        for column in result_df.columns:
            if str(column) in step.query:
                return str(column)
        return None

    @staticmethod
    def _resolve_date_column(
        step: ExecutionStep,
        result_df: pd.DataFrame,
    ) -> Optional[str]:
        for column in step.required_source_columns:
            if column in result_df.columns and (
                pd.api.types.is_datetime64_any_dtype(result_df[column])
                or any(term in column.lower() for term in ("日期", "时间", "年月", "date", "time"))
            ):
                return column
        for column in result_df.columns:
            if pd.api.types.is_datetime64_any_dtype(result_df[column]):
                return str(column)
        for column in result_df.columns:
            name = str(column).lower()
            if any(term in name for term in ("日期", "时间", "年月", "date", "time")):
                return str(column)
        return None

    @classmethod
    def _validate_date_period(
        cls,
        step: ExecutionStep,
        result_df: pd.DataFrame,
    ) -> Optional[str]:
        query = step.normalized_query or step.query
        date_column = cls._resolve_date_column(step, result_df)
        if date_column is None:
            return "rule result has no column for date validation"
        if result_df.empty:
            return None

        values = pd.to_datetime(result_df[date_column], errors="coerce")
        if values.isna().any():
            return f"rule result contains unparseable dates in {date_column!r}"

        date_range = _DATE_RANGE.search(query)
        if date_range is not None:
            start = pd.Timestamp(date_range.group(1))
            end = pd.Timestamp(date_range.group(2))
            if not ((values >= start) & (values < end)).all():
                return (
                    "rule result contains rows outside date range "
                    f"[{start.date()}, {end.date()})"
                )
            return None

        year_month = _DATE_YEAR_MONTH.search(query)
        if year_month is not None:
            year = int(year_month.group(1))
            month = int(year_month.group(2))
            if not ((values.dt.year == year) & (values.dt.month == month)).all():
                return f"rule result contains rows outside {year:04d}-{month:02d}"
            return None

        year_only = _DATE_YEAR_ONLY.search(query)
        if year_only is not None:
            year = int(year_only.group(1))
            if not (values.dt.year == year).all():
                return f"rule result contains rows outside year {year}"
        return None
