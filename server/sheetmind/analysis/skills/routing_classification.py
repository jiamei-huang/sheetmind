"""Classify normalized query structure, operations, outputs, and route."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context import AnalysisContext, MultiTurnMode, RoutingHint
from ..models.configs import ModelRole
from .base import Skill
from .query_normalization import NormalizedQuery, QueryNormalizationSkill


_ROUTING_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "routing_rules.json"


def _load_routing_rules() -> Dict[str, Any]:
    with _ROUTING_RULES_PATH.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data.get("level_1"), dict) or not isinstance(data.get("level_2"), dict):
        raise ValueError(f"routing rules must contain level_1 and level_2: {_ROUTING_RULES_PATH}")
    return data


def _keywords(groups: Dict[str, Any], group_name: str) -> List[str]:
    values = groups.get(group_name, [])
    if not isinstance(values, list):
        raise ValueError(f"routing keyword group must be a list: {group_name}")
    return [str(value).lower() for value in values]


_ROUTING_RULES = _load_routing_rules()
_LEVEL_1_GROUPS: Dict[str, Any] = _ROUTING_RULES["level_1"].get("keyword_groups", {})
_LEVEL_1_THRESHOLDS: Dict[str, Any] = _ROUTING_RULES["level_1"].get("thresholds", {})
_OPERATION_TERMS: Dict[str, List[str]] = {
    name: _keywords(_ROUTING_RULES["level_2"].get("operation_terms", {}), name)
    for name in _ROUTING_RULES["level_2"].get("operation_terms", {})
}
_OUTPUT_TERMS: Dict[str, List[str]] = {
    name: _keywords(_ROUTING_RULES["level_2"].get("output_terms", {}), name)
    for name in _ROUTING_RULES["level_2"].get("output_terms", {})
}
_DECISION_CONFIDENCE: Dict[str, Any] = _ROUTING_RULES["level_2"].get(
    "decision_confidence", {}
)

_RESET_KWS = _keywords(_LEVEL_1_GROUPS, "reset")
_FOLLOW_UP_KWS = _keywords(_LEVEL_1_GROUPS, "follow_up")
_SEQUENCE_KWS = _keywords(_LEVEL_1_GROUPS, "sequence")
_PARALLEL_KWS = _keywords(_LEVEL_1_GROUPS, "parallel")
_DEPENDENCY_KWS = _keywords(_LEVEL_1_GROUPS, "dependency")
_TARGET_FIELD_CANDIDATES = [
    str(value) for value in _ROUTING_RULES.get("target_field_candidates", [])
]
_TOP_N_PATTERN = re.compile(
    str(_ROUTING_RULES["level_2"].get("patterns", {}).get("top_n_regex", r"(?:前|top\s*)\d+")),
    re.IGNORECASE,
)
_NUMERIC_CONDITION_PATTERN = re.compile(
    str(
        _ROUTING_RULES["level_2"].get("patterns", {}).get(
            "numeric_condition_regex", r"[><≥≤]"
        )
    )
)
_DATE_RANGE_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}\s*至\s*\d{4}-\d{2}-\d{2}"
)


@dataclass
class OperationIntent:
    """Data operations required by one atomic query."""

    types: List[str] = field(default_factory=list)
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)
    source: str = "rules"
    confidence: float = 1.0

    def has(self, *names: str) -> bool:
        return any(name in self.types for name in names)


@dataclass
class OutputIntent:
    """Requested result formats, independent from the execution route."""

    formats: List[str] = field(default_factory=lambda: ["auto"])
    explicit: bool = False
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)
    source: str = "rules"
    confidence: float = 1.0

    @property
    def wants_chart(self) -> bool:
        return "chart" in self.formats

    @property
    def wants_table(self) -> bool:
        return bool({"table", "export_excel"}.intersection(self.formats))

    @property
    def wants_insight(self) -> bool:
        return "insight" in self.formats


@dataclass
class QueryStructure:
    requires_planning: bool = False
    needs_semantic_planning: bool = False
    classification: str = "simple"
    confidence: float = 1.0
    score: int = 0
    signals: List[str] = field(default_factory=list)


@dataclass
class RoutingResult:
    hint: RoutingHint
    mode: MultiTurnMode
    confidence: float
    reasoning: str
    is_compound: bool = False
    operation_intent: OperationIntent = field(default_factory=OperationIntent)
    output_intent: OutputIntent = field(default_factory=OutputIntent)
    normalized_query: Optional[NormalizedQuery] = None
    needs_new_computation: bool = True
    uses_previous_result: bool = False
    target_fields: List[str] = field(default_factory=list)
    structure: QueryStructure = field(default_factory=QueryStructure)


class RoutingClassificationSkill(Skill):
    """Keep normalization, intent extraction, and route choice behind one interface."""

    name = "routing_classification"
    description = "Detect query structure, operation intent, output intent, and execution route"
    LLM_THRESHOLD = float(_LEVEL_1_THRESHOLDS.get("llm_routing_confidence", 0.55))

    def __init__(
        self,
        router: Any,
        normalization_skill: Optional[QueryNormalizationSkill] = None,
    ) -> None:
        super().__init__(router)
        self.normalization_skill = normalization_skill or QueryNormalizationSkill(router)

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        atomic: bool = False,
        normalized_query: Optional[NormalizedQuery] = None,
        **kwargs: Any,
    ) -> RoutingResult:
        normalized = normalized_query or await self.normalization_skill.run(ctx, query)
        q = normalized.normalized_text
        mode = self._detect_multiturn_mode(q, ctx)
        operation_intent, output_intent = self._extract_intents(q)
        structure = (
            QueryStructure(classification="atomic", confidence=1.0)
            if atomic
            else self._classify_structure(q, operation_intent, output_intent)
        )
        hint, confidence, reasoning = self._decide_route(operation_intent)

        should_enrich = confidence < self.LLM_THRESHOLD and (
            atomic or not structure.needs_semantic_planning
        )
        if should_enrich:
            try:
                operation_intent, output_intent = await self._llm_extract_intents(
                    q, ctx, operation_intent, output_intent
                )
                hint, confidence, reasoning = self._decide_route(operation_intent)
            except Exception as exc:
                reasoning += f" (LLM intent fallback failed: {exc})"

        return RoutingResult(
            hint=hint,
            mode=mode,
            confidence=confidence,
            reasoning=reasoning,
            is_compound=structure.requires_planning,
            operation_intent=operation_intent,
            output_intent=output_intent,
            normalized_query=normalized,
            needs_new_computation=hint in (
                RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN
            ),
            uses_previous_result=mode == MultiTurnMode.FOLLOW_UP,
            target_fields=self._extract_target_fields(q),
            structure=structure,
        )

    def _detect_multiturn_mode(
        self, query: str, ctx: AnalysisContext
    ) -> MultiTurnMode:
        q_lower = query.lower()
        if ctx.active_result is not None and any(kw in q_lower for kw in _RESET_KWS):
            return MultiTurnMode.RESET
        if ctx.active_result is not None and any(kw in q_lower for kw in _FOLLOW_UP_KWS):
            return MultiTurnMode.FOLLOW_UP
        return MultiTurnMode.NEW_QUERY

    @staticmethod
    def _match_terms(query: str, groups: Dict[str, List[str]]) -> Dict[str, List[str]]:
        q_lower = query.lower()
        return {
            name: [term for term in terms if term in q_lower]
            for name, terms in groups.items()
            if any(term in q_lower for term in terms)
        }

    @classmethod
    def _extract_intents(cls, query: str) -> tuple[OperationIntent, OutputIntent]:
        """Extract operation and output meaning without choosing an engine."""
        # Collect raw semantic signals from configurable vocabularies.
        operation_matches = cls._match_terms(query, _OPERATION_TERMS)
        output_matches = cls._match_terms(query, _OUTPUT_TERMS)
        operations = [
            name for name in operation_matches if name != "detect_action"
        ]

        def add_operation(name: str, evidence: Optional[str] = None) -> None:
            if name not in operations:
                operations.append(name)
            if evidence:
                operation_matches.setdefault(name, []).append(evidence)

        if _TOP_N_PATTERN.search(query):
            add_operation("top_n", "regex:top_n")
        if _NUMERIC_CONDITION_PATTERN.search(query):
            add_operation("numeric_condition", "regex:numeric_condition")
        if _DATE_RANGE_PATTERN.search(query):
            add_operation("date_filter", "regex:date_range")

        # Derive intents from signal combinations and resolve ambiguity before routing.
        if "anomaly" in operation_matches and "detect_action" in operation_matches:
            add_operation("anomaly_detect")

        chart_mentioned = bool(
            {"chart_reference", "chart_type"}.intersection(output_matches)
        )
        explains = "explain" in operations
        references_existing = "reference_marker" in output_matches
        visualizes = "visualization_action" in output_matches
        chart_requested = False
        if chart_mentioned and (
            "chart_action" in output_matches or visualizes
        ):
            chart_requested = not (references_existing and explains)
        if "chart_type" in output_matches and not references_existing and not explains:
            chart_requested = True
        if visualizes and not explains:
            chart_requested = True
        if (
            "display_action" in output_matches
            and "trend" in operations
            and not explains
        ):
            chart_requested = True

        formats: List[str] = []

        def add_format(name: str) -> None:
            if name not in formats:
                formats.append(name)

        if chart_requested:
            add_format("chart")
            add_operation("chart_data_prep")
        if "table_reference" in output_matches:
            add_format("table")
        if (
            "export_excel" in output_matches
            and "export_action" in output_matches
        ):
            add_format("export_excel")
        if (
            "insight_output" in output_matches
            or (explains and not chart_requested)
        ):
            add_format("insight")

        if not operations:
            if formats and set(formats) <= {"table", "export_excel"}:
                operations.append("pass_through")
            else:
                operations.append("general")
        if not formats:
            formats.append("auto")

        return (
            OperationIntent(types=operations, matched_terms=operation_matches),
            OutputIntent(
                formats=formats,
                explicit=formats != ["auto"],
                matched_terms=output_matches,
            ),
        )

    @classmethod
    def _classify_structure(
        cls,
        query: str,
        operation_intent: Optional[OperationIntent] = None,
        output_intent: Optional[OutputIntent] = None,
    ) -> QueryStructure:
        if operation_intent is None or output_intent is None:
            operation_intent, output_intent = cls._extract_intents(query)
        q_lower = query.lower()
        boundary_pattern = str(
            _ROUTING_RULES["level_1"].get("patterns", {}).get(
                "clause_boundary_regex", r"[。！？!?；;]"
            )
        )
        parallel_pattern = "|".join(
            re.escape(keyword) for keyword in sorted(_PARALLEL_KWS, key=len, reverse=True)
        )
        split_pattern = (
            f"(?:{boundary_pattern})|(?:{parallel_pattern})"
            if parallel_pattern
            else boundary_pattern
        )
        parts = re.split(split_pattern, query)
        meaningful = [
            part.strip()
            for part in parts
            if len(part.strip()) >= 6 and cls._looks_independent_question(part)
        ]

        structural_signals: List[str] = []
        score = 0
        sequence_hits = [kw for kw in _SEQUENCE_KWS if kw in q_lower]
        dependency_hits = [kw for kw in _DEPENDENCY_KWS if kw in q_lower]
        parallel_hits = [kw for kw in _PARALLEL_KWS if kw in q_lower]
        if sequence_hits:
            score += 2
            structural_signals.append(f"sequence:{sequence_hits[0]}")
        if dependency_hits:
            score += 2
            structural_signals.append(f"dependency:{dependency_hits[0]}")
        if len(meaningful) >= 2:
            score += 2
            structural_signals.append(f"analytical_clauses:{len(meaningful)}")
        elif parallel_hits:
            score += 1
            structural_signals.append(f"parallel:{parallel_hits[0]}")

        threshold = int(_LEVEL_1_THRESHOLDS.get("planning_score", 2))
        if score >= threshold:
            return QueryStructure(
                requires_planning=True,
                needs_semantic_planning=True,
                classification="complex",
                confidence=0.95,
                score=score,
                signals=structural_signals,
            )

        operations = set(operation_intent.types)
        computational = {
            "aggregate", "top_n", "trend", "pivot", "compare", "chart_data_prep",
            "anomaly_detect", "complex_transform", "advanced_analysis", "numeric_condition",
        }
        mixed_compute_insight = bool(
            operations.intersection(computational)
            and (
                "explain" in operations
                or output_intent.wants_insight
            )
        )
        semantic_min_chars = int(
            _LEVEL_1_THRESHOLDS.get("semantic_planning_min_chars", 24)
        )
        ambiguous = (
            operations == {"general"}
            or "analysis_request" in operations
            or bool(parallel_hits)
            or mixed_compute_insight
            or (len(query) >= semantic_min_chars and len(operations) >= 2)
        )
        if ambiguous:
            reasons = list(structural_signals)
            if operations == {"general"}:
                reasons.append("no_clear_operation")
            if "analysis_request" in operations:
                reasons.append("generic_analysis_request")
            if len(query) >= semantic_min_chars:
                reasons.append("long_query")
            if mixed_compute_insight:
                reasons.append("mixed_compute_insight")
            return QueryStructure(
                requires_planning=False,
                needs_semantic_planning=True,
                classification="uncertain",
                confidence=0.50,
                score=score,
                signals=reasons,
            )

        return QueryStructure(
            classification="simple",
            confidence=0.95,
            score=score,
            signals=structural_signals,
        )

    @classmethod
    def _detect_compound(cls, query: str) -> bool:
        return cls._classify_structure(query).requires_planning

    @classmethod
    def _looks_independent_question(cls, part: str) -> bool:
        p = part.strip().lower()
        if not p:
            return False
        if any(p.startswith(prefix) for prefix in [
            "更直观", "直观", "方便", "便于", "用于", "用来", "看看", "看一下", "展示一下",
        ]):
            return False
        operation, output = cls._extract_intents(p)
        return operation.types != ["general"] or output.formats != ["auto"]

    @staticmethod
    def _decide_route(operation_intent: OperationIntent) -> tuple[RoutingHint, float, str]:
        operations = set(operation_intent.types)

        def confidence(name: str, default: float) -> float:
            return float(_DECISION_CONFIDENCE.get(name, default))

        computational = {
            "aggregate", "top_n", "trend", "pivot", "chart_data_prep", "compare",
            "anomaly_detect", "complex_transform", "advanced_analysis", "numeric_condition",
        }
        hard_computation = computational - {"compare"}
        extreme_complexity = {
            "top_n", "trend", "pivot", "chart_data_prep", "numeric_condition",
        }

        if "explain" in operations and not operations.intersection(hard_computation):
            return (
                RoutingHint.INSIGHT_ONLY,
                confidence("insight_only", 0.86),
                "Route decision: explanation without new computation",
            )
        if {"which", "extreme"} <= operations and not operations.intersection(extreme_complexity):
            return (
                RoutingHint.RULE_ENGINE,
                confidence("rule_engine", 0.88),
                "Route decision: deterministic which + extreme",
            )
        hits = sorted(operations.intersection(computational))
        if hits:
            return (
                RoutingHint.CODE_GEN,
                confidence("code_gen", 0.86),
                f"Route decision: computation operations {hits}",
            )
        deterministic = operations.intersection({"filter", "date_filter", "sort", "pass_through"})
        if deterministic:
            return (
                RoutingHint.RULE_ENGINE,
                confidence("rule_engine", 0.88),
                f"Route decision: deterministic operations {sorted(deterministic)}",
            )
        if operations.intersection({"analysis_request", "anomaly"}):
            return (
                RoutingHint.INSIGHT_ONLY,
                confidence("analysis_request", 0.50),
                "Route decision: generic analysis requires semantic clarification",
            )
        if "vague" in operations:
            return (
                RoutingHint.RULE_ENGINE,
                confidence("vague", 0.70),
                "Route decision: vague pass-through query",
            )
        return (
            RoutingHint.CODE_GEN,
            confidence("unknown", 0.45),
            "Route decision: no clear operation; provisional CODE_GEN",
        )

    async def _llm_extract_intents(
        self,
        query: str,
        ctx: AnalysisContext,
        rule_operation: OperationIntent,
        rule_output: OutputIntent,
    ) -> tuple[OperationIntent, OutputIntent]:
        provider = self.router.get_provider(ModelRole.ROUTING)
        operation_names = sorted(
            (set(_OPERATION_TERMS) - {"detect_action"})
            | {"anomaly_detect", "chart_data_prep", "pass_through"}
        )
        output_names = ["auto", "table", "chart", "insight", "export_excel"]
        system = (
            "你是数据分析意图抽取器，不选择执行引擎。"
            "operation_intents描述对数据做什么，output_intents描述结果如何呈现。"
            "只输出JSON："
            '{"operation_intents":["..."],"output_intents":["..."],'
            '"confidence":0.0,"reasoning":"一句话"}。'
            f"允许的operation_intents：{operation_names}。"
            f"允许的output_intents：{output_names}。"
            "没有明确输出要求时使用auto；不要输出rule、code、insight路由。"
        )
        user_content = (
            f"用户查询：{query}\n"
            f"规则operation：{rule_operation.types}\n"
            f"规则output：{rule_output.formats}"
        )
        conv_text = ctx.conversation_text(max_turns=4)
        if conv_text:
            user_content += f"\n最近对话：\n{conv_text}"
        response = await provider.complete(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            max_tokens=320,
            temperature=0.0,
            json_mode=True,
        )
        data = json.loads(response)
        raw_operations = data.get("operation_intents", [])
        raw_outputs = data.get("output_intents", [])
        if not isinstance(raw_operations, list) or not isinstance(raw_outputs, list):
            raise ValueError("intent extractor returned invalid intent lists")

        operations = list(rule_operation.types)
        for name in raw_operations:
            value = str(name)
            if value in operation_names and value not in operations:
                operations.append(value)
        outputs = [name for name in rule_output.formats if name != "auto"]
        for name in raw_outputs:
            value = str(name)
            if value in output_names and value != "auto" and value not in outputs:
                outputs.append(value)
        if "chart" in outputs and "chart_data_prep" not in operations:
            operations.append("chart_data_prep")
        if not outputs:
            outputs = ["auto"]
        score = min(max(float(data.get("confidence", 0.75)), 0.0), 1.0)
        return (
            OperationIntent(
                types=operations,
                matched_terms=dict(rule_operation.matched_terms),
                source="hybrid",
                confidence=score,
            ),
            OutputIntent(
                formats=outputs,
                explicit=outputs != ["auto"],
                matched_terms=dict(rule_output.matched_terms),
                source="hybrid",
                confidence=score,
            ),
        )

    @staticmethod
    def _extract_target_fields(query: str) -> List[str]:
        return [field for field in _TARGET_FIELD_CANDIDATES if field.lower() in query.lower()]
