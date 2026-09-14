"""Semantic validation shared by deterministic and generated executors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pandas as pd

from ..context import ExecutionStep


_CURRENCY_COLUMNS = {"币别", "币种", "货币", "货币类型", "currency", "currencycode"}


@dataclass(frozen=True)
class ExecutionContractDecision:
    valid: bool
    reasons: List[str] = field(default_factory=list)


class ExecutionContractValidator:
    """Reject structurally valid DataFrames that contradict the semantic plan."""

    def validate(
        self,
        *,
        step: ExecutionStep,
        source_df: pd.DataFrame,
        result_df: pd.DataFrame,
    ) -> ExecutionContractDecision:
        reasons: List[str] = []
        semantics = step.semantics

        if semantics.limit is not None and len(result_df) > semantics.limit:
            reasons.append(
                f"result has {len(result_df)} rows but the semantic limit is {semantics.limit}"
            )

        if semantics.metrics and not result_df.empty:
            numeric_columns = list(result_df.select_dtypes(include="number").columns)
            aggregating = any(metric.aggregation != "none" for metric in semantics.metrics)
            if aggregating and not numeric_columns:
                reasons.append("aggregated metric result contains no numeric output column")

        for sort in semantics.sort:
            candidates = [
                column for column in result_df.columns
                if self._same_field(str(column), sort.field)
            ]
            if not candidates:
                continue
            sort_column = candidates[0]
            currency_column = next(
                (
                    column for column in result_df.columns
                    if self._normalize_field(str(column)) in _CURRENCY_COLUMNS
                ),
                None,
            )
            partitions = (
                [group for _, group in result_df.groupby(currency_column, dropna=False, sort=False)]
                if currency_column is not None
                else [result_df]
            )
            ordered = all(
                (
                    group[sort_column].dropna().is_monotonic_increasing
                    if sort.direction == "asc"
                    else group[sort_column].dropna().is_monotonic_decreasing
                )
                for group in partitions
            )
            if not ordered:
                reasons.append(f"result is not {sort.direction} by {candidates[0]!r}")

        if len(result_df.columns) == 0:
            reasons.append("result has no columns")
        if len(result_df) > 100_000:
            reasons.append("result exceeds the supported row limit")

        return ExecutionContractDecision(valid=not reasons, reasons=reasons)

    @staticmethod
    def _same_field(left: str, right: str) -> bool:
        a = ExecutionContractValidator._normalize_field(left)
        b = ExecutionContractValidator._normalize_field(right)
        return bool(a and b and (a == b or a in b or b in a))

    @staticmethod
    def _normalize_field(value: str) -> str:
        return "".join(
            character.lower() for character in value if character.isalnum()
        )
