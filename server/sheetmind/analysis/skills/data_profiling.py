"""Structured dataframe profiling with a compact prompt-friendly text view."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from ..context import AnalysisContext
from .base import Skill
from .semantic_typing import SemanticFieldMap


@dataclass
class ColumnProfile:
    name: str
    semantic_type: str
    null_count: int
    null_ratio: float
    unique_count: int
    unique_ratio: float
    sample_values: List[str]
    numeric_stats: Optional[Dict[str, float]] = None
    date_range: Optional[Dict[str, str]] = None
    recommended_aggregation: Optional[str] = None


class DataProfile(str):
    """Structured profile that remains usable anywhere a prompt string was expected."""

    def __new__(
        cls,
        prompt_text: str,
        *,
        row_count: int,
        column_count: int,
        columns: List[ColumnProfile],
        metric_candidates: List[str],
        dimension_candidates: List[str],
        date_candidates: List[str],
        warnings: List[str],
    ) -> "DataProfile":
        instance = str.__new__(cls, prompt_text)
        instance.prompt_text = prompt_text
        instance.row_count = row_count
        instance.column_count = column_count
        instance.columns = columns
        instance.metric_candidates = metric_candidates
        instance.dimension_candidates = dimension_candidates
        instance.date_candidates = date_candidates
        instance.warnings = warnings
        return instance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": [asdict(column) for column in self.columns],
            "metric_candidates": self.metric_candidates,
            "dimension_candidates": self.dimension_candidates,
            "date_candidates": self.date_candidates,
            "warnings": self.warnings,
        }


class DataProfilingSkill(Skill):
    """Profile data once and expose both machine-readable and prompt-ready views."""

    name = "data_profiling"
    description = "Build structured column profiles and a compact LLM prompt view"

    MAX_SAMPLE_VALUES = 4
    MAX_COLS_DETAIL = 20

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: pd.DataFrame = None,  # type: ignore[assignment]
        field_map: Optional[SemanticFieldMap] = None,
        **kwargs: Any,
    ) -> DataProfile:
        if df is None or df.empty:
            return DataProfile(
                "（数据为空）",
                row_count=0,
                column_count=0 if df is None else len(df.columns),
                columns=[],
                metric_candidates=[],
                dimension_candidates=[],
                date_candidates=[],
                warnings=["数据为空，无法生成统计画像。"],
            )

        profiles: List[ColumnProfile] = []
        warnings: List[str] = []
        duplicate_columns = df.columns[df.columns.duplicated()].tolist()
        if duplicate_columns:
            warnings.append(f"存在重复列名：{duplicate_columns}")

        for col in df.columns:
            name = str(col)
            series = df[col]
            semantic = field_map.get(name) if field_map else None
            semantic_type = semantic.type if semantic else self._infer_type(series)
            profiles.append(self._profile_column(name, series, semantic_type))

        metric_candidates = [profile.name for profile in profiles if profile.semantic_type == "numeric"]
        dimension_candidates = [
            profile.name for profile in profiles
            if profile.semantic_type in {"categorical", "identifier"}
        ]
        date_candidates = [
            profile.name for profile in profiles
            if profile.semantic_type in {"datetime", "datetime-like"}
        ]
        prompt_text = self._to_prompt_text(df, profiles, warnings)
        return DataProfile(
            prompt_text,
            row_count=len(df),
            column_count=len(df.columns),
            columns=profiles,
            metric_candidates=metric_candidates,
            dimension_candidates=dimension_candidates,
            date_candidates=date_candidates,
            warnings=warnings,
        )

    def _profile_column(self, name: str, series: pd.Series, semantic_type: str) -> ColumnProfile:
        non_null = series.dropna()
        numeric_series = pd.to_numeric(series, errors="coerce")
        numeric_stats: Optional[Dict[str, float]] = None
        if semantic_type == "numeric" and numeric_series.notna().any():
            numeric_stats = {
                "min": float(numeric_series.min()),
                "max": float(numeric_series.max()),
                "mean": float(numeric_series.mean()),
                "sum": float(numeric_series.sum()),
            }

        date_range: Optional[Dict[str, str]] = None
        if semantic_type == "datetime":
            dates = pd.to_datetime(non_null, errors="coerce", format="mixed").dropna()
            if not dates.empty:
                date_range = {"min": str(dates.min().date()), "max": str(dates.max().date())}

        samples = [str(value) for value in non_null.astype(str).drop_duplicates().head(self.MAX_SAMPLE_VALUES)]
        return ColumnProfile(
            name=name,
            semantic_type=semantic_type,
            null_count=int(series.isna().sum()),
            null_ratio=round(float(series.isna().mean()), 4),
            unique_count=int(series.nunique(dropna=True)),
            unique_ratio=round(float(series.nunique(dropna=True) / max(len(series), 1)), 4),
            sample_values=samples,
            numeric_stats=numeric_stats,
            date_range=date_range,
            recommended_aggregation=self._recommended_aggregation(name, semantic_type),
        )

    @staticmethod
    def _infer_type(series: pd.Series) -> str:
        if pd.api.types.is_numeric_dtype(series):
            return "numeric"
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        return "categorical"

    @staticmethod
    def _recommended_aggregation(name: str, semantic_type: str) -> Optional[str]:
        lowered = name.lower()
        if semantic_type == "identifier":
            return "count_distinct"
        if semantic_type != "numeric":
            return None
        if any(keyword in lowered for keyword in ("价格", "单价", "折扣", "率", "price", "discount", "rate")):
            return "avg"
        return "sum"

    def _to_prompt_text(
        self,
        df: pd.DataFrame,
        profiles: List[ColumnProfile],
        warnings: List[str],
    ) -> str:
        lines = [f"数据规模：{len(df)} 行 × {len(df.columns)} 列", f"列信息（前 {min(len(profiles), self.MAX_COLS_DETAIL)} 列）："]
        for profile in profiles[: self.MAX_COLS_DETAIL]:
            stats = ""
            if profile.numeric_stats:
                stats = " | " + " ".join(
                    f"{key}={value:.2f}" for key, value in profile.numeric_stats.items()
                )
            agg = f" | 建议聚合={profile.recommended_aggregation}" if profile.recommended_aggregation else ""
            lines.append(
                f"  {profile.name!r}: 类型={profile.semantic_type}, unique={profile.unique_count}, "
                f"nulls={profile.null_count}, 示例=[{', '.join(profile.sample_values)}]{stats}{agg}"
            )
        if len(profiles) > self.MAX_COLS_DETAIL:
            lines.append(f"  ... （省略了 {len(profiles) - self.MAX_COLS_DETAIL} 列）")
        lines.extend(f"警告：{warning}" for warning in warnings)
        return "\n".join(lines)
