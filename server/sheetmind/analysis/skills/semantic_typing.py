"""Rule-based semantic typing and alias generation for dataframe columns."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

from ..context import AnalysisContext
from .base import Skill

SemanticFieldMap = Dict[str, "SemanticFieldInfo"]


@dataclass
class SemanticFieldInfo:
    """Semantic metadata shared by profiling, code generation, and charting."""

    canonical_name: str
    aliases: List[str]
    type: str = "unknown"
    semantic_role: str = "unknown"
    should_aggregate: bool = False
    qualifiers: List[str] = field(default_factory=list)
    confidence: float = 0.0


_NUMERIC_KWS = [
    "金额", "数量", "价格", "成本", "收入", "销售额", "利润", "费用", "总额", "单价",
    "amount", "price", "cost", "revenue", "sales", "profit", "total", "qty", "quantity",
    "count", "sum", "avg", "rate", "ratio", "percent", "score", "index",
]
_DATETIME_KWS = ["日期", "时间", "date", "time", "datetime", "at", "on"]
_PERIOD_KWS = ["年月", "月份", "月度", "年份", "年度", "季度", "period", "month", "year", "quarter", "week", "周"]
_IDENTIFIER_KWS = [
    "id", "编号", "编码", "代码", "订单号", "单号", "sku", "客户id", "用户id",
    "tracking", "tracking_no", "waybill", "运单号", "货号", "序号",
]
_CATEGORICAL_KWS = [
    "名称", "类别", "类型", "状态", "渠道", "地区", "区域", "品牌", "产品", "商品",
    "客户", "供应商", "部门", "name", "category", "type", "status", "channel", "region",
    "brand", "product", "customer", "supplier", "department",
]

_QUALIFIER_PATTERNS = {
    "rmb": ("人民币", "rmb", "cny", "¥", "元"),
    "usd": ("美元", "usd", "$"),
    "tax_included": ("含税", "价税合计"),
    "tax_excluded": ("不含税", "未税"),
    "original_currency": ("原币",),
    "local_currency": ("本币",),
    "percent": ("百分比", "百分率", "%", "率"),
    "quantity": ("件", "数量", "qty", "quantity"),
}

_PERIOD_VALUE_RE = re.compile(
    r"^(?:\d{4}|(?:\d{4}[-/]?\d{1,2})|(?:\d{4}[-/]?q[1-4])|q[1-4]|\d{1,2}月)$",
    re.IGNORECASE,
)


class SemanticTypingSkill(Skill):
    """Infer reliable column intent without an LLM call."""

    name = "semantic_typing"
    description = "Infer field type, aggregation safety, aliases, and qualifiers"

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
            col_name = str(col)
            field_type, confidence = self._infer_type(col_name, df[col])
            canonical, aliases, qualifiers = self._generate_names(col_name)
            result[col_name] = SemanticFieldInfo(
                canonical_name=canonical,
                aliases=aliases,
                type=field_type,
                semantic_role=self._semantic_role(field_type),
                should_aggregate=field_type == "numeric",
                qualifiers=qualifiers,
                confidence=confidence,
            )
        return result

    @classmethod
    def _infer_type(cls, col_name: str, series: pd.Series) -> tuple[str, float]:
        name = cls._normalise(col_name)
        values = series.dropna()

        # Names win for identifiers: numeric-looking IDs must never become metrics.
        if any(keyword in name for keyword in _IDENTIFIER_KWS):
            return "identifier", 0.98
        if cls._looks_like_identifier_values(values):
            return "identifier", 0.75

        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime", 0.99
        if any(keyword in name for keyword in _PERIOD_KWS) and cls._looks_like_period(values):
            return "datetime-like", 0.93
        if any(keyword in name for keyword in _DATETIME_KWS):
            if cls._looks_like_date(values):
                return "datetime", 0.92
            return "datetime-like", 0.65

        if pd.api.types.is_numeric_dtype(series):
            return "numeric", 0.97

        numeric_ratio = cls._numeric_ratio(values)
        if any(keyword in name for keyword in _NUMERIC_KWS) and numeric_ratio >= 0.6:
            return "numeric", 0.90
        if cls._looks_like_period(values):
            return "datetime-like", 0.82
        if cls._looks_like_date(values):
            return "datetime", 0.82
        if any(keyword in name for keyword in _CATEGORICAL_KWS) or series.dtype == object:
            return "categorical", 0.75
        return "unknown", 0.3

    @staticmethod
    def _normalise(value: str) -> str:
        return re.sub(r"[\s_\-（(）)【】\[\]{}]", "", str(value)).lower()

    @staticmethod
    def _numeric_ratio(values: pd.Series) -> float:
        if values.empty:
            return 0.0
        converted = pd.to_numeric(values.astype(str).str.replace(",", "", regex=False), errors="coerce")
        return float(converted.notna().mean())

    @staticmethod
    def _looks_like_date(values: pd.Series) -> bool:
        sample = values.head(10)
        if sample.empty:
            return False
        text = sample.astype(str)
        if not text.str.contains(r"[-/年月日]", regex=True).any():
            return False
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        return float(parsed.notna().mean()) >= 0.8

    @staticmethod
    def _looks_like_period(values: pd.Series) -> bool:
        sample = values.head(10)
        if sample.empty:
            return False
        return float(sample.astype(str).str.strip().str.match(_PERIOD_VALUE_RE).mean()) >= 0.8

    @staticmethod
    def _looks_like_identifier_values(values: pd.Series) -> bool:
        if values.empty or len(values) < 2:
            return False
        text = values.head(50).astype(str).str.strip()
        unique_ratio = text.nunique() / max(len(text), 1)
        return unique_ratio >= 0.95 and float(text.str.match(r"^0*\d{6,}$").mean()) >= 0.8

    @staticmethod
    def _semantic_role(field_type: str) -> str:
        return {
            "numeric": "metric",
            "datetime": "time_dimension",
            "datetime-like": "period_dimension",
            "categorical": "dimension",
            "identifier": "identifier",
        }.get(field_type, "unknown")

    @classmethod
    def _generate_names(cls, col_name: str) -> tuple[str, List[str], List[str]]:
        normalised = cls._normalise(col_name)
        qualifiers = [
            qualifier for qualifier, aliases in _QUALIFIER_PATTERNS.items()
            if any(alias.lower() in normalised or alias in col_name.lower() for alias in aliases)
        ]

        canonical = re.sub(r"[（(].*?[）)]", "", col_name)
        for qualifier, aliases in _QUALIFIER_PATTERNS.items():
            if qualifier in qualifiers:
                for alias in aliases:
                    canonical = re.sub(re.escape(alias), "", canonical, flags=re.IGNORECASE)
        canonical = re.sub(r"[\s_\-（(）)【】\[\]{}]", "", canonical).strip() or col_name

        aliases = [col_name, cls._normalise(col_name), canonical]
        if "rmb" in qualifiers:
            aliases.extend([f"人民币{canonical}", f"{canonical}人民币", f"{canonical}RMB", f"{canonical}（RMB）", "rmb_amount"])
        if "usd" in qualifiers:
            aliases.extend([f"美元{canonical}", f"{canonical}美元", f"{canonical}USD", f"{canonical}（USD）", "usd_amount"])
        if canonical in {"金额", "销售额", "收入"}:
            aliases.extend(["amount", "sales_amount", "revenue"])

        return canonical, list(dict.fromkeys(alias for alias in aliases if alias)), qualifiers
