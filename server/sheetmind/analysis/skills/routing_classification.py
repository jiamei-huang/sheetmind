"""
SheetMind — Routing Classification Skill
============================================
Returns RoutingHint (rule / code / text) + MultiTurnMode (new / follow_up / reset).

Design:
- Rule-based keyword matching is the primary path (cheap, instant).
- Keyword banks live in analysis/config/routing_rules.json so routing language
  can be tuned without editing this module.
- LLM fallback only when confidence < 0.55 (rare ambiguous cases).
- Multi-turn mode is determined first — it takes priority and can influence routing.
- Secondary intent facets are returned beside the 3-way execution route.

Routing rules:
- CODE_GEN signals override ordinary RULE_ENGINE signals.
- INSIGHT_ONLY wins for chart-reference explanation queries such as
  "这个图表说明什么".
- Low-confidence matches fall back to the routing LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context import AnalysisContext, MultiTurnMode, RoutingHint
from ..models.configs import ModelRole
from .base import Skill


# ---------------------------------------------------------------------------
# Routing rule config
# ---------------------------------------------------------------------------

_ROUTING_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "routing_rules.json"


def _load_routing_rules() -> Dict[str, Any]:
    with _ROUTING_RULES_PATH.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    groups = data.get("keyword_groups")
    if not isinstance(groups, dict):
        raise ValueError(f"routing rules must contain keyword_groups: {_ROUTING_RULES_PATH}")
    return data


_ROUTING_RULES = _load_routing_rules()
_KEYWORD_GROUPS: Dict[str, Any] = _ROUTING_RULES["keyword_groups"]


def _keywords(group_name: str) -> List[str]:
    values = _KEYWORD_GROUPS.get(group_name, [])
    if not isinstance(values, list):
        raise ValueError(f"routing keyword group must be a list: {group_name}")
    return [str(value) for value in values]


_RESET_KWS = _keywords("reset")
_FOLLOW_UP_KWS = _keywords("follow_up")
_CODE_GEN_KWS = _keywords("code_gen")
_RULE_ONLY_KWS = _keywords("rule_only")
_INSIGHT_ONLY_KWS = _keywords("insight_only")
_VAGUE_KWS = _keywords("vague")
_DETERMINISTIC_EXTREME_KWS = _keywords("deterministic_extreme")
_DETERMINISTIC_EXTREME_SUBJECT_KWS = _keywords("deterministic_extreme_subject")
_COMPLEX_OVERRIDE_KWS = _keywords("complex_override")
_FILTER_TRIGGER_KWS = _keywords("filter_trigger")
_SORT_TRIGGER_KWS = _keywords("sort_trigger")
_AGGREGATE_KWS = _keywords("aggregate")
_TREND_KWS = _keywords("trend")
_COMPARE_KWS = _keywords("compare")
_ANOMALY_KWS = _keywords("anomaly")
_CHART_KWS = _keywords("chart")
_CHART_REF_ONLY_KWS = frozenset(_keywords("chart_reference_only"))
_TARGET_FIELD_CANDIDATES = [
    str(value) for value in _ROUTING_RULES.get("target_field_candidates", [])
]
_CODE_GEN_OPS = re.compile(
    str(_ROUTING_RULES.get("operators", {}).get("code_gen_regex", r"[><≥≤]"))
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IntentFacets:
    """Secondary product intent labels that sit beside the execution route."""

    operation_types: List[str] = field(default_factory=list)
    needs_new_computation: bool = True
    wants_chart: bool = False
    uses_previous_result: bool = False
    target_fields: List[str] = field(default_factory=list)


@dataclass
class RoutingResult:
    hint: RoutingHint
    mode: MultiTurnMode
    confidence: float
    reasoning: str
    is_compound: bool = False   # True when query contains multiple independent questions
    facets: IntentFacets = field(default_factory=IntentFacets)


# ---------------------------------------------------------------------------
# Skill implementation
# ---------------------------------------------------------------------------

class RoutingClassificationSkill(Skill):
    """
    Classify a query into a RoutingHint and MultiTurnMode.
    Uses rule-based detection first; LLM fallback for low-confidence cases.
    """

    name = "routing_classification"
    description = "Classify query into RoutingHint (rule/code/insight) + MultiTurnMode (new/follow_up/reset)"

    # Confidence threshold below which we call the LLM
    LLM_THRESHOLD = 0.55

    # ---------------------------------------------------------------------------
    # Public
    # ---------------------------------------------------------------------------

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        **kwargs: Any,
    ) -> RoutingResult:
        q = query.strip()

        # Step 1: determine multi-turn mode
        mode = self._detect_multiturn_mode(q, ctx)

        # Step 2: rule-based routing hint
        hint, confidence, reasoning = self._rule_classify(q)

        # Step 3: LLM fallback if ambiguous
        if confidence < self.LLM_THRESHOLD:
            try:
                hint, reasoning = await self._llm_classify(q, ctx, hint)
                confidence = 0.80
            except Exception as exc:
                # Don't fail the whole request on routing LLM error — fall back to rule result
                reasoning += f" (LLM fallback failed: {exc})"

        # Step 4: detect compound / multi-part queries
        is_compound = self._detect_compound(q)
        facets = self._build_facets(q, hint, mode)

        return RoutingResult(
            hint=hint,
            mode=mode,
            confidence=confidence,
            reasoning=reasoning,
            is_compound=is_compound,
            facets=facets,
        )

    # ---------------------------------------------------------------------------
    # Multi-turn mode detection
    # ---------------------------------------------------------------------------

    def _detect_multiturn_mode(
        self, query: str, ctx: AnalysisContext
    ) -> MultiTurnMode:
        q_lower = query.lower()

        # RESET: explicit restart — only meaningful when there is an active result to reset
        if ctx.active_result is not None and any(kw in q_lower for kw in _RESET_KWS):
            return MultiTurnMode.RESET

        # FOLLOW_UP: explicit reference-to-previous signal AND there is an active result.
        # NOTE: We intentionally do NOT use query length as a signal — Chinese queries
        # are naturally short, so a 15-char query like "按地区汇总销售额" is a new question,
        # not a follow-up just because it's brief.  Only explicit keywords count.
        if ctx.active_result is not None:
            if any(kw in q_lower for kw in _FOLLOW_UP_KWS):
                return MultiTurnMode.FOLLOW_UP

        return MultiTurnMode.NEW_QUERY

    # ---------------------------------------------------------------------------
    # Compound query detection
    # ---------------------------------------------------------------------------

    @staticmethod
    def _detect_compound(query: str) -> bool:
        """
        Return True when the query contains two or more independent analytical
        questions that each need separate computation.

        Heuristic: split on Chinese sentence terminators (。？) and connector
        phrases; if ≥ 2 segments are non-trivial (≥ 6 chars) the query is
        treated as compound so CodeGenerationSkill can produce a combined result.
        """
        import re

        # Split on sentence boundaries and common compound connectors
        parts = re.split(r'[。？?]|另外|同时|还有|以及|并且|此外', query)
        meaningful = [
            p.strip()
            for p in parts
            if len(p.strip()) >= 6 and RoutingClassificationSkill._looks_independent_question(p)
        ]
        return len(meaningful) >= 2

    @staticmethod
    def _looks_independent_question(part: str) -> bool:
        """Filter out follow-up modifiers such as "更直观看尾程花费"."""
        p = part.strip().lower()
        if not p:
            return False

        modifier_prefixes = [
            "更直观", "直观", "方便", "便于", "用于", "用来", "看看",
            "看一下", "展示一下",
        ]
        if any(p.startswith(prefix) for prefix in modifier_prefixes):
            return False

        action_kws = (
            _CODE_GEN_KWS
            + _RULE_ONLY_KWS
            + _INSIGHT_ONLY_KWS
            + _DETERMINISTIC_EXTREME_SUBJECT_KWS
        )
        return any(kw in p for kw in action_kws)

    # ---------------------------------------------------------------------------
    # Rule-based classification
    # ---------------------------------------------------------------------------

    def _rule_classify(self, query: str) -> tuple:
        """Returns (RoutingHint, confidence, reasoning)."""
        q_lower = query.lower()

        # Check INSIGHT_ONLY signals BEFORE CODE_GEN when all CODE_GEN hits are
        # chart-reference words only (no aggregation / calculation signals).
        # This prevents "这个图表说明什么" from being misclassified as CODE_GEN.
        insight_hits = [kw for kw in _INSIGHT_ONLY_KWS if kw in q_lower]
        if insight_hits:
            code_candidates = [kw for kw in _CODE_GEN_KWS if kw in q_lower]
            non_chart_code = [kw for kw in code_candidates if kw not in _CHART_REF_ONLY_KWS]
            if not non_chart_code:
                # INSIGHT_ONLY wins: no real computation signals, just chart-ref words
                return (
                    RoutingHint.INSIGHT_ONLY,
                    0.85,
                    f"INSIGHT_ONLY signals: {insight_hits[:3]} (no agg/calc CODE_GEN signals)",
                )

        # Deterministic "which category costs/sells the most?" questions are
        # safer in the rule engine than in free-form pandas code generation.
        if (
            any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_SUBJECT_KWS)
            and any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_KWS)
            and not any(kw in q_lower for kw in _COMPLEX_OVERRIDE_KWS)
        ):
            return (
                RoutingHint.RULE_ENGINE,
                0.86,
                "RULE_ENGINE deterministic aggregate-extreme question",
            )

        # Check CODE_GEN signals (they take priority over RULE_ENGINE)
        code_hits = [kw for kw in _CODE_GEN_KWS if kw in q_lower]
        has_op = bool(_CODE_GEN_OPS.search(query))
        if code_hits or has_op:
            matched = code_hits[:3]  # keep first 3 for reasoning
            conf = 0.90 if len(code_hits) >= 2 else 0.82
            return (
                RoutingHint.CODE_GEN,
                conf,
                f"CODE_GEN signals: {matched}" + (" + numeric operator" if has_op else ""),
            )

        # Check INSIGHT_ONLY signals (open-ended, no data op) — second pass for pure insight queries
        insight_hits = [kw for kw in _INSIGHT_ONLY_KWS if kw in q_lower]
        rule_hits = [kw for kw in _RULE_ONLY_KWS if kw in q_lower]
        if insight_hits and not rule_hits:
            return (
                RoutingHint.INSIGHT_ONLY,
                0.85,
                f"INSIGHT_ONLY signals: {insight_hits[:3]}",
            )

        # Check RULE_ENGINE signals
        if rule_hits:
            return (
                RoutingHint.RULE_ENGINE,
                0.88,
                f"RULE_ENGINE signals: {rule_hits[:3]}",
            )

        # Vague / pass-through queries (e.g. "看看数据") → rule engine pass-through
        vague_hits = [kw for kw in _VAGUE_KWS if kw in q_lower]
        if vague_hits and len(query) < 15:
            return (
                RoutingHint.RULE_ENGINE,
                0.70,
                f"Vague query (pass-through): {vague_hits}",
            )

        # Low-confidence fallback — default to CODE_GEN (more flexible)
        return (
            RoutingHint.CODE_GEN,
            0.45,
            "No clear signal; defaulting to CODE_GEN",
        )

    # ---------------------------------------------------------------------------
    # LLM fallback
    # ---------------------------------------------------------------------------

    async def _llm_classify(
        self,
        query: str,
        ctx: AnalysisContext,
        rule_hint: RoutingHint,
    ) -> tuple:
        """Call the LLM for ambiguous routing.  Returns (RoutingHint, reasoning)."""
        provider = self.router.get_provider(ModelRole.ROUTING)

        conv_text = ctx.conversation_text(max_turns=4)
        system = (
            "你是 RoutingClassifier，专门判断数据分析查询的执行路径。\n\n"
            "只输出 JSON，格式：{\"routing\": \"rule\"|\"code\"|\"insight\", \"reasoning\": \"一句话说明\"}\n\n"
            "routing 含义：\n"
            "  rule  — 简单筛选/排序，无需计算，规则引擎可直接执行\n"
            "  code  — 需要聚合/计算/图表/复杂条件，LLM生成pandas代码执行\n"
            "  insight — 不产生新的结构化计算结果，基于已有结果或数据概况写洞察\n\n"
            "注意：判断执行路径，不判断输出格式。"
        )

        user_content = (
            f"用户查询：{query}\n"
            f"规则引擎预测：{rule_hint.value}\n"
        )
        if conv_text:
            user_content += f"\n对话历史（最近几轮）：\n{conv_text}"

        response = await provider.complete(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            max_tokens=128,
            temperature=0.0,
            json_mode=True,
        )

        try:
            data = json.loads(response)
            hint_str = data.get("routing", rule_hint.value)
            reasoning = data.get("reasoning", "LLM routing")
            if hint_str == "text":
                hint_str = RoutingHint.INSIGHT_ONLY.value
            return RoutingHint(hint_str), reasoning
        except Exception:
            return rule_hint, f"LLM parse failed; kept rule result: {response[:80]}"

    # ---------------------------------------------------------------------------
    # Secondary intent facets
    # ---------------------------------------------------------------------------

    def _build_facets(
        self,
        query: str,
        hint: RoutingHint,
        mode: MultiTurnMode,
    ) -> IntentFacets:
        q_lower = query.lower()
        operation_types: List[str] = []

        def add(name: str) -> None:
            if name not in operation_types:
                operation_types.append(name)

        if any(kw in q_lower for kw in _FILTER_TRIGGER_KWS):
            add("filter")
        if any(kw in q_lower for kw in _SORT_TRIGGER_KWS):
            add("sort")
        if (
            any(kw in q_lower for kw in _AGGREGATE_KWS)
            or (
                any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_SUBJECT_KWS)
                and any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_KWS)
            )
        ):
            add("aggregate")
        if any(kw in q_lower for kw in _TREND_KWS):
            add("trend")
        if any(kw in q_lower for kw in _COMPARE_KWS):
            add("compare")
        if any(kw in q_lower for kw in _ANOMALY_KWS):
            add("anomaly")
        if any(kw in q_lower for kw in _INSIGHT_ONLY_KWS):
            add("explain")

        wants_chart = any(kw in q_lower for kw in _CHART_KWS)
        if wants_chart:
            add("chart")

        uses_previous = mode == MultiTurnMode.FOLLOW_UP
        needs_new_computation = hint in (RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN)

        if not operation_types:
            add("general")

        return IntentFacets(
            operation_types=operation_types,
            needs_new_computation=needs_new_computation,
            wants_chart=wants_chart,
            uses_previous_result=uses_previous,
            target_fields=self._extract_target_fields(query),
        )

    @staticmethod
    def _extract_target_fields(query: str) -> List[str]:
        """Best-effort field mentions for trace/debug use before semantic typing."""
        return [field for field in _TARGET_FIELD_CANDIDATES if field.lower() in query.lower()]
