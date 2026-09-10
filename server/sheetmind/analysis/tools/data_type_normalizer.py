"""Deterministic dataframe type normalization based on semantic metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

from ..context import AnalysisContext
from ..skills.semantic_typing import SemanticFieldMap
from .base import Tool


@dataclass
class TypeNormalizationResult:
    df: pd.DataFrame
    converted_columns: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class DataTypeNormalizationTool(Tool):
    """Convert confidently identified temporal fields without mutating source data."""

    name = "data_type_normalizer"
    description = "Normalize semantic date and period fields for deterministic execution"
    MIN_PARSE_RATIO = 0.80

    def run(
        self,
        ctx: AnalysisContext,
        *,
        df: Optional[pd.DataFrame] = None,
        field_map: Optional[SemanticFieldMap] = None,
        **kwargs,
    ) -> TypeNormalizationResult:
        if df is None:
            return TypeNormalizationResult(df=pd.DataFrame())

        normalized = df.copy()
        converted: List[str] = []
        warnings: List[str] = []
        for column, info in (field_map or {}).items():
            if column not in normalized.columns:
                continue
            if info.type not in {"datetime", "datetime-like"} or info.confidence < 0.60:
                continue
            series = normalized[column]
            if pd.api.types.is_datetime64_any_dtype(series):
                continue

            parsed, strategy = self._parse_temporal_series(series)
            if parsed is None:
                warnings.append(f"时间字段 {column!r} 无法安全标准化，保留原始类型。")
                continue
            non_null = int(series.notna().sum())
            parse_ratio = float(parsed.notna().sum()) / max(non_null, 1)
            if parse_ratio < self.MIN_PARSE_RATIO:
                warnings.append(
                    f"时间字段 {column!r} 仅成功解析 {parse_ratio:.0%}，保留原始类型。"
                )
                continue

            normalized[column] = parsed
            converted.append(column)
            if parse_ratio < 1.0:
                warnings.append(
                    f"时间字段 {column!r} 使用 {strategy} 标准化，"
                    f"有 {non_null - int(parsed.notna().sum())} 个值无法解析。"
                )

        return TypeNormalizationResult(
            df=normalized,
            converted_columns=converted,
            warnings=warnings,
        )

    @staticmethod
    def _parse_temporal_series(series: pd.Series) -> Tuple[Optional[pd.Series], str]:
        values = series.dropna()
        if values.empty:
            return None, "empty"

        text = values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        full_text = series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

        if bool(text.str.fullmatch(r"\d{6}").all()):
            parsed = pd.to_datetime(full_text, format="%Y%m", errors="coerce")
            if parsed.notna().any():
                return parsed, "YYYYMM"
        if bool(text.str.fullmatch(r"\d{8}").all()):
            parsed = pd.to_datetime(full_text, format="%Y%m%d", errors="coerce")
            if parsed.notna().any():
                return parsed, "YYYYMMDD"
        if bool(text.str.fullmatch(r"\d{4}").all()):
            parsed = pd.to_datetime(full_text, format="%Y", errors="coerce")
            if parsed.notna().any():
                return parsed, "YYYY"

        numeric = pd.to_numeric(series, errors="coerce")
        numeric_values = numeric.dropna()
        if len(numeric_values) == len(values):
            median = float(numeric_values.median())
            if 20_000 <= median <= 80_000:
                return (
                    pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce"),
                    "Excel serial date",
                )
            if 1_000_000_000 <= median <= 4_000_000_000:
                return pd.to_datetime(numeric, unit="s", errors="coerce"), "Unix seconds"
            if 1_000_000_000_000 <= median <= 4_000_000_000_000:
                return pd.to_datetime(numeric, unit="ms", errors="coerce"), "Unix milliseconds"

        if bool(text.str.contains(r"[-/年月日T:]", regex=True).any()):
            return pd.to_datetime(series, errors="coerce", format="mixed"), "calendar text"
        return None, "unsupported"
