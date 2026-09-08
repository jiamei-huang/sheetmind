"""Rule-based chart planning grounded in semantic field metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

import pandas as pd

from ..context import AnalysisContext, ChartBlock, ChartSeries
from .base import Skill
from .field_resolution import FieldResolver
from .semantic_typing import SemanticFieldMap

_BAR_KWS = ["柱状图", "柱图", "bar", "柱状", "对比", "比较", "排名", "各个", "各", "每个"]
_LINE_KWS = ["折线图", "折线", "line", "趋势", "走势", "变化", "trend", "时序", "时间序列"]
_PIE_KWS = ["饼图", "pie", "占比", "比例", "份额", "percentage", "composition", "构成"]
_NEGATION_PATTERN = re.compile(r"(?:不是|不用|不要|换掉|替换|remove|not)\s*([^\s，,。.！!？?]{1,20})")
_IDENTIFIER_NAME_RE = re.compile(r"(?:^|[_\-\s])(id|sku)(?:$|[_\-\s])|编号|编码|订单号|单号|客户id|用户id|tracking|waybill", re.I)


@dataclass
class ChartSpec:
    chart_type: str
    x_col: str
    y_cols: List[str]
    confidence: float
    reason: str


class ChartPlanningSkill(Skill):
    name = "chart_planning"
    description = "Build a safe chart plan using semantic metadata"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        result_df: Optional[pd.DataFrame] = None,
        field_map: Optional[SemanticFieldMap] = None,
        **kwargs: Any,
    ) -> Optional[ChartBlock]:
        if result_df is None or result_df.empty:
            return None

        negated = self._extract_negated_cols(query, result_df)
        x_col = self._select_x_col(query, result_df, negated, field_map)
        y_cols = self._select_y_cols(query, result_df, x_col, negated, field_map)
        if not x_col or not y_cols:
            return None

        chart_type = self._determine_chart_type(query, result_df, x_col, field_map)
        reason = self._plan_reason(x_col, y_cols, field_map)
        confidence = 0.91 if field_map and all(col in field_map for col in [x_col, *y_cols]) else 0.72
        spec = ChartSpec(chart_type, x_col, y_cols, confidence, reason)
        try:
            return self._build_chart_block(result_df, spec, field_map)
        except Exception:
            return None

    def _determine_chart_type(
        self,
        query: str,
        df: pd.DataFrame,
        x_col: str,
        field_map: Optional[SemanticFieldMap],
    ) -> str:
        q_lower = query.lower()
        if any(kw in q_lower for kw in _PIE_KWS):
            return "pie"
        if any(kw in q_lower for kw in _LINE_KWS):
            return "line"
        if any(kw in q_lower for kw in _BAR_KWS):
            return "bar"
        if field_map and field_map.get(x_col, None) and field_map[x_col].type in {"datetime", "datetime-like"}:
            return "line"
        if pd.api.types.is_datetime64_any_dtype(df[x_col]):
            return "line"
        return "bar"

    def _select_x_col(
        self,
        query: str,
        df: pd.DataFrame,
        negated: set[str],
        field_map: Optional[SemanticFieldMap],
    ) -> Optional[str]:
        if field_map:
            resolution = FieldResolver().resolve(
                query,
                {col: info for col, info in field_map.items() if col in df.columns and col not in negated},
                allowed_types={"categorical", "datetime", "datetime-like"},
            )
            if resolution:
                return resolution.column

        for col in df.columns:
            if col in negated or self._is_identifier(col, field_map):
                continue
            if field_map and field_map.get(col, None) and field_map[col].type in {"datetime", "datetime-like"}:
                return col
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                return col
        for col in df.columns:
            if col not in negated and not self._is_identifier(col, field_map) and not pd.api.types.is_numeric_dtype(df[col]):
                return col
        return next((col for col in df.columns if col not in negated), None)

    def _select_y_cols(
        self,
        query: str,
        df: pd.DataFrame,
        x_col: Optional[str],
        negated: set[str],
        field_map: Optional[SemanticFieldMap],
    ) -> List[str]:
        candidates = [
            col for col in df.columns
            if col != x_col and col not in negated and pd.api.types.is_numeric_dtype(df[col])
            and not self._is_identifier(col, field_map)
            and (not field_map or field_map.get(col, None) is None or field_map[col].should_aggregate)
        ]
        if not candidates:
            return []

        if field_map:
            resolution = FieldResolver().resolve(
                query,
                {col: field_map[col] for col in candidates if col in field_map},
                aggregate_only=True,
            )
            if resolution:
                return [resolution.column]

        for col in candidates:
            clean = re.sub(r"[（(）)\[\]【】]", "", str(col))
            if str(col) in query or clean in query:
                return [col]
        keyword_matches = [
            col for col in candidates
            if any(keyword in str(col).lower() for keyword in ("金额", "销售额", "收入", "利润", "数量", "费用", "价格", "amount", "revenue", "sales", "profit", "count", "qty"))
        ]
        return (keyword_matches or candidates)[:5]

    @staticmethod
    def _is_identifier(column: str, field_map: Optional[SemanticFieldMap]) -> bool:
        return bool(field_map and column in field_map and field_map[column].type == "identifier") or bool(
            _IDENTIFIER_NAME_RE.search(str(column))
        )

    @staticmethod
    def _extract_negated_cols(query: str, df: pd.DataFrame) -> set[str]:
        negated: set[str] = set()
        for match in _NEGATION_PATTERN.finditer(query):
            token = match.group(1).strip()
            negated.update(str(col) for col in df.columns if token in str(col) or str(col) in token)
        return negated

    @staticmethod
    def _plan_reason(x_col: str, y_cols: List[str], field_map: Optional[SemanticFieldMap]) -> str:
        if field_map and x_col in field_map:
            return f"{field_map[x_col].type} X-axis + numeric metric ({', '.join(y_cols)})"
        return f"dimension X-axis + numeric metric ({', '.join(y_cols)})"

    @staticmethod
    def _build_chart_block(
        df: pd.DataFrame,
        spec: ChartSpec,
        field_map: Optional[SemanticFieldMap],
    ) -> ChartBlock:
        plot_df = df[[spec.x_col, *spec.y_cols]].copy()
        if spec.chart_type in {"bar", "pie"} and len(plot_df) > 12:
            # Keep crowded categorical charts legible and preserve their total in Other.
            primary = spec.y_cols[0]
            plot_df = plot_df.assign(_sort_value=pd.to_numeric(plot_df[primary], errors="coerce").fillna(0))
            top = plot_df.nlargest(10, "_sort_value").drop(columns="_sort_value")
            rest = plot_df.drop(index=top.index)
            if not rest.empty:
                other = {spec.x_col: "Other"}
                for col in spec.y_cols:
                    other[col] = pd.to_numeric(rest[col], errors="coerce").sum()
                plot_df = pd.concat([top, pd.DataFrame([other])], ignore_index=True)
            else:
                plot_df = top
        else:
            plot_df = plot_df.head(500)

        labels = [str(value) for value in plot_df[spec.x_col].tolist()]
        series = [
            ChartSeries(
                name=column,
                values=[
                    float(value) if value is not None and pd.notna(value) else None
                    for value in pd.to_numeric(plot_df[column], errors="coerce").tolist()
                ],
            )
            for column in spec.y_cols
        ]
        unit = ""
        if field_map and spec.y_cols[0] in field_map:
            qualifiers = field_map[spec.y_cols[0]].qualifiers
            unit = {"rmb": "RMB", "usd": "USD", "percent": "%", "quantity": "quantity"}.get(
                next((qualifier for qualifier in qualifiers if qualifier in {"rmb", "usd", "percent", "quantity"}), ""), ""
            )
        y_axis_label = f"{spec.y_cols[0]} ({unit})" if unit and unit.lower() not in spec.y_cols[0].lower() else spec.y_cols[0]
        return ChartBlock(
            chart_type=spec.chart_type,
            labels=labels,
            series=series,
            x_axis_label=spec.x_col,
            y_axis_label=y_axis_label,
            confidence=spec.confidence,
            reason=spec.reason,
        )
