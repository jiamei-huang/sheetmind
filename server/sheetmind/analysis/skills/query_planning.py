"""Dependency-aware planning for multi-operation analysis queries."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

from ..context import (
    AnalysisContext,
    ExecutionStep,
    MultiTurnMode,
    QuerySemantics,
    RoutingHint,
)
from ..language import ResponseLanguage, infer_response_language, resolve_response_language
from ..models.configs import ModelRole
from .base import Skill
from .field_resolution import normalise_field_text, query_qualifiers
from .routing_classification import (
    RoutingClassificationSkill,
    RoutingResult,
    _LEVEL_1_THRESHOLDS,
    _PARALLEL_KWS,
    _SEQUENCE_KWS,
)

logger = logging.getLogger(__name__)

_MAX_PLAN_STEPS = int(_LEVEL_1_THRESHOLDS.get("max_plan_steps", 6))
_EXPLICIT_LIMIT = re.compile(
    r"(?:前\s*|top\s*|最高(?:的)?\s*|最低(?:的)?\s*)(\d+)",
    re.IGNORECASE,
)


class QueryPlan(BaseModel):
    """Validated execution plan returned by QueryPlanningSkill."""

    steps: List[ExecutionStep] = Field(default_factory=list)
    is_multi_step: bool = False
    source: Literal["single", "llm", "rule_fallback"] = "single"
    mode: Optional[MultiTurnMode] = None
    response_language: Optional[ResponseLanguage] = None
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
        inferred_language = infer_response_language(query)
        sheet_catalog = kwargs.get("sheet_catalog") or []
        force_semantic_planning = bool(kwargs.get("force_semantic_planning"))
        if (
            not routing.structure.needs_semantic_planning
            and not self._needs_context_planning(ctx, query, routing)
            and not force_semantic_planning
        ):
            return QueryPlan(
                steps=[self._single_step(query, routing)],
                is_multi_step=False,
                source="single",
                mode=routing.mode,
                response_language=inferred_language,
                confidence=routing.confidence,
                reasoning=routing.reasoning,
            )

        raw_steps: List[Dict[str, Any]]
        source: Literal["llm", "rule_fallback"]
        planned_mode = routing.mode
        response_language = inferred_language
        confidence = 0.65
        reasoning = "Structure detection requested semantic planning."
        try:
            raw_steps, planned_mode, response_language, confidence, reasoning = await self._llm_decompose(
                ctx,
                query,
                routing.mode,
                sheet_catalog=sheet_catalog,
            )
            source = "llm"
        except Exception as exc:
            logger.warning("[QueryPlanning] planner fallback: %s", exc)
            raw_steps = self._rule_decompose(query, sheet_catalog=sheet_catalog)
            source = "rule_fallback"
            response_language = inferred_language
            reasoning = f"Deterministic fallback after planner error: {exc}"

        # A one-step plan may classify context and fields, but it must not
        # paraphrase the user's operation into a different execution route.
        if len(raw_steps) == 1 and not raw_steps[0].get("depends_on"):
            raw_steps[0]["query"] = query

        steps = await self._route_steps(ctx, raw_steps, planned_mode)
        return QueryPlan(
            steps=steps,
            is_multi_step=len(steps) > 1,
            source=source,
            mode=planned_mode,
            response_language=response_language,
            confidence=min(max(confidence, 0.0), 1.0),
            reasoning=reasoning,
        )

    @staticmethod
    def _single_step(query: str, routing: RoutingResult) -> ExecutionStep:
        return ExecutionStep(
            step_id="s1",
            question_id="q1",
            query=query,
            normalized_query=(
                routing.normalized_query.normalized_text
                if routing.normalized_query is not None
                else query
            ),
            route=routing.hint,
            depends_on=[],
            input_source=(
                "active_dataframe"
                if routing.mode == MultiTurnMode.FOLLOW_UP
                and routing.hint in (RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN)
                else "previous_result"
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

    @staticmethod
    def _needs_context_planning(
        ctx: AnalysisContext,
        query: str,
        routing: RoutingResult,
    ) -> bool:
        if routing.mode == MultiTurnMode.RESET:
            return False
        has_context = ctx.active_result is not None or ctx.last_assistant_result() is not None
        if not has_context:
            return False
        # Once a task has a usable result, every new turn is semantically
        # context-sensitive even when it is phrased as a complete sentence.
        # The model decides whether to continue or start fresh; keyword rules
        # remain hints, never the gate that prevents that decision.
        return True

    async def _llm_decompose(
        self,
        ctx: AnalysisContext,
        query: str,
        default_mode: MultiTurnMode,
        *,
        sheet_catalog: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[List[Dict[str, Any]], MultiTurnMode, ResponseLanguage, float, str]:
        provider = self.router.get_provider(ModelRole.QUERY_PLANNING)
        system = (
            "你是多轮数据分析执行规划器。先判断当前查询是否依赖最近对话或上一轮结果。\n"
            "mode只能是new/follow_up/reset：用户说重新/全部数据/从头时为reset；"
            "用户用这个、这些、上述、其中、里面、继续、刚才等指代最近结果或范围时为follow_up；"
            "完全独立的新主题为new。\n"
            "每个步骤的input_source只能是source/active_dataframe/previous_result/step。"
            "follow_up且需要继续筛选、分组、汇总、排序、占比、Top-N等新计算时，"
            "第一步通常用active_dataframe；只解释或把上一轮结果转成图时才用previous_result。\n"
            "needs_new_computation表示是否需要真实数据处理；解释/解读通常为false。\n"
            "每个原子问题必须输出semantics。source_candidate_ids只能引用候选Sheet的candidate_id；"
            "source_hints保留用户说出的文件或Sheet概念。source_mode只能是single/union/join/independent。"
            "默认single；只有明确要求纵向合并且字段结构相同才是union；按键关联是join；"
            "分别回答多个Sheet必须拆成独立步骤，不得把compare误写成union。"
            "dimensions是分组维度；metrics包含field、aggregation和可选alias；"
            "filters包含field/operator/value；sort包含field/direction；limit只用于用户明确给出数字的Top N。"
            "对于‘哪个最高/最低/最多/最贵’等隐式极值问题，limit必须为null：结论指出极值项，"
            "但执行结果必须保留用于比较的完整分组排序表。\n"
            "target_fields必须列出步骤使用的精确字段名；如果规划上下文中存在精确列名，"
            "禁止把易仓SKU、平台SKU等不同字段合并成泛称SKU。"
            "scope_from为空、previous_result或前序步骤ID，表示从哪里继承筛选范围；"
            "需要上一轮范围但要回到明细数据计算时，使用input_source=active_dataframe且"
            "scope_from=previous_result。纠正上一轮分组字段或指标时使用"
            "scope_from=previous_filters，只继承原筛选条件，不继承错误结果实体。\n"
            "不得仅因为用户说趋势、走势或趋势图就自行添加日期、月份等时间字段；"
            "只有用户明确要求时间变化或可用schema中确有且问题需要时间字段时才使用。"
            "若上一轮表格已包含绘图所需维度和指标，优先使用previous_result，"
            "不要重复筛选、分组或汇总。\n"
            "然后判断查询是一个问题还是多个问题。\n"
            "response_language只能是zh或en，表示所有面向用户的动态回答语言。"
            "根据当前用户查询的主要自然语言判断，忽略文件名、Sheet名、字段名、SKU等原始标识；"
            "若用户明确要求中文或英文，则服从该要求。\n"
            "单一问题原样返回1个步骤；多个问题拆成最多6个原子步骤，并标出前置依赖。\n"
            "只负责结构、拆分和依赖，不选择执行引擎，不生成代码。\n"
            "每一步必须可单独执行；筛选、汇总、Top-N、比较、图表、解释应按用户语义排序。\n"
            "如果后一步基于前一步结果，depends_on 必须引用一个前序步骤ID；每步最多一个依赖。"
            "并行问题不添加依赖。\n"
            "图表或原因分析可以依赖计算步骤，但不应改写前一步的计算要求。\n"
            "只输出JSON："
            '{"mode":"new","response_language":"zh","steps":[{"id":"s1","query":"...","depends_on":[],'
            '"input_source":"source","scope_from":null,"target_fields":["精确列名"],'
            '"needs_new_computation":true,"semantics":{"source_hints":[],"source_candidate_ids":[],'
            '"source_mode":"single","dimensions":[],"metrics":[],"filters":[],"sort":[],"limit":null}}],'
            '"reasoning":"...","confidence":0.0}'
        )
        planning_context = self._planning_context(ctx)
        planning_context["sheet_catalog"] = [
            {
                "candidate_id": item.get("candidateId"),
                "file_name": item.get("fileName"),
                "sheet_name": item.get("sheetName"),
                "columns": item.get("columns", []),
            }
            for item in (sheet_catalog or [])
        ]
        user_content = (
            f"用户查询：{query}\n"
            f"规则初判mode：{default_mode.value}\n"
            "规划上下文："
            f"{json.dumps(planning_context, ensure_ascii=False, default=str)}"
        )
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

        planned_mode = self._parse_mode(data.get("mode"), default_mode)
        response_language = resolve_response_language(
            query,
            data.get("response_language"),
        )
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
            input_source = str(item.get("input_source", "")).strip()
            if input_source not in {"source", "active_dataframe", "previous_result", "step"}:
                input_source = "step" if dependencies else ""
            needs_raw = item.get("needs_new_computation")
            raw_targets = item.get("target_fields", [])
            target_fields = (
                [str(value) for value in raw_targets if str(value).strip()]
                if isinstance(raw_targets, list)
                else []
            )
            semantics_raw = item.get("semantics")
            try:
                semantics = QuerySemantics.model_validate(
                    semantics_raw if isinstance(semantics_raw, dict) else {}
                )
            except Exception as exc:
                raise ValueError(f"planner step {index} has invalid semantics: {exc}") from exc
            valid_candidate_ids = {
                str(candidate.get("candidateId", "")) for candidate in (sheet_catalog or [])
            }
            if any(
                candidate_id not in valid_candidate_ids
                for candidate_id in semantics.source_candidate_ids
            ):
                raise ValueError(f"planner step {index} invented a source candidate")
            semantic_fields = [
                *semantics.dimensions,
                *(metric.field for metric in semantics.metrics),
                *(query_filter.field for query_filter in semantics.filters),
                *(sort.field for sort in semantics.sort),
            ]
            target_fields = list(dict.fromkeys([*target_fields, *semantic_fields]))
            scope_from = str(item.get("scope_from") or "").strip() or None
            if scope_from not in {None, "previous_result", "previous_filters", *known_ids}:
                raise ValueError(f"planner step {index} references an invalid scope")
            steps.append({
                "id": step_id,
                "query": str(item["query"]).strip(),
                "depends_on": dependencies,
                "input_source": input_source,
                "scope_from": scope_from,
                "target_fields": target_fields,
                "needs_new_computation": needs_raw if isinstance(needs_raw, bool) else None,
                "semantics": semantics,
            })
            known_ids.append(step_id)

        self._validate_preserved_constraints(
            query,
            steps,
            ctx,
            sheet_catalog=sheet_catalog,
        )

        confidence = float(data.get("confidence", 0.75))
        reasoning = str(data.get("reasoning", "LLM decomposition"))
        return steps, planned_mode, response_language, confidence, reasoning

    @staticmethod
    def _parse_mode(raw_mode: Any, default_mode: MultiTurnMode) -> MultiTurnMode:
        try:
            return MultiTurnMode(str(raw_mode))
        except Exception:
            return default_mode

    @staticmethod
    def _validate_preserved_constraints(
        original_query: str,
        steps: List[Dict[str, Any]],
        ctx: Optional[AnalysisContext] = None,
        *,
        sheet_catalog: Optional[List[Dict[str, Any]]] = None,
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

        if ctx is None:
            return

        frames = [
            frame
            for frame in (ctx._active_df, ctx._result_df)
            if isinstance(frame, pd.DataFrame)
        ]
        last_tabular = ctx.last_tabular_result()
        last_table = last_tabular.first_table() if last_tabular is not None else None
        if last_table is not None and last_table.columns:
            frames.append(pd.DataFrame(last_table.rows, columns=last_table.columns))

        known_columns = list(dict.fromkeys(
            str(column)
            for frame in frames
            for column in frame.columns
        ))
        if ctx.active_lineage is not None:
            known_columns = list(dict.fromkeys([
                *known_columns,
                *ctx.active_lineage.filters.keys(),
                *ctx.active_lineage.result_filters.keys(),
                *ctx.active_lineage.result_columns,
            ]))
        known_columns = list(dict.fromkeys([
            *known_columns,
            *(
                str(column)
                for candidate in (sheet_catalog or [])
                for column in candidate.get("columns", [])
            ),
        ]))
        original_normalized = normalise_field_text(original_query)
        planned_targets = {
            normalise_field_text(target)
            for step in steps
            for target in step.get("target_fields", [])
        }
        planned_normalized = normalise_field_text(planned_text)
        missing_columns = [
            column
            for column in known_columns
            if len(normalise_field_text(column)) >= 2
            and normalise_field_text(column) in original_normalized
            and normalise_field_text(column) not in planned_normalized
            and normalise_field_text(column) not in planned_targets
        ]
        if missing_columns:
            raise ValueError(
                f"planner dropped explicit schema columns: {missing_columns}"
            )

        known_normalized = {normalise_field_text(column) for column in known_columns}
        unknown_targets = [
            target
            for step in steps
            for target in step.get("target_fields", [])
            if normalise_field_text(target) not in known_normalized
        ]
        if known_columns and unknown_targets:
            raise ValueError(
                f"planner referenced fields outside the available schema: {unknown_targets}"
            )

        mentioned_values: List[str] = []
        for frame in frames[:1]:
            for column in frame.columns:
                if pd.api.types.is_numeric_dtype(frame[column]):
                    continue
                values = frame[column].dropna().astype(str).drop_duplicates().head(1000)
                for value in values:
                    token = value.strip()
                    if len(token) >= 2 and token in original_query and token not in mentioned_values:
                        mentioned_values.append(token)
        missing_values = [value for value in mentioned_values if value not in planned_text]
        if missing_values:
            raise ValueError(
                f"planner dropped explicit filter values: {missing_values}"
            )

    @staticmethod
    def _planning_context(ctx: AnalysisContext) -> Dict[str, Any]:
        active = ctx._active_df if isinstance(ctx._active_df, pd.DataFrame) else None
        previous = ctx._result_df if isinstance(ctx._result_df, pd.DataFrame) else None
        if previous is None:
            result = ctx.last_tabular_result()
            table = result.first_table() if result is not None else None
            if table is not None:
                previous = pd.DataFrame(table.rows, columns=table.columns)

        def preview(frame: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
            if frame is None or frame.empty:
                return []
            safe = frame.head(5).astype(object).where(pd.notna(frame.head(5)), None)
            return safe.to_dict("records")

        latest_resolutions: List[Dict[str, Any]] = []
        if ctx.active_result is not None:
            for block in ctx.active_result.blocks:
                if getattr(block, "kind", "") in {"field_resolution", "sheet_resolution"}:
                    latest_resolutions.append(block.model_dump(mode="json"))

        return {
            "selected_sheets": list(ctx.selected_sheets),
            "requested_sheet_scope": list(ctx.requested_sheet_scope),
            "active_columns": [] if active is None else [str(c) for c in active.columns],
            "active_preview": preview(active),
            "previous_result_columns": [] if previous is None else [str(c) for c in previous.columns],
            "previous_result_preview": preview(previous),
            "latest_resolution_requests": latest_resolutions,
            "previous_execution_plan": (
                None if ctx.execution_plan is None else ctx.execution_plan.model_dump(mode="json")
            ),
            "previous_lineage": (
                None if ctx.active_lineage is None else ctx.active_lineage.model_dump(mode="json")
            ),
        }

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("planner response must be a JSON object")
        return data

    @classmethod
    def _rule_decompose(
        cls,
        query: str,
        *,
        sheet_catalog: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Conservatively split clauses while preserving explicit source scope."""
        marked = query
        for keyword in sorted(_SEQUENCE_KWS, key=len, reverse=True):
            marked = marked.replace(keyword, f"<SEQ>{keyword}")
        for keyword in sorted(_PARALLEL_KWS, key=len, reverse=True):
            marked = marked.replace(keyword, f"<PAR>{keyword}")
        marked = re.sub(r"[。！？!?；;]|(?<!\d)\.(?!\d)", "<PAR>", marked)

        pieces = re.split(r"(<SEQ>|<PAR>)", marked)
        raw_steps: List[Dict[str, Any]] = []
        next_dependency = False
        inherited_source_ids: List[str] = []
        inherited_source_hints: List[str] = []
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
            named_sources = cls._sources_named_in_clause(clause, sheet_catalog or [])
            if named_sources:
                inherited_source_ids = [
                    str(candidate.get("candidateId", ""))
                    for candidate in named_sources
                    if str(candidate.get("candidateId", ""))
                ]
                inherited_source_hints = list(dict.fromkeys(
                    str(candidate.get("sheetName", ""))
                    for candidate in named_sources
                    if str(candidate.get("sheetName", ""))
                ))
            step_id = f"s{len(raw_steps) + 1}"
            dependencies = [raw_steps[-1]["id"]] if raw_steps and next_dependency else []
            raw_steps.append({
                "id": step_id,
                "query": clause,
                "depends_on": dependencies,
                "semantics": QuerySemantics(
                    source_hints=list(inherited_source_hints),
                    source_candidate_ids=list(inherited_source_ids),
                ),
            })
            next_dependency = False

        return raw_steps[:_MAX_PLAN_STEPS]

    @staticmethod
    def _sources_named_in_clause(
        clause: str,
        sheet_catalog: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return exact Sheet-name matches; duplicate workbook versions stay ambiguous."""
        normalized_clause = re.sub(
            r"[\s_\-（）()【】\[\]{}.,，。:：]",
            "",
            str(clause).lower(),
        )
        matches: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in sheet_catalog:
            sheet_name = str(candidate.get("sheetName", "")).strip()
            normalized_sheet = re.sub(
                r"[\s_\-（）()【】\[\]{}.,，。:：]",
                "",
                sheet_name.lower(),
            )
            candidate_id = str(candidate.get("candidateId", ""))
            if (
                len(normalized_sheet) >= 2
                and normalized_sheet in normalized_clause
                and candidate_id
                and candidate_id not in seen
            ):
                matches.append(candidate)
                seen.add(candidate_id)
        return matches

    async def _route_steps(
        self,
        ctx: AnalysisContext,
        raw_steps: List[Dict[str, Any]],
        mode: MultiTurnMode,
    ) -> List[ExecutionStep]:
        steps: List[ExecutionStep] = []
        question_by_step: Dict[str, str] = {}
        question_count = 0
        for index, raw in enumerate(raw_steps, start=1):
            step_routing = await self.routing_skill.run(ctx, raw["query"], atomic=True)
            dependencies = list(raw.get("depends_on", []))
            route = step_routing.hint
            needs_computation = step_routing.hint in (
                RoutingHint.RULE_ENGINE, RoutingHint.CODE_GEN
            )
            if raw.get("needs_new_computation") is True:
                needs_computation = True
            operations = set(step_routing.operation_intent.types)

            # A chart-only dependent step consumes an existing result; it does
            # not need generated pandas code of its own.
            if dependencies and operations and operations <= {"chart_data_prep", "general"}:
                route = RoutingHint.INSIGHT_ONLY
                needs_computation = False

            input_source = str(raw.get("input_source") or "")
            if input_source not in {"source", "active_dataframe", "previous_result", "step"}:
                input_source = (
                    "step"
                    if dependencies
                    else "active_dataframe"
                    if index == 1 and mode == MultiTurnMode.FOLLOW_UP and needs_computation
                    else "previous_result"
                    if index == 1 and mode == MultiTurnMode.FOLLOW_UP
                    else "source"
                )

            scope_from = raw.get("scope_from")
            if scope_from is None and index == 1 and mode == MultiTurnMode.FOLLOW_UP and needs_computation:
                scope_from = "previous_result"

            planned_targets = [
                str(value)
                for value in raw.get("target_fields", [])
                if str(value).strip()
            ]
            target_fields = planned_targets or step_routing.target_fields
            semantics = raw.get("semantics")
            if not isinstance(semantics, QuerySemantics):
                semantics = QuerySemantics()
            # For an implicit extreme ("which is highest"), the limit belongs
            # to the prose conclusion, not to the evidence table. Keep every
            # grouped candidate so the user can verify how the winner was found.
            if (
                "extreme" in operations
                and semantics.limit is not None
                and _EXPLICIT_LIMIT.search(raw["query"]) is None
            ):
                semantics = semantics.model_copy(update={"limit": None})
            if dependencies:
                question_id = question_by_step.get(dependencies[0], f"q{question_count or 1}")
            else:
                question_count += 1
                question_id = f"q{question_count}"
            question_by_step[f"s{index}"] = question_id

            steps.append(ExecutionStep(
                step_id=f"s{index}",
                question_id=question_id,
                query=raw["query"],
                normalized_query=(
                    step_routing.normalized_query.normalized_text
                    if step_routing.normalized_query is not None
                    else raw["query"]
                ),
                route=route,
                depends_on=dependencies,
                input_source=input_source,  # type: ignore[arg-type]
                scope_from=scope_from,
                operation_intents=step_routing.operation_intent.types,
                output_intents=step_routing.output_intent.formats,
                output_explicit=step_routing.output_intent.explicit,
                needs_new_computation=needs_computation,
                target_fields=target_fields,
                semantics=semantics,
                confidence=step_routing.confidence,
            ))
        return steps
