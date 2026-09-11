"""Hybrid semantic typing grounded in deterministic dataframe evidence."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ..context import AnalysisContext
from ..models.configs import ModelRole
from .base import Skill

SemanticFieldMap = Dict[str, "SemanticFieldInfo"]
logger = logging.getLogger(__name__)


@dataclass
class SemanticFieldInfo:
    """Semantic metadata shared by profiling, code generation, and charting."""

    canonical_name: str
    aliases: List[str]
    type: str = "unknown"
    semantic_role: str = "unknown"
    should_aggregate: bool = False
    recommended_aggregation: str = "none"
    qualifiers: List[str] = field(default_factory=list)
    confidence: float = 0.0
    inference_source: str = "rule"
    reason: str = ""


@dataclass(frozen=True)
class ColumnEvidence:
    """Compact physical profile used by both rules and the semantic LLM."""

    column: str
    pandas_dtype: str
    non_null_count: int
    null_ratio: float
    unique_ratio: float
    numeric_ratio: float
    date_ratio: float
    sample_values: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "pandas_dtype": self.pandas_dtype,
            "non_null_count": self.non_null_count,
            "null_ratio": self.null_ratio,
            "unique_ratio": self.unique_ratio,
            "numeric_ratio": self.numeric_ratio,
            "date_ratio": self.date_ratio,
            "sample_values": self.sample_values,
        }


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

_ALLOWED_TYPES = {
    "numeric", "datetime", "datetime-like", "categorical", "identifier", "unknown"
}
_ALLOWED_AGGREGATIONS = {"sum", "avg", "min", "max", "count", "count_distinct", "last", "none"}
_AGGREGATION_AMBIGUITY_KWS = (
    "率", "比例", "占比", "percent", "ratio", "rate", "单价", "均价", "price",
    "累计", "余额", "库存", "存量", "balance", "inventory",
)


class SemanticTypingSkill(Skill):
    """Infer column semantics with rules first and an LLM for ambiguous fields."""

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
        evidence_map: Dict[str, ColumnEvidence] = {}
        for col in df.columns:
            col_name = str(col)
            evidence = self._build_evidence(col_name, df[col])
            evidence_map[col_name] = evidence
            field_type, confidence = self._infer_type(col_name, df[col])
            canonical, aliases, qualifiers = self._generate_names(col_name)
            aggregation = self._recommended_aggregation(col_name, field_type)
            result[col_name] = SemanticFieldInfo(
                canonical_name=canonical,
                aliases=aliases,
                type=field_type,
                semantic_role=self._semantic_role(field_type),
                should_aggregate=field_type == "numeric" and aggregation != "none",
                recommended_aggregation=aggregation,
                qualifiers=qualifiers,
                confidence=confidence,
                reason="deterministic name, dtype, and value-profile evidence",
            )

        ambiguous = [
            column
            for column, info in result.items()
            if self._needs_llm_review(column, info)
        ]
        if ambiguous:
            try:
                refinements = await self._llm_refine(
                    ctx,
                    query,
                    {column: evidence_map[column] for column in ambiguous[:40]},
                    {column: result[column] for column in ambiguous[:40]},
                )
                self._apply_refinements(result, evidence_map, refinements)
            except Exception as exc:
                logger.warning("[SemanticTyping] LLM refinement failed; using rules: %s", exc)
        return result

    @classmethod
    def _build_evidence(cls, column: str, series: pd.Series) -> ColumnEvidence:
        values = series.dropna()
        unique_ratio = float(values.nunique(dropna=True) / max(len(values), 1))
        return ColumnEvidence(
            column=column,
            pandas_dtype=str(series.dtype),
            non_null_count=int(len(values)),
            null_ratio=round(float(series.isna().mean()), 4),
            unique_ratio=round(unique_ratio, 4),
            numeric_ratio=round(cls._numeric_ratio(values), 4),
            date_ratio=round(cls._date_ratio(values), 4),
            sample_values=[
                str(value)
                for value in values.astype(str).drop_duplicates().head(5)
            ],
        )

    @classmethod
    def _needs_llm_review(cls, column: str, info: SemanticFieldInfo) -> bool:
        normalized = cls._normalise(column)
        return (
            info.confidence < 0.85
            or info.type == "unknown"
            or (
                info.type == "numeric"
                and (
                    any(keyword in normalized for keyword in _AGGREGATION_AMBIGUITY_KWS)
                    or not any(keyword in normalized for keyword in _NUMERIC_KWS)
                )
            )
        )

    async def _llm_refine(
        self,
        ctx: AnalysisContext,
        query: str,
        evidence: Dict[str, ColumnEvidence],
        rule_results: SemanticFieldMap,
    ) -> List[Dict[str, Any]]:
        provider = self.router.get_provider(ModelRole.SEMANTIC_TYPING)
        payload = []
        for column, profile in evidence.items():
            rule = rule_results[column]
            payload.append({
                **profile.to_dict(),
                "rule_type": rule.type,
                "rule_role": rule.semantic_role,
                "rule_aggregation": rule.recommended_aggregation,
                "rule_qualifiers": rule.qualifiers,
                "rule_confidence": rule.confidence,
            })
        system = (
            "你是表格字段语义判定器。只处理给出的疑难字段，不得新增或删除字段。"
            "结合列名、dtype、统计比例和样本值判断字段用途。"
            "type只能是numeric/datetime/datetime-like/categorical/identifier/unknown；"
            "aggregation只能是sum/avg/min/max/count/count_distinct/last/none。"
            "比例、率、单价通常用avg而不是sum；ID不得聚合；累计值、余额、库存快照通常用last或none。"
            "只输出JSON：{\"fields\":[{\"column\":\"...\",\"type\":\"...\","
            "\"aggregation\":\"...\",\"qualifiers\":[],\"canonical_name\":\"...\","
            "\"aliases\":[],\"confidence\":0.0,\"reason\":\"...\"}]}"
        )
        response = await provider.complete(
            messages=[{
                "role": "user",
                "content": (
                    f"用户问题：{query}\n"
                    f"字段证据：{json.dumps(payload, ensure_ascii=False)}"
                ),
            }],
            system=system,
            max_tokens=1600,
            temperature=0.0,
            json_mode=True,
        )
        data = self._parse_json(response)
        fields = data.get("fields", [])
        return fields if isinstance(fields, list) else []

    @classmethod
    def _apply_refinements(
        cls,
        result: SemanticFieldMap,
        evidence_map: Dict[str, ColumnEvidence],
        refinements: List[Dict[str, Any]],
    ) -> None:
        for raw in refinements:
            if not isinstance(raw, dict):
                continue
            column = str(raw.get("column", ""))
            if column not in result or column not in evidence_map:
                continue
            base = result[column]
            evidence = evidence_map[column]
            proposed_type = str(raw.get("type", base.type))
            confidence = cls._safe_confidence(raw.get("confidence"))
            if confidence < 0.65 or not cls._valid_type_override(
                base,
                evidence,
                proposed_type,
            ):
                continue

            aggregation = str(raw.get("aggregation", base.recommended_aggregation))
            if aggregation not in _ALLOWED_AGGREGATIONS:
                aggregation = base.recommended_aggregation
            if proposed_type == "identifier":
                aggregation = "count_distinct"
            elif proposed_type != "numeric":
                aggregation = "none"
            elif not cls._valid_aggregation(column, aggregation):
                aggregation = base.recommended_aggregation

            raw_qualifiers = raw.get("qualifiers", [])
            qualifiers = list(base.qualifiers)
            if isinstance(raw_qualifiers, list):
                for qualifier in raw_qualifiers[:6]:
                    value = str(qualifier).strip()
                    if (
                        value in _QUALIFIER_PATTERNS
                        and value not in qualifiers
                        and cls._qualifier_supported(value, evidence)
                    ):
                        qualifiers.append(value)

            occupied_names = {
                cls._normalise(other_column)
                for other_column in result
                if other_column != column
            }
            proposed_canonical = str(raw.get("canonical_name", "")).strip()
            canonical = (
                proposed_canonical
                if proposed_canonical
                and cls._normalise(proposed_canonical) not in occupied_names
                else base.canonical_name
            )
            aliases = list(base.aliases)
            raw_aliases = raw.get("aliases", [])
            if isinstance(raw_aliases, list):
                aliases.extend(
                    str(alias).strip()
                    for alias in raw_aliases[:6]
                    if str(alias).strip()
                    and cls._normalise(str(alias)) not in occupied_names
                )

            result[column] = SemanticFieldInfo(
                canonical_name=canonical,
                aliases=list(dict.fromkeys(aliases)),
                type=proposed_type,
                semantic_role=cls._semantic_role(proposed_type),
                should_aggregate=proposed_type == "numeric" and aggregation != "none",
                recommended_aggregation=aggregation,
                qualifiers=qualifiers,
                confidence=min(confidence, 0.92),
                inference_source="hybrid",
                reason=str(raw.get("reason", "LLM refinement validated against data evidence")),
            )

    @staticmethod
    def _valid_type_override(
        base: SemanticFieldInfo,
        evidence: ColumnEvidence,
        proposed_type: str,
    ) -> bool:
        if proposed_type not in _ALLOWED_TYPES:
            return False
        if base.type == "identifier" and base.confidence >= 0.95:
            return proposed_type == "identifier"
        if base.type == "datetime" and base.confidence >= 0.98:
            return proposed_type == "datetime"
        normalized_name = SemanticTypingSkill._normalise(evidence.column)
        if (
            base.type in {"datetime", "datetime-like"}
            and any(keyword in normalized_name for keyword in [*_DATETIME_KWS, *_PERIOD_KWS])
        ):
            return proposed_type in {"datetime", "datetime-like"}
        if proposed_type == "numeric" and evidence.numeric_ratio < 0.6:
            return False
        if proposed_type in {"datetime", "datetime-like"} and (
            evidence.date_ratio < 0.6 and base.type not in {"datetime", "datetime-like"}
        ):
            return False
        if proposed_type == "identifier" and (
            evidence.unique_ratio < 0.7 and base.type != "identifier"
        ):
            return False
        return True

    @classmethod
    def _valid_aggregation(cls, column: str, aggregation: str) -> bool:
        name = cls._normalise(column)
        if any(keyword in name for keyword in ("率", "比例", "占比", "单价", "均价", "rate", "ratio", "percent", "price")):
            return aggregation in {"avg", "min", "max", "none"}
        if any(keyword in name for keyword in ("累计", "余额", "库存", "存量", "balance", "inventory")):
            return aggregation in {"last", "min", "max", "none"}
        return True

    @staticmethod
    def _qualifier_supported(
        qualifier: str,
        evidence: ColumnEvidence,
    ) -> bool:
        haystack = " ".join([evidence.column, *evidence.sample_values]).lower()
        return any(
            str(alias).lower() in haystack
            for alias in _QUALIFIER_PATTERNS.get(qualifier, ())
        )

    @staticmethod
    def _safe_confidence(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        data = json.loads(text)
        return data if isinstance(data, dict) else {}

    @classmethod
    def _infer_type(cls, col_name: str, series: pd.Series) -> tuple[str, float]:
        name = cls._normalise(col_name)
        values = series.dropna()

        # Names win for identifiers: numeric-looking IDs must never become metrics.
        if any(keyword in name for keyword in _IDENTIFIER_KWS):
            return "identifier", 0.98

        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime", 0.99
        if any(keyword in name for keyword in _PERIOD_KWS) and cls._looks_like_period(values):
            return "datetime-like", 0.93
        if any(keyword in name for keyword in _DATETIME_KWS):
            if cls._looks_like_date(values):
                return "datetime", 0.92
            return "datetime-like", 0.65
        if cls._looks_like_identifier_values(values):
            return "identifier", 0.75

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
    def _date_ratio(values: pd.Series) -> float:
        sample = values.head(30)
        if sample.empty:
            return 0.0
        text = sample.astype(str).str.strip()
        plausible = text.str.contains(r"[-/年月日:]", regex=True) | text.str.match(
            _PERIOD_VALUE_RE
        )
        if not plausible.any():
            return 0.0
        parsed = pd.to_datetime(sample.where(plausible), errors="coerce", format="mixed")
        return float(parsed.notna().mean())

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
    def _recommended_aggregation(cls, col_name: str, field_type: str) -> str:
        if field_type == "identifier":
            return "count_distinct"
        if field_type != "numeric":
            return "none"
        name = cls._normalise(col_name)
        if any(keyword in name for keyword in ("累计", "余额", "库存", "存量", "balance", "inventory")):
            return "last"
        if any(keyword in name for keyword in ("率", "比例", "占比", "单价", "均价", "rate", "ratio", "percent", "price")):
            return "avg"
        return "sum"

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
