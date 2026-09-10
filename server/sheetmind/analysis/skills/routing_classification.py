"""Classify query structure, intent signals, and execution route."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context import AnalysisContext, MultiTurnMode, RoutingHint
from ..models.configs import ModelRole
from .base import Skill


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
_SIGNAL_TERMS: Dict[str, List[str]] = {
    name: _keywords(_ROUTING_RULES["level_2"].get("signal_terms", {}), name)
    for name in _ROUTING_RULES["level_2"].get("signal_terms", {})
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


@dataclass
class IntentSignals:
    """Meaning extracted from an atomic query before route selection."""

    active: List[str] = field(default_factory=list)
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)
    source: str = "rules"
    confidence: float = 1.0

    def has(self, *names: str) -> bool:
        return any(name in self.active for name in names)


@dataclass
class IntentFacets:
    """Product-facing intent labels derived from IntentSignals."""

    operation_types: List[str] = field(default_factory=list)
    needs_new_computation: bool = True
    wants_chart: bool = False
    uses_previous_result: bool = False
    target_fields: List[str] = field(default_factory=list)


@dataclass
class QueryStructure:
    """Structure decision made before dependency-aware planning."""

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
    intent_signals: IntentSignals = field(default_factory=IntentSignals)
    facets: IntentFacets = field(default_factory=IntentFacets)
    structure: QueryStructure = field(default_factory=QueryStructure)


class RoutingClassificationSkill(Skill):
    """
    Expose one stable routing interface while keeping three decisions separate:
    structure detection, intent-signal extraction, and route decision.
    """

    name = "routing_classification"
    description = "Detect query structure and route atomic intent signals"
    LLM_THRESHOLD = float(_LEVEL_1_THRESHOLDS.get("llm_routing_confidence", 0.55))

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        atomic: bool = False,
        **kwargs: Any,
    ) -> RoutingResult:
        q = query.strip()
        mode = self._detect_multiturn_mode(q, ctx)
        intent_signals = self._extract_intent_signals(q)
        structure = (
            QueryStructure(classification="atomic", confidence=1.0)
            if atomic
            else self._classify_structure(q, intent_signals)
        )
        hint, confidence, reasoning = self._decide_route(intent_signals)

        # Top-level ambiguous structure is handled by QueryPlanningSkill in a
        # single semantic call. Atomic or clearly-simple queries may use the
        # lightweight signal extractor when local signals are insufficient.
        should_enrich_signals = confidence < self.LLM_THRESHOLD and (
            atomic or not structure.needs_semantic_planning
        )
        if should_enrich_signals:
            try:
                intent_signals = await self._llm_extract_signals(q, ctx, intent_signals)
                hint, confidence, reasoning = self._decide_route(intent_signals)
            except Exception as exc:
                reasoning += f" (LLM signal fallback failed: {exc})"

        facets = self._build_facets(q, hint, mode, intent_signals)
        return RoutingResult(
            hint=hint,
            mode=mode,
            confidence=confidence,
            reasoning=reasoning,
            is_compound=structure.requires_planning,
            intent_signals=intent_signals,
            facets=facets,
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
    def _extract_intent_signals(query: str) -> IntentSignals:
        """Level 2A: extract meanings only; no signal names an engine."""
        q_lower = query.lower()
        matched: Dict[str, List[str]] = {}
        for name, terms in _SIGNAL_TERMS.items():
            hits = [term for term in terms if term in q_lower]
            if hits:
                matched[name] = hits

        active = list(matched)

        def add(name: str, evidence: Optional[str] = None) -> None:
            if name not in active:
                active.append(name)
            if evidence:
                matched.setdefault(name, []).append(evidence)

        if _TOP_N_PATTERN.search(query):
            add("top_n", "regex:top_n")
        if _NUMERIC_CONDITION_PATTERN.search(query):
            add("numeric_condition", "regex:numeric_condition")

        # Chart nouns are references. Creation requires an action; explanation
        # requires explanatory language. This prevents "这个图说明什么" from
        # being treated as a request to generate a new chart.
        chart_mentioned = bool({"chart_reference", "chart_type"}.intersection(active))
        if chart_mentioned and "chart_action" in active:
            add("chart_create")
        if (
            "chart_type" in active
            and "reference_marker" not in active
            and "explain" not in active
            and "anomaly" not in active
        ):
            add("chart_create")
        if chart_mentioned and (
            "explain" in active or "anomaly" in active
        ):
            add("chart_explain")
        if "anomaly" in active and "detect_action" in active:
            add("anomaly_detect")

        return IntentSignals(active=active, matched_terms=matched)

    @staticmethod
    def _classify_structure(
        query: str,
        intent_signals: Optional[IntentSignals] = None,
    ) -> QueryStructure:
        """
        Level 1: rules decide only high-confidence simple/complex cases.
        Ambiguous shape is marked for semantic planning instead of guessed.
        """
        intent_signals = intent_signals or RoutingClassificationSkill._extract_intent_signals(query)
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
            if len(part.strip()) >= 6
            and RoutingClassificationSkill._looks_independent_question(part)
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

        planning_threshold = int(_LEVEL_1_THRESHOLDS.get("planning_score", 2))
        if score >= planning_threshold:
            return QueryStructure(
                requires_planning=True,
                needs_semantic_planning=True,
                classification="complex",
                confidence=0.95,
                score=score,
                signals=structural_signals,
            )

        semantic_min_chars = int(
            _LEVEL_1_THRESHOLDS.get("semantic_planning_min_chars", 32)
        )
        semantic_signals = {
            "filter", "sort", "aggregate", "top_n", "trend", "pivot",
            "compare", "anomaly", "chart_create", "chart_explain", "explain",
            "anomaly_detect", "complex_transform", "advanced_analysis",
            "numeric_condition", "which", "extreme", "vague",
        }
        active_semantics = semantic_signals.intersection(intent_signals.active)
        computation_semantics = {
            "aggregate", "top_n", "trend", "pivot", "compare", "chart_create",
            "anomaly_detect", "complex_transform", "advanced_analysis", "numeric_condition",
        }
        mixed_compute_explain = bool(
            active_semantics.intersection(computation_semantics)
            and active_semantics.intersection({"explain", "chart_explain"})
        )
        ambiguous = (
            not active_semantics
            or bool(parallel_hits)
            or mixed_compute_explain
            or (
                len(query) >= semantic_min_chars
                and len(active_semantics) >= 2
            )
        )
        if ambiguous:
            reasons = list(structural_signals)
            if not active_semantics:
                reasons.append("no_clear_intent")
            if len(query) >= semantic_min_chars:
                reasons.append("long_query")
            if mixed_compute_explain:
                reasons.append("mixed_compute_explain")
            return QueryStructure(
                requires_planning=False,
                needs_semantic_planning=True,
                classification="uncertain",
                confidence=0.50,
                score=score,
                signals=reasons,
            )

        return QueryStructure(
            requires_planning=False,
            needs_semantic_planning=False,
            classification="simple",
            confidence=0.95,
            score=score,
            signals=structural_signals,
        )

    @staticmethod
    def _detect_compound(query: str) -> bool:
        return RoutingClassificationSkill._classify_structure(query).requires_planning

    @staticmethod
    def _looks_independent_question(part: str) -> bool:
        p = part.strip().lower()
        if not p:
            return False
        modifier_prefixes = [
            "更直观", "直观", "方便", "便于", "用于", "用来", "看看", "看一下", "展示一下",
        ]
        if any(p.startswith(prefix) for prefix in modifier_prefixes):
            return False
        signals = RoutingClassificationSkill._extract_intent_signals(p)
        return bool(
            set(signals.active)
            - {
                "vague", "date_filter", "chart_reference", "chart_type",
                "chart_action", "reference_marker", "detect_action",
            }
        )

    @staticmethod
    def _decide_route(intent_signals: IntentSignals) -> tuple[RoutingHint, float, str]:
        """Level 2B: apply stable combinations to extracted meanings."""
        active = set(intent_signals.active)

        def confidence(name: str, default: float) -> float:
            return float(_DECISION_CONFIDENCE.get(name, default))

        computational = {
            "aggregate", "top_n", "trend", "pivot", "chart_create", "compare",
            "anomaly_detect", "complex_transform", "advanced_analysis", "numeric_condition",
        }
        extreme_complexity = {"top_n", "trend", "pivot", "chart_create", "numeric_condition"}
        hard_computation = computational - {"compare"}

        if "chart_explain" in active and not active.intersection(computational):
            return (
                RoutingHint.INSIGHT_ONLY,
                confidence("insight_only", 0.86),
                "Route decision: chart_explain without new computation",
            )

        if "explain" in active and not active.intersection(hard_computation):
            return (
                RoutingHint.INSIGHT_ONLY,
                confidence("insight_only", 0.86),
                "Route decision: explanation without new computation",
            )

        if {"which", "extreme"} <= active and not active.intersection(extreme_complexity):
            return (
                RoutingHint.RULE_ENGINE,
                confidence("rule_engine", 0.88),
                "Route decision: deterministic which + extreme",
            )

        computation_hits = sorted(active.intersection(computational))
        if computation_hits:
            return (
                RoutingHint.CODE_GEN,
                confidence("code_gen", 0.86),
                f"Route decision: computation signals {computation_hits}",
            )

        if active.intersection({"filter", "date_filter", "sort"}):
            hits = sorted(active.intersection({"filter", "date_filter", "sort"}))
            return (
                RoutingHint.RULE_ENGINE,
                confidence("rule_engine", 0.88),
                f"Route decision: deterministic signals {hits}",
            )

        if active.intersection({"explain", "anomaly"}):
            hits = sorted(active.intersection({"explain", "anomaly"}))
            return (
                RoutingHint.INSIGHT_ONLY,
                confidence("insight_only", 0.86),
                f"Route decision: insight signals {hits} without new computation",
            )

        if "vague" in active:
            return (
                RoutingHint.RULE_ENGINE,
                confidence("vague", 0.70),
                "Route decision: vague pass-through query",
            )

        return (
            RoutingHint.CODE_GEN,
            confidence("unknown", 0.45),
            "Route decision: no clear signal; provisional CODE_GEN",
        )

    async def _llm_extract_signals(
        self,
        query: str,
        ctx: AnalysisContext,
        rule_signals: IntentSignals,
    ) -> IntentSignals:
        """Use the model to add semantic signals; route selection stays local."""
        provider = self.router.get_provider(ModelRole.ROUTING)
        allowed = sorted(
            set(_SIGNAL_TERMS)
            | {"anomaly_detect", "chart_create", "chart_explain", "numeric_condition", "top_n"}
        )
        system = (
            "你是数据分析意图信号抽取器，不选择执行引擎。"
            "从允许的信号中选出用户明确需要的信号，只输出JSON："
            '{"signals":["..."],"confidence":0.0,"reasoning":"一句话"}。'
            f"允许的信号：{allowed}。"
            "chart_reference只是提到已有图；chart_create是创建新图；chart_explain是解释已有图。"
        )
        user_content = f"用户查询：{query}\n规则已发现：{rule_signals.active}"
        conv_text = ctx.conversation_text(max_turns=4)
        if conv_text:
            user_content += f"\n最近对话：\n{conv_text}"

        response = await provider.complete(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            max_tokens=256,
            temperature=0.0,
            json_mode=True,
        )
        data = json.loads(response)
        raw_signals = data.get("signals", [])
        if not isinstance(raw_signals, list):
            raise ValueError("signal extractor returned a non-list signals field")
        valid = [str(name) for name in raw_signals if str(name) in allowed]
        active = list(rule_signals.active)
        for name in valid:
            if name not in active:
                active.append(name)
        return IntentSignals(
            active=active,
            matched_terms=dict(rule_signals.matched_terms),
            source="hybrid",
            confidence=min(max(float(data.get("confidence", 0.75)), 0.0), 1.0),
        )

    def _build_facets(
        self,
        query: str,
        hint: RoutingHint,
        mode: MultiTurnMode,
        intent_signals: IntentSignals,
    ) -> IntentFacets:
        active = set(intent_signals.active)
        operation_types: List[str] = []
        operation_map = [
            ("filter", {"filter", "date_filter", "numeric_condition"}),
            ("sort", {"sort"}),
            ("aggregate", {"aggregate", "top_n", "extreme"}),
            ("trend", {"trend"}),
            ("pivot", {"pivot"}),
            ("compare", {"compare"}),
            ("anomaly", {"anomaly", "anomaly_detect"}),
            ("explain", {"explain", "chart_explain"}),
            ("transform", {"complex_transform"}),
            ("advanced_analysis", {"advanced_analysis"}),
            ("chart", {"chart_create"}),
        ]
        for operation, source_signals in operation_map:
            if active.intersection(source_signals):
                operation_types.append(operation)
        if not operation_types:
            operation_types.append("general")

        return IntentFacets(
            operation_types=operation_types,
            needs_new_computation=hint in (RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN),
            wants_chart="chart_create" in active,
            uses_previous_result=mode == MultiTurnMode.FOLLOW_UP,
            target_fields=self._extract_target_fields(query),
        )

    @staticmethod
    def _extract_target_fields(query: str) -> List[str]:
        return [field for field in _TARGET_FIELD_CANDIDATES if field.lower() in query.lower()]
