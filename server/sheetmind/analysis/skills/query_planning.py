"""Dependency-aware planning for multi-operation analysis queries."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..context import AnalysisContext, ExecutionStep, MultiTurnMode, RoutingHint
from ..models.configs import ModelRole
from .base import Skill
from .field_resolution import query_qualifiers
from .routing_classification import (
    RoutingClassificationSkill,
    RoutingResult,
    _LEVEL_1_THRESHOLDS,
    _PARALLEL_KWS,
    _SEQUENCE_KWS,
)

logger = logging.getLogger(__name__)

_MAX_PLAN_STEPS = int(_LEVEL_1_THRESHOLDS.get("max_plan_steps", 6))


class QueryPlan(BaseModel):
    """Validated execution plan returned by QueryPlanningSkill."""

    steps: List[ExecutionStep] = Field(default_factory=list)
    is_multi_step: bool = False
    source: Literal["single", "llm", "rule_fallback"] = "single"
    confidence: float = 0.0
    reasoning: str = ""


class QueryPlanningSkill(Skill):
    """
    Turn a query into a small, dependency-ordered list of atomic operations.

    High-confidence simple queries do not call a model. Complex or structurally
    ambiguous queries use one semantic planning call that may confirm one step
    or return a dependency-aware decomposition. Invalid output falls back to a
    local deterministic splitter.
    """

    name = "query_planning"
    description = "Decompose complex queries into validated dependency-ordered execution steps"

    def __init__(
        self,
        router: Any,
        routing_skill: Optional[RoutingClassificationSkill] = None,
    ) -> None:
        super().__init__(router)
        self.routing_skill = routing_skill or RoutingClassificationSkill(router)

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        routing: Optional[RoutingResult] = None,
        **kwargs: Any,
    ) -> QueryPlan:
        routing = routing or await self.routing_skill.run(ctx, query)
        if not routing.structure.needs_semantic_planning:
            return QueryPlan(
                steps=[self._single_step(query, routing)],
                is_multi_step=False,
                source="single",
                confidence=routing.confidence,
                reasoning=routing.reasoning,
            )

        raw_steps: List[Dict[str, Any]]
        source: Literal["llm", "rule_fallback"]
        confidence = 0.65
        reasoning = "Structure detection requested semantic planning."
        try:
            raw_steps, confidence, reasoning = await self._llm_decompose(ctx, query)
            source = "llm"
        except Exception as exc:
            logger.warning("[QueryPlanning] planner fallback: %s", exc)
            raw_steps = self._rule_decompose(query)
            source = "rule_fallback"
            reasoning = f"Deterministic fallback after planner error: {exc}"

        steps = await self._route_steps(ctx, raw_steps, routing.mode)
        return QueryPlan(
            steps=steps,
            is_multi_step=len(steps) > 1,
            source=source,
            confidence=min(max(confidence, 0.0), 1.0),
            reasoning=reasoning,
        )

    @staticmethod
    def _single_step(query: str, routing: RoutingResult) -> ExecutionStep:
        return ExecutionStep(
            step_id="s1",
            query=query,
            normalized_query=(
                routing.normalized_query.normalized_text
                if routing.normalized_query is not None
                else query
            ),
            route=routing.hint,
            depends_on=[],
            input_source=(
                "previous_result"
                if routing.mode == MultiTurnMode.FOLLOW_UP
                else "source"
            ),
            operation_intents=routing.operation_intent.types,
            output_intents=routing.output_intent.formats,
            output_explicit=routing.output_intent.explicit,
            needs_new_computation=routing.hint in (
                RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN
            ),
            target_fields=routing.target_fields,
            confidence=routing.confidence,
        )

    async def _llm_decompose(
        self,
        ctx: AnalysisContext,
        query: str,
    ) -> tuple[List[Dict[str, Any]], float, str]:
        provider = self.router.get_provider(ModelRole.QUERY_PLANNING)
        system = (
            "你是数据分析结构识别与执行规划器。先判断查询是一个问题还是多个问题。\n"
            "单一问题原样返回1个步骤；多个问题拆成最多6个原子步骤，并标出前置依赖。\n"
            "只负责结构、拆分和依赖，不选择执行引擎，不生成代码。\n"
            "每一步必须可单独执行；筛选、汇总、Top-N、比较、图表、解释应按用户语义排序。\n"
            "如果后一步基于前一步结果，depends_on 必须引用一个前序步骤ID；每步最多一个依赖。"
            "并行问题不添加依赖。\n"
            "图表或原因分析可以依赖计算步骤，但不应改写前一步的计算要求。\n"
            "只输出JSON："
            '{"steps":[{"id":"s1","query":"...","depends_on":[]}],'
            '"reasoning":"...","confidence":0.0}'
        )
        user_content = f"用户查询：{query}"
        conv_text = ctx.conversation_text(max_turns=2)
        if conv_text:
            user_content += f"\n最近对话：\n{conv_text}"

        response = await provider.complete(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            max_tokens=1024,
            temperature=0.0,
            json_mode=True,
        )
        data = self._parse_json(response)
        raw = data.get("steps")
        if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_PLAN_STEPS:
            raise ValueError(f"planner returned invalid step count: {len(raw) if isinstance(raw, list) else 0}")

        steps: List[Dict[str, Any]] = []
        known_ids: List[str] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict) or not str(item.get("query", "")).strip():
                raise ValueError(f"planner step {index} has no query")
            step_id = f"s{index}"
            requested_deps = item.get("depends_on", [])
            if not isinstance(requested_deps, list):
                raise ValueError(f"planner step {index} has invalid dependencies")
            if len(requested_deps) > 1:
                raise ValueError(f"planner step {index} has more than one dataframe dependency")
            dependencies = [str(dep) for dep in requested_deps if str(dep) in known_ids]
            if len(dependencies) != len(requested_deps):
                raise ValueError(f"planner step {index} references a non-previous step")
            steps.append({
                "id": step_id,
                "query": str(item["query"]).strip(),
                "depends_on": dependencies,
            })
            known_ids.append(step_id)

        self._validate_preserved_constraints(query, steps)

        confidence = float(data.get("confidence", 0.75))
        reasoning = str(data.get("reasoning", "LLM decomposition"))
        return steps, confidence, reasoning

    @staticmethod
    def _validate_preserved_constraints(
        original_query: str,
        steps: List[Dict[str, Any]],
    ) -> None:
        """Reject plans that silently lose currency qualifiers or numeric conditions."""
        planned_text = " ".join(str(step["query"]) for step in steps)
        missing_qualifiers = query_qualifiers(original_query) - query_qualifiers(planned_text)
        if missing_qualifiers:
            raise ValueError(
                f"planner dropped field qualifiers: {sorted(missing_qualifiers)}"
            )

        original_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", original_query))
        planned_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", planned_text))
        missing_numbers = original_numbers - planned_numbers
        if missing_numbers:
            raise ValueError(
                f"planner dropped numeric constraints: {sorted(missing_numbers)}"
            )

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("planner response must be a JSON object")
        return data

    @staticmethod
    def _rule_decompose(query: str) -> List[Dict[str, Any]]:
        """Conservative fallback for explicit sequential or parallel clauses."""
        marked = query
        for keyword in sorted(_SEQUENCE_KWS, key=len, reverse=True):
            marked = marked.replace(keyword, f"<SEQ>{keyword}")
        for keyword in sorted(_PARALLEL_KWS, key=len, reverse=True):
            marked = marked.replace(keyword, f"<PAR>{keyword}")
        marked = re.sub(r"[。！？!?；;]", "<PAR>", marked)

        pieces = re.split(r"(<SEQ>|<PAR>)", marked)
        raw_steps: List[Dict[str, Any]] = []
        next_dependency = False
        for piece in pieces:
            if piece == "<SEQ>":
                next_dependency = True
                continue
            if piece == "<PAR>":
                next_dependency = False
                continue
            clause = piece.strip(" ，,。；;：:")
            clause = re.sub(r"^(?:先|然后|接着|随后|最后)", "", clause).strip()
            if len(clause) < 2:
                continue
            step_id = f"s{len(raw_steps) + 1}"
            dependencies = [raw_steps[-1]["id"]] if raw_steps and next_dependency else []
            raw_steps.append({"id": step_id, "query": clause, "depends_on": dependencies})
            next_dependency = False

        return raw_steps[:_MAX_PLAN_STEPS]

    async def _route_steps(
        self,
        ctx: AnalysisContext,
        raw_steps: List[Dict[str, Any]],
        mode: MultiTurnMode,
    ) -> List[ExecutionStep]:
        steps: List[ExecutionStep] = []
        for index, raw in enumerate(raw_steps, start=1):
            step_routing = await self.routing_skill.run(ctx, raw["query"], atomic=True)
            dependencies = list(raw.get("depends_on", []))
            route = step_routing.hint
            needs_computation = step_routing.hint in (
                RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN
            )
            operations = set(step_routing.operation_intent.types)

            # A chart-only dependent step consumes an existing result; it does
            # not need generated pandas code of its own.
            if dependencies and operations and operations <= {"chart_data_prep", "general"}:
                route = RoutingHint.INSIGHT_ONLY
                needs_computation = False

            steps.append(ExecutionStep(
                step_id=f"s{index}",
                query=raw["query"],
                normalized_query=(
                    step_routing.normalized_query.normalized_text
                    if step_routing.normalized_query is not None
                    else raw["query"]
                ),
                route=route,
                depends_on=dependencies,
                input_source=(
                    "step"
                    if dependencies
                    else "previous_result"
                    if index == 1 and mode == MultiTurnMode.FOLLOW_UP
                    else "source"
                ),
                operation_intents=step_routing.operation_intent.types,
                output_intents=step_routing.output_intent.formats,
                output_explicit=step_routing.output_intent.explicit,
                needs_new_computation=needs_computation,
                target_fields=step_routing.target_fields,
                confidence=step_routing.confidence,
            ))
        return steps
