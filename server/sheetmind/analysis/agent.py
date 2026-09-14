"""
SheetMind analysis orchestrator.

SheetMindAgent.run(ctx, query, emitter?) is the single entry point for
one analysis query.  It:
  1. Emits SSE progress events via the optional emitter
  2. Executes the skill pipeline (routing → sheet selection → execution → assemble)
  3. Appends the turn to AnalysisContext.conversation
  4. Records a Trace
  5. Returns ResultBlocks.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .context import (
    AnalysisContext,
    CalculationBasis,
    ChartBlock,
    ColumnMeta,
    ExecutionPlan,
    ExecutionStep,
    ExecutionReport,
    FieldCandidate,
    FieldResolutionBlock,
    FieldResolutionRecord,
    MultiTurnMode,
    QuestionResult,
    ResultBlocks,
    ResultLineage,
    RoutingHint,
    SheetCandidate,
    SheetResolutionBlock,
    SourceBinding,
    StatusBlock,
    SummaryBlock,
    TableBlock,
)
from .harness.repair_loop import RepairLoop
from .artifacts import get_artifact_store
from .language import (
    ResponseLanguage,
    infer_response_language,
    resolve_response_language,
    user_text,
)
from .models.router import ModelRouter
from .skills.chart_planning import ChartPlanningSkill
from .skills.code_generation import CodeGenerationSkill
from .skills.data_profiling import DataProfilingSkill
from .skills.field_resolution import FieldResolver, normalise_field_text
from .skills.insight_writing import InsightWritingSkill
from .skills.output_planning import OutputPlanningSkill
from .skills.query_planning import QueryPlan, QueryPlanningSkill
from .skills.query_normalization import QueryNormalizationSkill
from .skills.routing_classification import (
    RoutingClassificationSkill,
    RoutingResult,
)
from .skills.semantic_typing import SemanticFieldMap, SemanticTypingSkill
from .skills.sheet_selection import SheetSelectionDecision, SheetSelectionSkill
from .streaming.emitter import StreamEmitter
from .tools.dataframe_loader import DataframeLoaderTool
from .tools.data_type_normalizer import DataTypeNormalizationTool
from .tools.python_executor import PythonExecutorTool
from .tools.rule_engine import RuleEngineTool
from .tracing.storage import get_trace_store
from .tracing.current import bind_trace, reset_trace
from .tracing.trace import (
    EVT_ERROR,
    EVT_RESULT_ASSEMBLED,
    EVT_ROUTING,
    EVT_TOOL_END,
    Trace,
)
from .validators.result_validator import validate_result
from .validators.rule_result_validator import RuleResultValidator
from .validators.execution_contract_validator import ExecutionContractValidator

logger = logging.getLogger(__name__)

# Maximum rows in TableBlock.rows returned to frontend
_MAX_TABLE_ROWS = 1000


_DIRECT_WORKBOOK_EDIT_TERMS = (
    "直接修改",
    "直接改",
    "改好",
    "写回",
    "写入",
    "加好列",
    "加公式",
    "写公式",
    "保存到excel",
    "保存到 excel",
    "保存回",
    "下载改好",
    "修改原",
    "edit the workbook",
    "edit original",
    "modify the workbook",
    "modify original",
    "write back",
    "save back",
    "add a formula",
    "insert formula",
    "update the excel",
    "change the excel",
)


def _requests_direct_workbook_edit(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in _DIRECT_WORKBOOK_EDIT_TERMS)


def _workbook_edit_boundary_result(
    query: str,
    response_language: Optional[ResponseLanguage] = None,
) -> ResultBlocks:
    language = response_language or infer_response_language(query)
    content = user_text(
        language,
        en=(
            "I can't directly edit the original Excel workbook yet. "
            "SheetMind works on a parsed data preview so the uploaded file stays unchanged.\n\n"
            "I can help you design the transformation, calculate or preview the new column, "
            "explain the formula logic, and export a clean result after review."
        ),
        zh=(
            "SheetMind 暂时不能直接修改原始 Excel 文件。系统会分析解析后的数据预览，"
            "因此你上传的文件会保持不变。\n\n"
            "我可以帮助你设计处理逻辑、计算或预览新列、解释公式，并在确认后导出结果。"
        ),
    )
    block = SummaryBlock(content=content)
    return ResultBlocks(
        status="success",
        response_language=language,
        output_intents=["insight"],
        questions=[
            QuestionResult(
                question_id="q1",
                query=query,
                status="success",
                blocks=[block],
            )
        ],
        blocks=[block],
    )


class SheetMindAgent:
    """
    Top-level orchestrator for one SheetMind analysis run.

    One instance is safe to reuse across multiple requests (stateless itself;
    all state lives in AnalysisContext).

    Args:
        model_router — ModelRouter instance (optional; defaults to singleton
                       built from DEFAULT_CONFIGS + env overrides)
    """

    def __init__(self, model_router: Optional[ModelRouter] = None) -> None:
        self.router = model_router or ModelRouter()
        self.trace_store = get_trace_store()
        self.artifact_store = get_artifact_store()

        # Instantiate all skills and tools once (reusable, stateless)
        self.normalization_skill = QueryNormalizationSkill(self.router)
        self.routing_skill = RoutingClassificationSkill(
            self.router, self.normalization_skill
        )
        self.planning_skill = QueryPlanningSkill(self.router, self.routing_skill)
        self.output_planning_skill = OutputPlanningSkill(self.router)
        self.sheet_skill = SheetSelectionSkill(self.router)
        self.semantic_skill = SemanticTypingSkill(self.router)
        self.profiling_skill = DataProfilingSkill(self.router)
        self.code_gen_skill = CodeGenerationSkill(self.router)
        self.chart_skill = ChartPlanningSkill(self.router)
        self.insight_skill = InsightWritingSkill(self.router)

        self.df_loader = DataframeLoaderTool()
        self.type_normalizer = DataTypeNormalizationTool()
        self.rule_engine = RuleEngineTool()
        self.executor = PythonExecutorTool()
        self.rule_result_validator = RuleResultValidator()
        self.execution_contract_validator = ExecutionContractValidator()

        self.repair_loop = RepairLoop(self.code_gen_skill, self.executor)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        emitter: Optional[StreamEmitter] = None,
        run_id: Optional[str] = None,
    ) -> ResultBlocks:
        """
        Run one analysis turn.

        Args:
            ctx     — AnalysisContext (updated in-place with the new turn)
            query   — user's natural-language question
            emitter — optional SSE emitter; if provided, streams progress events

        Returns:
            ResultBlocks (native result protocol).
        """
        trace = Trace(
            session_trace_id=ctx.trace_id,
            project_id=ctx.project_id,
            task_id=ctx.task_id,
            query=query,
        )
        trace_token = bind_trace(trace)

        try:
            if emitter:
                await emitter.emit_thinking()

            result, routing_hint, multiturn_mode = await self._run_pipeline(
                ctx, query, trace, emitter
            )
            if run_id:
                result.run_id = run_id

            trace.add_event(
                EVT_RESULT_ASSEMBLED,
                output_summary=f"blocks={len(result.blocks)} has_table={result.has_table} has_chart={result.has_chart}",
            )
            trace.routing_hint = routing_hint.value if routing_hint else None
            trace.multiturn_mode = multiturn_mode.value if multiturn_mode else None

            # Persist turn in conversation history
            ctx.add_user_turn(query)
            ctx.add_assistant_turn(
                summary=self._extract_summary(result),
                result=result,
                routing_hint=routing_hint,
                multiturn_mode=multiturn_mode,
            )

            trace.finish(success=True)
            self.trace_store.save(trace)
            logger.info(trace.summary_line())

            if emitter:
                await emitter.emit_done(result.model_dump())

            return result

        except Exception as exc:
            logger.exception("SheetMindAgent.run failed for task=%s: %s", ctx.task_id, exc)
            trace.add_event(EVT_ERROR, error=str(exc))
            trace.finish(success=False, error=str(exc))
            self.trace_store.save(trace)

            if emitter:
                await emitter.emit_error()

            raise
        finally:
            reset_trace(trace_token)

    # ------------------------------------------------------------------
    # Analysis pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        ctx: AnalysisContext,
        query: str,
        trace: Trace,
        emitter: Optional[StreamEmitter],
    ) -> tuple:  # (ResultBlocks, Optional[RoutingHint], Optional[MultiTurnMode])
        """
        Execute the full skill pipeline:
          1. RoutingClassificationSkill → query structure + coarse route
          2. QueryPlanningSkill → validated atomic steps + dependencies
          3. SheetSelectionSkill + DataframeLoaderTool → source df
          4. Execute each step against its declared source/dependency result
          5. ChartPlanningSkill → chart blocks (if requested)
          6. InsightWritingSkill → summary_text
          7. Assemble + validate ResultBlocks
        """
        response_language = infer_response_language(query)
        # ----------------------------------------------------------------
        # 1. Routing classification
        # ----------------------------------------------------------------
        if _requests_direct_workbook_edit(query):
            return (
                _workbook_edit_boundary_result(query, response_language),
                RoutingHint.INSIGHT_ONLY,
                MultiTurnMode.NEW_QUERY,
            )

        if emitter:
            await emitter.emit_progress("Understanding your question...", step_id="routing")

        normalized_query = await self.normalization_skill.run(ctx, query)
        try:
            sheet_catalog = self.sheet_skill.catalog(ctx)
        except Exception as exc:
            logger.warning("[Pipeline] source catalog unavailable: %s", exc)
            sheet_catalog = []
        planning_sheet_catalog = self.sheet_skill.metadata_within_checked_scope(
            ctx,
            sheet_catalog,
        )
        routing = await self.routing_skill.run(
            ctx, query, normalized_query=normalized_query
        )
        hint: RoutingHint = routing.hint
        mode: MultiTurnMode = routing.mode
        if ctx.source_scope_changed:
            mode = MultiTurnMode.RESET
            routing.mode = MultiTurnMode.RESET
        explicit_source_switch = False
        wants_chart = (
            hint != RoutingHint.INSIGHT_ONLY
            and routing.output_intent.wants_chart
        )

        previous_result_df = self._previous_result_dataframe(ctx)
        if (
            mode == MultiTurnMode.FOLLOW_UP
            and len(sheet_catalog) <= 1
            and not routing.structure.needs_semantic_planning
            and wants_chart
            and self._query_targets_previous_result(query)
            and previous_result_df is not None
            and not previous_result_df.empty
        ):
            shortcut_plan = QueryPlan(
                steps=[QueryPlanningSkill._single_step(query, routing)],
                response_language=response_language,
                confidence=routing.confidence,
                reasoning="Direct presentation from the previous result.",
            )
            ctx.execution_plan = self._build_execution_plan(shortcut_plan, routing)
            self._trace_execution_plan(trace, ctx.execution_plan, routing)
            return await self._run_previous_result_chart(
                ctx=ctx,
                query=query,
                previous_result_df=previous_result_df,
                hint=hint,
                mode=mode,
                emitter=emitter,
                response_language=response_language,
            )

        if routing.structure.needs_semantic_planning and emitter:
            await emitter.emit_progress("Identifying the question structure...", step_id="query_planning")
        query_plan = await self.planning_skill.run(
            ctx,
            query,
            routing=routing,
            sheet_catalog=planning_sheet_catalog,
            force_semantic_planning=len(planning_sheet_catalog) > 1,
        )
        response_language = resolve_response_language(
            query,
            query_plan.response_language,
        )
        query_plan.response_language = response_language
        self._assign_question_ids(query_plan)
        active_candidate_ids = self._candidate_ids_from_scope(ctx.active_source_scope)
        planned_candidate_sets = [
            set(step.semantics.source_candidate_ids)
            for step in query_plan.steps
            if not step.depends_on and step.semantics.source_candidate_ids
        ]
        explicit_source_switch = bool(
            active_candidate_ids
            and any(candidates != active_candidate_ids for candidates in planned_candidate_sets)
        )
        mode = query_plan.mode or mode
        if ctx.source_scope_changed or explicit_source_switch:
            mode = MultiTurnMode.RESET
            query_plan.mode = MultiTurnMode.RESET
            for step in query_plan.steps:
                if not step.depends_on:
                    step.input_source = "source"
                    step.scope_from = None

        if self._can_reuse_previous_result_for_chart(
            ctx=ctx,
            query=query,
            plan=query_plan,
            previous_result_df=previous_result_df,
            wants_chart=wants_chart,
            explicit_source_switch=explicit_source_switch,
        ):
            mode = MultiTurnMode.FOLLOW_UP
            query_plan.mode = MultiTurnMode.FOLLOW_UP
            query_plan.reasoning = (
                f"{query_plan.reasoning} Reused the compatible previous tabular result "
                "for chart presentation."
            ).strip()
            for step in query_plan.steps:
                step.input_source = "previous_result"
                step.scope_from = None
                step.needs_new_computation = False

            execution_plan = self._build_execution_plan(query_plan, routing)
            ctx.execution_plan = execution_plan
            hint = execution_plan.route
            self._trace_execution_plan(trace, execution_plan, routing)
            return await self._run_previous_result_chart(
                ctx=ctx,
                query=query,
                previous_result_df=previous_result_df,
                hint=hint,
                mode=mode,
                emitter=emitter,
                response_language=response_language,
            )

        execution_plan = self._build_execution_plan(query_plan, routing)
        ctx.execution_plan = execution_plan
        hint = execution_plan.route
        self._trace_execution_plan(trace, execution_plan, routing)
        logger.debug(
            "[Pipeline] routing=%s mode=%s steps=%d planner=%s",
            hint.value,
            mode.value,
            len(query_plan.steps),
            query_plan.source,
        )

        # ----------------------------------------------------------------
        # 2-3. Bind and load a source independently for every root question.
        # ----------------------------------------------------------------
        source_dfs, source_resolution = await self._bind_step_sources(
            ctx=ctx,
            plan=query_plan,
            mode=mode,
            sheet_catalog=sheet_catalog,
            emitter=emitter,
        )
        if source_resolution is not None:
            sheet_block = self._sheet_resolution_block(
                ctx,
                source_resolution,
                response_language=response_language,
            )
            question = QuestionResult(
                question_id="q1",
                query=query,
                status="needs_input",
                blocks=[sheet_block],
            )
            return ResultBlocks(
                status="needs_input",
                response_language=response_language,
                focus_question_id="q1",
                output_intents=execution_plan.output_intents,
                questions=[question],
                blocks=[sheet_block],
            ), hint, mode
        df = next(iter(source_dfs.values()), None)

        # ----------------------------------------------------------------
        # 4. Execute validated plan steps in dependency order
        # ----------------------------------------------------------------
        result_df, visible_results, step_outputs, failed_steps = await self._execute_plan_steps(
            ctx=ctx,
            plan=query_plan,
            source_df=df,
            source_dfs=source_dfs,
            previous_result_df=previous_result_df,
            execution_plan=execution_plan,
            trace=trace,
            emitter=emitter,
        )
        hint = execution_plan.route
        if failed_steps and not visible_results:
            clarification_blocks = self._field_resolution_blocks(
                execution_plan.field_resolutions,
                status="needs_clarification",
                response_language=response_language,
            )
            if clarification_blocks:
                questions = [
                    QuestionResult(
                        question_id=step.question_id or step.step_id,
                        query=step.query,
                        status="needs_input",
                        blocks=clarification_blocks,
                        execution_report=step.execution_report,
                    )
                    for step in query_plan.steps
                    if step.step_id in failed_steps
                ]
                clarification_result = ResultBlocks(
                    status="needs_input",
                    response_language=response_language,
                    focus_question_id=questions[-1].question_id if questions else None,
                    output_intents=execution_plan.output_intents,
                    questions=questions,
                    blocks=clarification_blocks,
                )
                return validate_result(clarification_result), hint, mode
            error_msg = user_text(
                response_language,
                en="The data query failed. The failed stage and data source were recorded for review.",
                zh="数据查询执行失败。失败阶段和数据来源已记录，便于检查。",
            )
            logger.warning("[Pipeline] planned execution failed for task=%s", ctx.task_id)
            status_block = StatusBlock(
                status="failed",
                message=error_msg,
                error_code="execution_failed",
                details={"steps": failed_steps},
            )
            questions = [
                QuestionResult(
                    question_id=step.question_id or step.step_id,
                    query=step.query,
                    status="failed",
                    blocks=[status_block],
                    execution_report=step.execution_report,
                )
                for step in query_plan.steps
                if step.step_id in failed_steps
            ]
            return ResultBlocks(
                status="failed",
                response_language=response_language,
                focus_question_id=questions[-1].question_id if questions else None,
                output_intents=execution_plan.output_intents,
                questions=questions,
                blocks=[status_block],
            ), hint, mode

        # ----------------------------------------------------------------
        # 5. Build charts against the result owned by each atomic question.
        # ----------------------------------------------------------------
        chart_blocks_by_question: Dict[str, List[ChartBlock]] = {}
        chart_requests = [
            step for step in query_plan.steps if "chart" in step.output_intents
        ]
        for step in chart_requests:
            chart_df = step_outputs.get(step.step_id)
            if chart_df is None:
                same_question = [
                    frame
                    for visible_step, frame in visible_results
                    if visible_step.question_id == step.question_id
                ]
                chart_df = same_question[-1] if same_question else result_df
            if chart_df is None or chart_df.empty:
                continue
            if emitter:
                await emitter.emit_progress("Building the chart...", step_id="chart_planning")
            step_query = step.normalized_query or step.query
            result_field_map = await self.semantic_skill.run(ctx, step_query, df=chart_df)
            chart = await self.chart_skill.run(
                ctx,
                step_query,
                result_df=chart_df,
                field_map=result_field_map,
                required_columns=step.required_source_columns,
            )
            if chart is not None:
                chart.title = step.query
                chart_blocks_by_question.setdefault(step.question_id, []).append(chart)

        # ----------------------------------------------------------------
        # 6-7. Assemble a self-contained result for every sub-question.
        # ----------------------------------------------------------------
        if emitter:
            await emitter.emit_progress("Writing analysis insights...", step_id="insight_writing")

        question_ids = list(dict.fromkeys(
            step.question_id or step.step_id for step in query_plan.steps
        ))
        questions: List[QuestionResult] = []
        blocks: List[Any] = []
        assumed_blocks = self._field_resolution_blocks(
            execution_plan.field_resolutions,
            status="assumed",
            response_language=response_language,
        )

        for question_id in question_ids:
            question_steps = [
                step for step in query_plan.steps
                if (step.question_id or step.step_id) == question_id
            ]
            terminal_step = question_steps[-1]
            visible_for_question = [
                (step, frame)
                for step, frame in visible_results
                if (step.question_id or step.step_id) == question_id
            ]
            evidence_step, question_df = (
                visible_for_question[-1]
                if visible_for_question
                else (terminal_step, step_outputs.get(terminal_step.step_id))
            )
            if question_df is None and terminal_step.route == RoutingHint.INSIGHT_ONLY:
                if terminal_step.input_source == "previous_result":
                    question_df = previous_result_df
                else:
                    for candidate_df in (
                        source_dfs.get(question_steps[0].step_id),
                        ctx._result_df,
                        ctx._active_df,
                    ):
                        if candidate_df is not None:
                            question_df = candidate_df
                            break

            question_failed = any(step.step_id in failed_steps for step in question_steps)
            if question_failed:
                status_block = StatusBlock(
                    status="failed",
                    message=user_text(
                        response_language,
                        en="This question failed. Available results for the other questions were preserved.",
                        zh="这个问题执行失败，其他问题的可用结果已保留。",
                    ),
                    error_code=(terminal_step.execution_report.error_code if terminal_step.execution_report else "execution_failed"),
                    details={"step_ids": [step.step_id for step in question_steps]},
                )
                question_blocks: List[Any] = []
                if question_df is not None and not question_df.empty:
                    artifact_id = self.artifact_store.save(
                        ctx.task_id,
                        question_id,
                        question_df,
                    )
                    partial_table = self._build_table_block(
                        question_df,
                        artifact_id=artifact_id,
                    )
                    if partial_table is not None:
                        partial_table.title = user_text(
                            response_language,
                            en=f"{evidence_step.query} (intermediate result)",
                            zh=f"{evidence_step.query}（中间结果）",
                        )
                        partial_table.calculation_basis = self._build_calculation_basis(
                            ctx,
                            evidence_step,
                            question_df,
                        )
                        question_blocks.append(partial_table)
                question_blocks.append(status_block)
                question_status = "failed"
            elif question_df is None or question_df.empty:
                status_block = StatusBlock(
                    status="empty",
                    message=user_text(
                        response_language,
                        en="No data matched this query. Check the filters, currency, or data source.",
                        zh="没有与该问题匹配的数据。请检查筛选条件、币种或数据来源。",
                    ),
                    error_code="empty_result",
                    details={
                        "source_rows": terminal_step.execution_report.source_rows
                        if terminal_step.execution_report else None,
                    },
                )
                question_blocks = [status_block]
                question_status = "empty"
            else:
                question_output_intents = list(dict.fromkeys(
                    output_intent
                    for question_step in question_steps
                    for output_intent in question_step.output_intents
                ))
                question_has_computation = any(
                    question_step.needs_new_computation
                    and question_step.route != RoutingHint.INSIGHT_ONLY
                    for question_step in question_steps
                )
                step_output_plan = await self.output_planning_skill.run(
                    ctx,
                    terminal_step.query,
                    output_intents=question_output_intents,
                    route=evidence_step.route,
                    has_computation=question_has_computation,
                )
                question_blocks = []
                table_block: Optional[TableBlock] = None
                if step_output_plan.include_table:
                    artifact_id = self.artifact_store.save(
                        ctx.task_id,
                        question_id,
                        question_df,
                    )
                    table_block = self._build_table_block(
                        question_df,
                        artifact_id=artifact_id,
                    )
                    if table_block is not None:
                        table_block.title = evidence_step.query
                        table_block.calculation_basis = self._build_calculation_basis(
                            ctx,
                            evidence_step,
                            question_df,
                        )

                question_charts = chart_blocks_by_question.get(question_id, [])
                scenario = (
                    "insight_only"
                    if terminal_step.route == RoutingHint.INSIGHT_ONLY
                    else "chart" if question_charts else "processing"
                )
                summary_text = await self.insight_skill.run(
                    ctx,
                    terminal_step.query,
                    scenario=scenario,
                    result_df=question_df,
                    chart_block=question_charts[0] if question_charts else None,
                    table_block=table_block,
                    response_language=response_language,
                    source_row_count=(
                        evidence_step.execution_report.source_rows
                        if evidence_step.execution_report is not None
                        else None
                    ),
                )
                if summary_text and step_output_plan.include_summary:
                    question_blocks.append(SummaryBlock(content=summary_text))
                if table_block is not None:
                    question_blocks.append(table_block)
                question_blocks.extend(question_charts)
                question_status = "success"

            questions.append(QuestionResult(
                question_id=question_id,
                query=terminal_step.query,
                status=question_status,
                blocks=question_blocks,
                execution_report=terminal_step.execution_report or evidence_step.execution_report,
            ))
            blocks.extend(question_blocks)

        statuses = {question.status for question in questions}
        overall_status = (
            "success" if statuses == {"success"}
            else "empty" if statuses <= {"empty"}
            else "failed" if statuses <= {"failed", "skipped"}
            else "partial"
        )
        result = ResultBlocks(
            status=overall_status,
            response_language=response_language,
            focus_question_id=questions[-1].question_id if questions else None,
            output_intents=execution_plan.output_intents,
            questions=questions,
            blocks=[*assumed_blocks, *blocks],
        )

        if emitter:
            await emitter.emit_progress("Validating results...", step_id="validation")
        result = validate_result(result, degrade_invalid_charts=True)

        return result, hint, mode

    async def _bind_step_sources(
        self,
        *,
        ctx: AnalysisContext,
        plan: QueryPlan,
        mode: MultiTurnMode,
        sheet_catalog: List[Dict[str, Any]],
        emitter: Optional[StreamEmitter],
    ) -> tuple[Dict[str, pd.DataFrame], Optional[SheetSelectionDecision]]:
        """Resolve one physical source per independent root question."""
        source_dfs: Dict[str, pd.DataFrame] = {}
        root_steps = [step for step in plan.steps if not step.depends_on]

        for step in root_steps:
            needs_rows = step.needs_new_computation or (
                step.route == RoutingHint.INSIGHT_ONLY and ctx.active_result is None
            )
            if not needs_rows:
                continue

            planned_sources = set(step.semantics.source_candidate_ids)
            active_sources = self._candidate_ids_from_scope(ctx.active_source_scope)
            can_reuse_active_source = (
                not planned_sources or planned_sources == active_sources
            )
            if (
                step.input_source in {"active_dataframe", "previous_result"}
                and step.needs_new_computation
                and ctx._active_df is not None
                and can_reuse_active_source
            ):
                source_dfs[step.step_id] = ctx._active_df
                step.source_bindings = self._bindings_from_scope(ctx.active_source_scope)
                continue
            if step.input_source == "previous_result" and not step.needs_new_computation:
                continue

            selected_files: List[Dict[str, Any]] = []
            if (
                step.input_source == "active_dataframe"
                and ctx.active_source_scope
                and not step.semantics.source_candidate_ids
            ):
                try:
                    parsed = json.loads(ctx.active_source_scope)
                    if isinstance(parsed, list):
                        selected_files = parsed
                except (TypeError, ValueError, json.JSONDecodeError):
                    selected_files = []

            if not selected_files:
                if emitter:
                    await emitter.emit_progress(
                        f"Selecting a data source for \"{step.query[:24]}\"...",
                        step_id="sheet_selection",
                    )
                try:
                    selection = await self.sheet_skill.run(
                        ctx,
                        step.normalized_query or step.query,
                        target_fields=step.target_fields,
                        metadata=sheet_catalog,
                        preferred_candidate_ids=step.semantics.source_candidate_ids,
                        source_mode=step.semantics.source_mode,
                    )
                except Exception as exc:
                    step.execution_report = ExecutionReport(
                        status="failed",
                        engine=step.route.value,
                        error_code="source_selection_failed",
                        error_message=str(exc),
                    )
                    continue
                if isinstance(selection, SheetSelectionDecision):
                    if selection.needs_user_input:
                        return source_dfs, selection
                    selected_files = selection.selected_files
                    selected_candidates = {
                        candidate.candidate_id: candidate for candidate in selection.candidates
                    }
                    step.source_bindings = [
                        SourceBinding(
                            candidate_id=f"{item.get('fileId') or item.get('fileName', '')}::{sheet}",
                            file_id=str(item.get("fileId", "")) or None,
                            file_name=str(item.get("fileName", "")),
                            sheet_name=str(sheet),
                            confidence=float(item.get("confidence", selection.confidence)),
                            reason=str(item.get("reason", selection.reason)),
                        )
                        for item in selected_files
                        for sheet in item.get("sheets", [])
                        if f"{item.get('fileId') or item.get('fileName', '')}::{sheet}" in selected_candidates
                        or not selected_candidates
                    ]
                else:
                    selected_files = selection

            if not step.source_bindings:
                step.source_bindings = [
                    SourceBinding(
                        candidate_id=f"{item.get('fileId') or item.get('fileName', '')}::{sheet}",
                        file_id=str(item.get("fileId", "")) or None,
                        file_name=str(item.get("fileName", "")),
                        sheet_name=str(sheet),
                        confidence=float(item.get("confidence", 1.0)),
                        reason=str(item.get("reason", "validated source scope")),
                    )
                    for item in selected_files
                    for sheet in item.get("sheets", [])
                ]

            try:
                if emitter:
                    await emitter.emit_progress("Loading data...", step_id="data_loading")
                load_report = self.df_loader.run(
                    ctx,
                    selected_files=selected_files,
                    multiturn_mode=(
                        MultiTurnMode.RESET
                        if mode == MultiTurnMode.RESET
                        else MultiTurnMode.NEW_QUERY
                    ),
                    merge_strategy=step.semantics.source_mode,
                    update_context=False,
                    return_report=True,
                )
                loaded_df = (
                    load_report
                    if isinstance(load_report, pd.DataFrame)
                    else load_report.df
                )
                warnings = (
                    []
                    if isinstance(load_report, pd.DataFrame)
                    else list(load_report.warnings)
                )
                source_dfs[step.step_id] = loaded_df
                step.execution_report = ExecutionReport(
                    status="success",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    source_rows=len(loaded_df),
                    warnings=warnings,
                )
            except Exception as exc:
                step.execution_report = ExecutionReport(
                    status="failed",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    error_code="data_loading_failed",
                    error_message=str(exc),
                )

        return source_dfs, None

    @staticmethod
    def _candidate_ids_from_scope(raw_scope: str) -> set[str]:
        return {
            binding.candidate_id
            for binding in SheetMindAgent._bindings_from_scope(raw_scope)
        }

    @staticmethod
    def _assign_question_ids(plan: QueryPlan) -> None:
        """Normalize externally supplied plans into stable question groups."""
        used = {step.question_id for step in plan.steps if step.question_id}
        question_by_step: Dict[str, str] = {}
        counter = 0

        def next_question_id() -> str:
            nonlocal counter
            while True:
                counter += 1
                candidate = f"q{counter}"
                if candidate not in used:
                    used.add(candidate)
                    return candidate

        for step in plan.steps:
            if step.question_id:
                question_id = step.question_id
            elif step.depends_on:
                question_id = question_by_step.get(step.depends_on[0]) or next_question_id()
            else:
                question_id = next_question_id()
            step.question_id = question_id
            question_by_step[step.step_id] = question_id

    @staticmethod
    def _bindings_from_scope(raw_scope: str) -> List[SourceBinding]:
        try:
            scope = json.loads(raw_scope) if raw_scope else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return [
            SourceBinding(
                candidate_id=f"{item.get('fileId') or item.get('fileName', '')}::{sheet}",
                file_id=str(item.get("fileId", "")) or None,
                file_name=str(item.get("fileName", "")),
                sheet_name=str(sheet),
                confidence=1.0,
                reason="reused previous validated source",
            )
            for item in scope
            if isinstance(item, dict)
            for sheet in item.get("sheets", [])
        ]

    async def _execute_plan_steps(
        self,
        ctx: AnalysisContext,
        plan: QueryPlan,
        source_df: Optional[pd.DataFrame],
        source_dfs: Dict[str, pd.DataFrame],
        previous_result_df: Optional[pd.DataFrame],
        execution_plan: ExecutionPlan,
        trace: Trace,
        emitter: Optional[StreamEmitter],
    ) -> tuple[
        Optional[pd.DataFrame],
        List[tuple[ExecutionStep, pd.DataFrame]],
        Dict[str, pd.DataFrame],
        List[str],
    ]:
        """Execute an already validated plan; dependencies may only point backward."""
        outputs: Dict[str, pd.DataFrame] = {}
        output_scopes: Dict[str, Dict[str, List[str]]] = {}
        computed: List[tuple[ExecutionStep, pd.DataFrame]] = []
        failed_steps: List[str] = []
        source_by_step: Dict[str, Optional[pd.DataFrame]] = {}
        required_columns: List[str] = []
        previous_scope = (
            dict(ctx.active_lineage.filters)
            if ctx.active_lineage is not None
            else {}
        )
        previous_result_scope = (
            dict(ctx.active_lineage.result_filters)
            if ctx.active_lineage is not None
            else {}
        )

        for index, step in enumerate(plan.steps, start=1):
            step_query = step.normalized_query or step.query
            if step.depends_on and not step.source_bindings:
                dependency_step = next(
                    (item for item in plan.steps if item.step_id == step.depends_on[0]),
                    None,
                )
                if dependency_step is not None:
                    step.source_bindings = list(dependency_step.source_bindings)
            if step.depends_on and any(dependency in failed_steps for dependency in step.depends_on):
                failed_steps.append(step.step_id)
                step.execution_report = ExecutionReport(
                    status="skipped",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    error_code="dependency_failed",
                    error_message="A prerequisite analysis step failed.",
                )
                continue
            step_source_df = (source_dfs or {}).get(step.step_id)
            if step.depends_on:
                step_source_df = source_by_step.get(step.depends_on[0], step_source_df)
            if step_source_df is None:
                step_source_df = source_df
            input_df = self._step_input_dataframe(
                step,
                outputs=outputs,
                source_df=step_source_df,
                previous_result_df=previous_result_df,
                previous_scope_filters=previous_scope,
                previous_result_filters=previous_result_scope,
                output_scopes=output_scopes,
            )
            if input_df is None:
                if step.needs_new_computation:
                    logger.warning("[Pipeline] no input dataframe for step=%s", step.step_id)
                    failed_steps.append(step.step_id)
                    if step.execution_report is None:
                        step.execution_report = ExecutionReport(
                            status="failed",
                            engine=step.route.value,
                            source_bindings=step.source_bindings,
                            error_code="missing_input",
                            error_message="No dataframe was available for this question.",
                        )
                    continue
                continue

            if not step.needs_new_computation or step.route == RoutingHint.INSIGHT_ONLY:
                outputs[step.step_id] = input_df
                source_by_step[step.step_id] = step_source_df
                continue

            if emitter:
                await emitter.emit_progress(
                    f"Running step {index}/{len(plan.steps)}...",
                    step_id="execution",
                )

            if input_df.empty:
                outputs[step.step_id] = input_df.copy()
                computed.append((step, outputs[step.step_id]))
                source_by_step[step.step_id] = step_source_df
                step.execution_report = (step.execution_report or ExecutionReport()).model_copy(
                    update={
                        "status": "empty",
                        "engine": step.route.value,
                        "source_bindings": step.source_bindings,
                        "source_rows": 0,
                        "result_rows": 0,
                        "operations": list(step.operation_intents),
                    }
                )
                continue

            if emitter:
                await emitter.emit_progress("Detecting field types...", step_id="semantic_typing")
            field_map = await self.semantic_skill.run(ctx, step_query, df=input_df)
            decisions = FieldResolver().decide_all(
                step_query,
                field_map,
                mentions=step.target_fields,
            )
            step.field_resolutions = [self._field_resolution_record(item) for item in decisions]
            execution_plan.field_resolutions.extend(step.field_resolutions)
            step.required_source_columns = [
                record.selected_column
                for record in step.field_resolutions
                if record.selected_column is not None
                and record.status in {"confirmed", "assumed"}
            ]
            for column in step.required_source_columns:
                if column not in required_columns:
                    required_columns.append(column)
            execution_plan.required_source_columns = list(required_columns)

            if any(record.status == "needs_clarification" for record in step.field_resolutions):
                failed_steps.append(step.step_id)
                step.execution_report = ExecutionReport(
                    status="failed",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    source_rows=len(input_df),
                    fields=step.required_source_columns,
                    error_code="field_clarification_required",
                    error_message="One or more fields require confirmation.",
                )
                continue

            normalized_types = self.type_normalizer.run(
                ctx,
                df=input_df,
                field_map=field_map,
            )
            input_df = normalized_types.df
            if normalized_types.warnings:
                logger.warning(
                    "[Pipeline] type normalization warnings for step=%s: %s",
                    step.step_id,
                    "; ".join(normalized_types.warnings),
                )

            if emitter:
                await emitter.emit_progress("Profiling the data...", step_id="data_profiling")
            data_summary = await self.profiling_skill.run(
                ctx,
                step_query,
                df=input_df,
                field_map=field_map,
            )

            result: Optional[pd.DataFrame] = None
            executed_operations = list(step.operation_intents)
            if step.route == RoutingHint.RULE_ENGINE:
                try:
                    rule_result = self.rule_engine.run(
                        ctx,
                        query=step_query,
                        df=input_df,
                        field_map=field_map,
                        required_columns=step.required_source_columns,
                        return_report=True,
                    )
                    validation = self.rule_result_validator.validate(
                        step=step,
                        source_df=input_df,
                        rule_result=rule_result,
                    )
                    if validation.valid:
                        result = rule_result.result_df
                        executed_operations = list(rule_result.matched_rules)
                    elif validation.fallback_to_codegen:
                        logger.warning(
                            "[Pipeline] rule result rejected for step=%s: %s",
                            step.step_id,
                            "; ".join(validation.reasons),
                        )
                        step.route = RoutingHint.CODE_GEN
                        execution_plan.route = RoutingHint.CODE_GEN
                        trace.add_event(
                            EVT_TOOL_END,
                            tool_name=self.rule_engine.name,
                            output_summary=f"step={step.step_id} rejected; fallback=code",
                            metadata={"reasons": validation.reasons},
                        )
                except Exception as exc:
                    logger.warning(
                        "[Pipeline] rule step %s fell back to CODE_GEN: %s",
                        step.step_id,
                        exc,
                    )
                    step.route = RoutingHint.CODE_GEN
                    execution_plan.route = RoutingHint.CODE_GEN

            if step.route == RoutingHint.CODE_GEN and result is None:
                if emitter:
                    async def emit_fn(message: str) -> None:
                        await emitter.emit_progress(message, step_id="execution")
                else:
                    emit_fn = None
                result, _code, _repairs = await self.repair_loop.run(
                    ctx=ctx,
                    query=step_query,
                    df=input_df,
                    data_summary=data_summary,
                    field_map=field_map,
                    wants_chart="chart" in step.output_intents,
                    is_compound=False,
                    required_columns=step.required_source_columns,
                    semantics=step.semantics,
                    trace=trace,
                    emit_progress=emit_fn,
                )

            if result is None:
                failed_steps.append(step.step_id)
                step.execution_report = ExecutionReport(
                    status="failed",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    source_rows=len(input_df),
                    fields=step.required_source_columns,
                    operations=list(step.operation_intents),
                    error_code="execution_failed",
                    error_message="The selected executor could not produce a valid result.",
                )
                continue

            contract = self.execution_contract_validator.validate(
                step=step,
                source_df=input_df,
                result_df=result,
            )
            if not contract.valid:
                failed_steps.append(step.step_id)
                step.execution_report = ExecutionReport(
                    status="failed",
                    engine=step.route.value,
                    source_bindings=step.source_bindings,
                    source_rows=len(input_df),
                    result_rows=len(result),
                    fields=step.required_source_columns,
                    operations=executed_operations,
                    error_code="semantic_contract_failed",
                    error_message="; ".join(contract.reasons),
                )
                continue

            outputs[step.step_id] = result
            source_by_step[step.step_id] = step_source_df
            inherited_scope: Dict[str, List[str]] = {}
            if step.scope_from == "previous_result":
                inherited_scope = self._merge_scope_filters(
                    previous_scope,
                    previous_result_scope,
                )
            elif step.scope_from == "previous_filters":
                inherited_scope = previous_scope
            elif step.depends_on:
                inherited_scope = output_scopes.get(step.depends_on[0], {})
            scope_filters = self._merge_scope_filters(
                inherited_scope,
                self._extract_mentioned_scope_filters(step_query, input_df),
            )
            result_filters = self._scope_filters_from_result(result)
            output_scopes[step.step_id] = self._merge_scope_filters(
                scope_filters,
                result_filters,
            )
            ctx.active_lineage = ResultLineage(
                source_scope=(
                    AnalysisContext.scope_key([
                        {
                            **({"fileId": binding.file_id} if binding.file_id else {}),
                            "fileName": binding.file_name,
                            "sheets": [binding.sheet_name],
                        }
                        for binding in step.source_bindings
                    ])
                    if step.source_bindings
                    else ctx.active_source_scope or ctx.requested_scope_key()
                ),
                filters=scope_filters,
                result_filters=result_filters,
                result_columns=[str(column) for column in result.columns],
            )
            computed.append((step, result))
            step.execution_report = (step.execution_report or ExecutionReport()).model_copy(
                update={
                    "status": "empty" if result.empty else "success",
                    "engine": step.route.value,
                    "source_bindings": step.source_bindings,
                    "source_rows": len(input_df),
                    "result_rows": len(result),
                    "fields": list(step.required_source_columns),
                    "operations": executed_operations,
                }
            )
            if step.source_bindings:
                selected_scope = [
                    {
                        **({"fileId": binding.file_id} if binding.file_id else {}),
                        "fileName": binding.file_name,
                        "sheets": [binding.sheet_name],
                    }
                    for binding in step.source_bindings
                ]
                ctx.active_source_scope = AnalysisContext.scope_key(selected_scope)
                ctx.selected_sheets = [binding.sheet_name for binding in step.source_bindings]
                ctx._source_df = step_source_df
                ctx.source_scope_changed = False
            self._remember_result_dataframe(ctx, input_df=input_df, result_df=result)

        execution_plan.required_source_columns = required_columns
        consumed_by_computation = {
            dependency
            for step in plan.steps
            if step.needs_new_computation
            for dependency in step.depends_on
        }
        visible = [item for item in computed if item[0].step_id not in consumed_by_computation]
        final_result = computed[-1][1] if computed else None
        return final_result, visible, outputs, failed_steps

    @staticmethod
    def _step_input_dataframe(
        step: ExecutionStep,
        outputs: Dict[str, pd.DataFrame],
        source_df: Optional[pd.DataFrame],
        previous_result_df: Optional[pd.DataFrame],
        previous_scope_filters: Optional[Dict[str, List[str]]] = None,
        previous_result_filters: Optional[Dict[str, List[str]]] = None,
        output_scopes: Optional[Dict[str, Dict[str, List[str]]]] = None,
    ) -> Optional[pd.DataFrame]:
        if len(step.depends_on) > 1:
            return None
        if step.depends_on:
            dependency_df = outputs.get(step.depends_on[0])
            if (
                dependency_df is not None
                and step.needs_new_computation
                and source_df is not None
                and SheetMindAgent._requires_detail_rows(step, source_df, dependency_df)
            ):
                dependency_scope = (output_scopes or {}).get(step.depends_on[0], {})
                if dependency_scope:
                    return SheetMindAgent._apply_scope_filters(
                        source_df,
                        dependency_scope,
                    )
                return SheetMindAgent._apply_previous_result_scope(
                    source_df,
                    dependency_df,
                )
            return dependency_df
        if step.input_source == "active_dataframe":
            if step.scope_from in {"previous_result", "previous_filters"}:
                persisted_scope = dict(previous_scope_filters or {})
                if step.scope_from == "previous_result":
                    persisted_scope = SheetMindAgent._merge_scope_filters(
                        persisted_scope,
                        previous_result_filters or {},
                    )
                if persisted_scope:
                    return SheetMindAgent._apply_scope_filters(
                        source_df,
                        persisted_scope,
                    ) if source_df is not None else previous_result_df
                if previous_result_df is None:
                    return source_df
                return SheetMindAgent._apply_previous_result_scope(
                    source_df,
                    previous_result_df,
                ) if source_df is not None else previous_result_df
            return source_df
        if step.input_source == "previous_result" and previous_result_df is not None:
            if (
                step.needs_new_computation
                and source_df is not None
                and SheetMindAgent._requires_detail_rows(
                    step,
                    source_df,
                    previous_result_df,
                )
            ):
                persisted_scope = SheetMindAgent._merge_scope_filters(
                    previous_scope_filters or {},
                    previous_result_filters or {},
                )
                if persisted_scope:
                    return SheetMindAgent._apply_scope_filters(
                        source_df,
                        persisted_scope,
                    )
                return SheetMindAgent._apply_previous_result_scope(
                    source_df,
                    previous_result_df,
                )
            return previous_result_df
        return source_df

    @staticmethod
    def _apply_previous_result_scope(
        source_df: pd.DataFrame,
        previous_result_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Use categorical values from the previous result as filters on source_df."""
        if source_df.empty or previous_result_df.empty:
            return source_df

        scoped = source_df
        source_lookup: Dict[str, List[str]] = {}
        for column in source_df.columns:
            source_lookup.setdefault(
                SheetMindAgent._scope_column_key(str(column)), []
            ).append(str(column))
        for prev_column in previous_result_df.columns:
            values = previous_result_df[prev_column].dropna().astype(str).unique().tolist()
            if not values or len(values) > 50:
                continue
            if pd.api.types.is_numeric_dtype(previous_result_df[prev_column]):
                continue

            key = SheetMindAgent._scope_column_key(str(prev_column))
            source_column = str(prev_column) if str(prev_column) in source_df.columns else None
            if source_column is None:
                exact_candidates = source_lookup.get(key, [])
                if len(exact_candidates) == 1:
                    source_column = exact_candidates[0]
            if source_column is None:
                fuzzy_candidates = [
                    candidate
                    for source_key, candidates in source_lookup.items()
                    if key and (key in source_key or source_key in key)
                    for candidate in candidates
                ]
                if len(fuzzy_candidates) == 1:
                    source_column = fuzzy_candidates[0]
            if source_column is None:
                continue

            mask = scoped[source_column].astype(str).isin(values)
            scoped = scoped[mask]

        return scoped

    @staticmethod
    def _apply_scope_filters(
        source_df: pd.DataFrame,
        filters: Dict[str, List[str]],
    ) -> pd.DataFrame:
        """Apply persisted lineage predicates using exact, unambiguous columns."""
        scoped = source_df
        normalized_columns: Dict[str, List[str]] = {}
        for column in source_df.columns:
            normalized_columns.setdefault(
                normalise_field_text(str(column)), []
            ).append(str(column))

        for requested_column, values in filters.items():
            source_column = requested_column if requested_column in source_df.columns else None
            if source_column is None:
                candidates = normalized_columns.get(
                    normalise_field_text(requested_column), []
                )
                if len(candidates) == 1:
                    source_column = candidates[0]
            if source_column is None or not values:
                continue
            allowed = {str(value) for value in values}
            scoped = scoped[scoped[source_column].astype(str).isin(allowed)]
        return scoped

    @staticmethod
    def _extract_mentioned_scope_filters(
        query: str,
        df: pd.DataFrame,
    ) -> Dict[str, List[str]]:
        filters: Dict[str, List[str]] = {}
        for column in df.columns:
            if pd.api.types.is_numeric_dtype(df[column]):
                continue
            matches = [
                value
                for value in df[column].dropna().astype(str).drop_duplicates().head(1000)
                if len(value.strip()) >= 2 and value.strip() in query
            ]
            if matches:
                filters[str(column)] = matches
        return filters

    @staticmethod
    def _scope_filters_from_result(df: pd.DataFrame) -> Dict[str, List[str]]:
        if df.empty or len(df) > 50:
            return {}
        return {
            str(column): df[column].dropna().astype(str).drop_duplicates().tolist()
            for column in df.columns
            if not pd.api.types.is_numeric_dtype(df[column])
            and not df[column].dropna().empty
        }

    @staticmethod
    def _merge_scope_filters(
        *groups: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        merged: Dict[str, List[str]] = {}
        for group in groups:
            for column, values in group.items():
                merged[str(column)] = list(dict.fromkeys(str(value) for value in values))
        return merged

    @staticmethod
    def _scope_column_key(column: str) -> str:
        return normalise_field_text(str(column))

    @staticmethod
    def _requires_detail_rows(
        step: ExecutionStep,
        source_df: pd.DataFrame,
        candidate_df: pd.DataFrame,
    ) -> bool:
        """Whether a computation needs source rows absent from an aggregate."""
        query_text = normalise_field_text(step.query)
        source_columns = [str(column) for column in source_df.columns]
        candidate_columns = [str(column) for column in candidate_df.columns]

        explicitly_requested = [
            column
            for column in source_columns
            if len(normalise_field_text(column)) >= 2
            and normalise_field_text(column) in query_text
        ]
        if any(column not in candidate_columns for column in explicitly_requested):
            return True

        return (
            SheetMindAgent._targets_present(source_df, step.target_fields)
            and not SheetMindAgent._targets_present(candidate_df, step.target_fields)
        )

    @staticmethod
    def _targets_present(df: pd.DataFrame, target_fields: List[str]) -> bool:
        """Return True when all requested semantic targets appear in df columns."""
        targets = [
            normalise_field_text(field)
            for field in target_fields
            if normalise_field_text(field)
        ]
        if not targets:
            return True
        columns = [
            normalise_field_text(str(column))
            for column in df.columns
            if normalise_field_text(str(column))
        ]
        return all(
            any(target == column or target in column or column in target for column in columns)
            for target in targets
        )

    @staticmethod
    def _build_execution_plan(
        plan: QueryPlan,
        routing: RoutingResult,
    ) -> ExecutionPlan:
        def unique(values: List[str]) -> List[str]:
            return list(dict.fromkeys(values))

        routes = [step.route for step in plan.steps]
        if RoutingHint.CODE_GEN in routes:
            primary_route = RoutingHint.CODE_GEN
        elif RoutingHint.RULE_ENGINE in routes:
            primary_route = RoutingHint.RULE_ENGINE
        else:
            primary_route = RoutingHint.INSIGHT_ONLY

        operation_intents = unique([
            operation
            for step in plan.steps
            for operation in step.operation_intents
        ]) or list(routing.operation_intent.types)
        output_intents = unique([
            output
            for step in plan.steps
            for output in step.output_intents
        ])
        if not output_intents:
            output_intents = list(routing.output_intent.formats)
        target_fields = unique([
            target
            for step in plan.steps
            for target in step.target_fields
        ]) or list(routing.target_fields)

        return ExecutionPlan(
            route=primary_route,
            mode=plan.mode or routing.mode,
            response_language=(
                plan.response_language
                or infer_response_language(
                    routing.normalized_query.original_text
                    if routing.normalized_query is not None
                    else ""
                )
            ),
            original_query=(
                routing.normalized_query.original_text
                if routing.normalized_query is not None
                else ""
            ),
            normalized_query=(
                routing.normalized_query.normalized_text
                if routing.normalized_query is not None
                else ""
            ),
            operation_intents=operation_intents,
            output_intents=output_intents,
            output_explicit=(
                routing.output_intent.explicit
                or any(step.output_explicit for step in plan.steps)
            ),
            needs_new_computation=any(step.needs_new_computation for step in plan.steps),
            uses_previous_result=(
                (plan.mode or routing.mode) == MultiTurnMode.FOLLOW_UP
                or any(
                    step.input_source in {"active_dataframe", "previous_result"}
                    for step in plan.steps
                )
            ),
            target_fields=target_fields,
            confidence=plan.confidence,
            steps=plan.steps,
            planner_used=plan.source != "single",
            planner_source=plan.source,
            reasoning=plan.reasoning,
        )

    @staticmethod
    def _trace_execution_plan(
        trace: Trace,
        execution_plan: ExecutionPlan,
        routing: RoutingResult,
    ) -> None:
        trace.add_event(
            EVT_ROUTING,
            output_summary=(
                f"hint={execution_plan.route.value} mode={execution_plan.mode.value} "
                f"conf={execution_plan.confidence:.2f} steps={len(execution_plan.steps)}"
            ),
            metadata={
                "reasoning": routing.reasoning,
                "structure": {
                    "requires_planning": routing.structure.requires_planning,
                    "needs_semantic_planning": routing.structure.needs_semantic_planning,
                    "classification": routing.structure.classification,
                    "score": routing.structure.score,
                    "signals": routing.structure.signals,
                },
                "operation_intent": routing.operation_intent.types,
                "output_intent": routing.output_intent.formats,
                "normalized_query": (
                    routing.normalized_query.model_dump()
                    if routing.normalized_query is not None
                    else None
                ),
                "execution_plan": execution_plan.model_dump(mode="json"),
            },
        )

    # ------------------------------------------------------------------
    # Field resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _field_resolution_record(decision: Any) -> FieldResolutionRecord:
        selected = decision.selected
        confidence = (
            0.50
            if decision.status == "needs_clarification"
            else selected.confidence if selected is not None else 0.0
        )
        return FieldResolutionRecord(
            reference=decision.reference,
            status=decision.status,
            selected_column=selected.column if selected is not None else None,
            confidence=confidence,
            reason=decision.reason,
            candidates=[
                FieldCandidate(
                    column=candidate.column,
                    confidence=candidate.confidence,
                    reason=candidate.reason,
                )
                for candidate in decision.candidates
            ],
        )

    @staticmethod
    def _field_resolution_blocks(
        records: List[FieldResolutionRecord],
        *,
        status: str,
        response_language: ResponseLanguage = "en",
    ) -> List[FieldResolutionBlock]:
        blocks: List[FieldResolutionBlock] = []
        seen: set[tuple[str, str]] = set()
        for record in records:
            if record.status != status:
                continue
            key = (record.reference, record.status)
            if key in seen:
                continue
            seen.add(key)
            if status == "needs_clarification":
                message = user_text(
                    response_language,
                    en=f"\"{record.reference}\" may match multiple fields. Choose one to continue.",
                    zh=f"“{record.reference}”可能对应多个字段，请选择一个后继续。",
                )
            else:
                message = user_text(
                    response_language,
                    en=f"Assumed column \"{record.selected_column}\" for this run ({record.reason}).",
                    zh=f"本次分析使用字段“{record.selected_column}”（{record.reason}）。",
                )
            blocks.append(FieldResolutionBlock(
                reference=record.reference,
                status=record.status,
                selected_column=record.selected_column,
                confidence=record.confidence,
                reason=record.reason,
                candidates=record.candidates,
                message=message,
            ))
        return blocks

    @staticmethod
    def _sheet_resolution_block(
        ctx: AnalysisContext,
        decision: SheetSelectionDecision,
        *,
        response_language: ResponseLanguage = "en",
    ) -> SheetResolutionBlock:
        current_sheets = [
            str(sheet)
            for scope in ctx.requested_sheet_scope
            for sheet in scope.get("sheets", [])
        ] or list(ctx.selected_sheets)
        if decision.reason == "no sheets are checked for analysis":
            message = user_text(
                response_language,
                en=(
                    "No data sheets are selected. "
                    "Select at least one sheet in Import Your Data, then submit the question again."
                ),
                zh="尚未选择数据表。请在 Import Your Data 中至少勾选一个 Sheet，然后重新提交问题。",
            )
        elif decision.status == "scope_conflict":
            candidate = decision.candidates[0]
            message = user_text(
                response_language,
                en=(
                    "The selected scope does not contain the best data source. "
                    f"\"{candidate.file_name} / {candidate.sheet_name}\" is a better match. "
                    "Select that sheet in Import Your Data and submit the question again. SheetMind will not read unselected data."
                ),
                zh=(
                    "当前勾选范围不包含最匹配的数据来源。"
                    f"“{candidate.file_name} / {candidate.sheet_name}”与问题更匹配。"
                    "请在 Import Your Data 中勾选该 Sheet 后重新提交；SheetMind 不会读取未勾选的数据。"
                ),
            )
        else:
            message = user_text(
                response_language,
                en=(
                    "Multiple selected data sources could answer this question. "
                    "Keep only the intended files and sheets selected in Import Your Data, "
                    "or include the file and sheet name in the question before submitting again."
                ),
                zh=(
                    "多个已勾选的数据来源都可能回答这个问题。"
                    "请在 Import Your Data 中只保留需要的文件和 Sheet，"
                    "或在问题中明确文件名和 Sheet 名后重新提交。"
                ),
            )
        return SheetResolutionBlock(
            status=decision.status,
            message=message,
            current_sheets=current_sheets,
            candidates=[
                SheetCandidate(
                    candidate_id=candidate.candidate_id,
                    file_id=candidate.file_id or None,
                    file_name=candidate.file_name,
                    sheet_name=candidate.sheet_name,
                    columns=candidate.columns[:12],
                    confidence=candidate.score,
                    reason=candidate.reason,
                )
                for candidate in decision.candidates
            ],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _query_targets_previous_result(query: str) -> bool:
        """Return True when the user explicitly wants to reuse the prior result."""
        q_lower = query.lower()
        refs = [
            "上面", "上述", "上方", "前面", "刚才", "上一轮", "上个",
            "基于", "在此基础", "这个结果", "这些结果", "当前结果",
            "previous result", "above result", "based on",
        ]
        return any(ref in q_lower for ref in refs)

    @staticmethod
    def _can_reuse_previous_result_for_chart(
        *,
        ctx: AnalysisContext,
        query: str,
        plan: QueryPlan,
        previous_result_df: Optional[pd.DataFrame],
        wants_chart: bool,
        explicit_source_switch: bool,
    ) -> bool:
        """Recognize chart-only follow-ups even when planning labels them new."""
        if (
            not wants_chart
            or explicit_source_switch
            or ctx.source_scope_changed
            or plan.mode == MultiTurnMode.RESET
            or previous_result_df is None
            or previous_result_df.empty
            or len(plan.steps) != 1
        ):
            return False

        step = plan.steps[0]
        operations = set(step.operation_intents)
        if not operations.issubset({"chart_data_prep", "trend", "general"}):
            return False

        output_intents = set(step.output_intents)
        if output_intents and not output_intents.issubset({"chart", "insight", "auto"}):
            return False

        columns = [str(column) for column in previous_result_df.columns]
        if len(columns) < 2 or not any(
            pd.api.types.is_numeric_dtype(previous_result_df[column])
            for column in previous_result_df.columns
        ):
            return False

        normalized_columns = [normalise_field_text(column) for column in columns]
        numeric_columns = [
            str(column)
            for column in previous_result_df.columns
            if pd.api.types.is_numeric_dtype(previous_result_df[column])
        ]
        lineage_filters = (
            ctx.active_lineage.filters
            if ctx.active_lineage is not None
            else {}
        )
        normalized_query = normalise_field_text(query)

        scope_aliases = {
            "平台": ("平台", "渠道"),
            "店铺": ("店铺", "门店"),
            "仓库": ("仓库",),
            "物流商": ("物流商", "承运商"),
            "费用类型": ("费用类型", "尾程", "仓储"),
        }
        for column, values in lineage_filters.items():
            normalized_column = normalise_field_text(column)
            aliases = scope_aliases.get(
                normalized_column,
                (normalized_column,),
            )
            if not any(
                normalise_field_text(alias) in normalized_query
                for alias in aliases
                if normalise_field_text(alias)
            ):
                continue
            if not any(
                normalise_field_text(value) in normalized_query
                for value in values
                if normalise_field_text(value)
            ):
                return False

        def covered_by_result(target: str) -> bool:
            normalized_target = normalise_field_text(target)
            if not normalized_target:
                return True
            if any(
                normalized_target == column
                or normalized_target in column
                or column in normalized_target
                for column in normalized_columns
            ):
                return True

            metric_terms = ("费用", "花费", "金额", "cost", "fee")
            return (
                len(numeric_columns) == 1
                and any(term in normalized_target for term in metric_terms)
            )

        def covered_by_active_scope(target: str) -> bool:
            normalized_target = normalise_field_text(target)
            for column, values in lineage_filters.items():
                normalized_column = normalise_field_text(column)
                if not (
                    normalized_target == normalized_column
                    or normalized_target in normalized_column
                    or normalized_column in normalized_target
                ):
                    continue
                return any(
                    normalise_field_text(value) in normalized_query
                    for value in values
                    if normalise_field_text(value)
                )
            return False

        return all(
            covered_by_result(target) or covered_by_active_scope(target)
            for target in step.target_fields
        )

    @staticmethod
    def _previous_result_dataframe(ctx: AnalysisContext) -> Optional[pd.DataFrame]:
        """Rebuild the previous tabular result as a DataFrame when available."""
        if isinstance(ctx._result_df, pd.DataFrame) and not ctx._result_df.empty:
            return ctx._result_df

        result = ctx.last_tabular_result()
        if result is None:
            return None

        table = result.focused_table()
        if table is None or not table.rows or not table.columns:
            return None

        if table.artifact_id:
            artifact_df = get_artifact_store().load(table.artifact_id, ctx.task_id)
            if artifact_df is not None:
                return artifact_df

        try:
            return pd.DataFrame(table.rows, columns=table.columns)
        except Exception:
            return None

    async def _run_previous_result_chart(
        self,
        ctx: AnalysisContext,
        query: str,
        previous_result_df: pd.DataFrame,
        hint: RoutingHint,
        mode: MultiTurnMode,
        emitter: Optional[StreamEmitter],
        response_language: ResponseLanguage = "en",
    ) -> tuple:
        """Render a chart from the last table result without rerunning codegen."""
        if emitter:
            await emitter.emit_progress("Building a chart from the previous result...", step_id="chart_planning")

        field_map = await self.semantic_skill.run(ctx, query, df=previous_result_df)
        step = (
            ctx.execution_plan.steps[0]
            if ctx.execution_plan and ctx.execution_plan.steps
            else None
        )
        decisions = FieldResolver().decide_all(
            query,
            field_map,
            mentions=step.target_fields if step else None,
        )
        records = [self._field_resolution_record(item) for item in decisions]
        required_columns = [
            record.selected_column
            for record in records
            if record.selected_column is not None
            and record.status in {"confirmed", "assumed"}
        ]
        if step is not None:
            step.field_resolutions = records
            step.required_source_columns = required_columns
        if ctx.execution_plan is not None:
            ctx.execution_plan.field_resolutions = records
            ctx.execution_plan.required_source_columns = required_columns
        clarification_blocks = self._field_resolution_blocks(
            records,
            status="needs_clarification",
            response_language=response_language,
        )
        if clarification_blocks:
            question = QuestionResult(
                question_id=step.question_id if step else "q1",
                query=query,
                status="needs_input",
                blocks=clarification_blocks,
            )
            result = ResultBlocks(
                status="needs_input",
                response_language=response_language,
                focus_question_id=question.question_id,
                output_intents=["chart"],
                questions=[question],
                blocks=clarification_blocks,
            )
            return validate_result(result), hint, mode

        chart_block = await self.chart_skill.run(
            ctx,
            query,
            result_df=previous_result_df,
            field_map=field_map,
            required_columns=required_columns,
        )
        if chart_block is None:
            status_block = StatusBlock(
                status="failed",
                message=user_text(
                    response_language,
                    en="The previous result does not contain drawable dimension or numeric fields.",
                    zh="上一轮结果不包含可用于绘图的维度或数值字段。",
                ),
                error_code="chart_not_supported",
            )
            question = QuestionResult(
                question_id=step.question_id if step else "q1",
                query=query,
                status="failed",
                blocks=[status_block],
            )
            result = ResultBlocks(
                status="failed",
                response_language=response_language,
                focus_question_id=question.question_id,
                output_intents=["chart"],
                questions=[question],
                blocks=[status_block],
            )
            return result, hint, mode

        if emitter:
            await emitter.emit_progress("Writing analysis insights...", step_id="insight_writing")

        question_id = step.question_id if step else "q1"
        artifact_id = self.artifact_store.save(
            ctx.task_id,
            question_id,
            previous_result_df,
        )
        table_block = self._build_table_block(
            previous_result_df,
            artifact_id=artifact_id,
        )
        if table_block is not None:
            table_block.title = query
            basis_step = step or ExecutionStep(
                step_id="previous_result_chart",
                query=query,
                route=hint,
                input_source="previous_result",
                operation_intents=["chart_data_prep"],
                output_intents=["chart"],
                needs_new_computation=False,
                required_source_columns=required_columns,
            )
            table_block.calculation_basis = self._build_calculation_basis(
                ctx,
                basis_step,
                previous_result_df,
                from_previous_result=True,
            )

        summary_text = await self.insight_skill.run(
            ctx,
            query,
            scenario="chart",
            result_df=previous_result_df,
            chart_block=chart_block,
            table_block=table_block,
            response_language=response_language,
        )

        blocks: List[Any] = self._field_resolution_blocks(
            records,
            status="assumed",
            response_language=response_language,
        )
        if summary_text:
            blocks.append(SummaryBlock(content=summary_text))
        if table_block is not None:
            blocks.append(table_block)
        blocks.append(chart_block)

        report = ExecutionReport(
            status="success",
            engine="insight",
            source_bindings=step.source_bindings if step else [],
            source_rows=len(previous_result_df),
            result_rows=len(previous_result_df),
            fields=required_columns,
            operations=["chart_data_prep"],
        )
        if step is not None:
            step.execution_report = report
        question = QuestionResult(
            question_id=question_id,
            query=query,
            status="success",
            blocks=blocks,
            execution_report=report,
        )
        result = ResultBlocks(
            status="success",
            response_language=response_language,
            focus_question_id=question_id,
            output_intents=["chart"],
            questions=[question],
            blocks=blocks,
        )
        return validate_result(result, degrade_invalid_charts=True), hint, mode

    @staticmethod
    def _remember_result_dataframe(
        ctx: AnalysisContext,
        input_df: Optional[pd.DataFrame],
        result_df: pd.DataFrame,
    ) -> None:
        """
        Keep the last result available without losing the richer source rows.

        If an operation returned the same columns as the input (typical filters,
        sorting, and pass-through queries), the result becomes the active working
        DataFrame for follow-up refinement. Aggregations and Top-N outputs are
        stored as `_result_df` but do not replace `_active_df`, so later turns can
        still ask for deeper breakdowns using the original rows.
        """
        ctx._result_df = result_df

        if input_df is None:
            ctx._active_df = result_df
            return

        if list(result_df.columns) == list(input_df.columns):
            ctx._active_df = result_df

    @staticmethod
    def _build_table_block(
        df: pd.DataFrame,
        artifact_id: Optional[str] = None,
    ) -> Optional[TableBlock]:
        """Convert a result DataFrame into a TableBlock."""
        if df is None or df.empty:
            return None

        # Truncate large results for frontend
        display_df = df.head(_MAX_TABLE_ROWS)

        columns = list(display_df.columns)
        rows: List[Dict[str, Any]] = []
        for _, row in display_df.iterrows():
            row_dict: Dict[str, Any] = {}
            for col in columns:
                val = row[col]
                # Convert numpy/pandas scalars to Python native types
                if pd.isna(val) if not isinstance(val, (list, dict)) else False:
                    row_dict[col] = None
                elif hasattr(val, "item"):
                    # numpy scalar → Python native
                    row_dict[col] = val.item()
                else:
                    row_dict[col] = val
            rows.append(row_dict)

        # Build column metadata
        col_meta: List[ColumnMeta] = []
        for col in columns:
            if pd.api.types.is_numeric_dtype(display_df[col]):
                ctype = "numeric"
                # Detect decimal places for floats
                decimal_places = None
                if pd.api.types.is_float_dtype(display_df[col]):
                    decimal_places = 2
                col_meta.append(ColumnMeta(
                    name=col, type=ctype, decimal_places=decimal_places
                ))
            elif pd.api.types.is_datetime64_any_dtype(display_df[col]):
                col_meta.append(ColumnMeta(name=col, type="datetime"))
            else:
                col_meta.append(ColumnMeta(name=col, type="categorical"))

        return TableBlock(
            columns=columns,
            rows=rows,
            total_rows=len(df),
            preview_row_count=len(display_df),
            columns_metadata=col_meta,
            artifact_id=artifact_id,
        )

    @staticmethod
    def _build_calculation_basis(
        ctx: AnalysisContext,
        step: ExecutionStep,
        result_df: pd.DataFrame,
        *,
        from_previous_result: bool = False,
    ) -> CalculationBasis:
        """Describe reproducible inputs and operations without exposing model reasoning."""
        response_language: ResponseLanguage = (
            ctx.execution_plan.response_language
            if ctx.execution_plan is not None
            else "en"
        )
        source_sheets: List[str] = []
        raw_scope: List[Dict[str, Any]] = [
            {
                **({"fileId": binding.file_id} if binding.file_id else {}),
                "fileName": binding.file_name,
                "sheets": [binding.sheet_name],
            }
            for binding in step.source_bindings
        ]
        if not raw_scope and ctx.active_source_scope:
            try:
                parsed_scope = json.loads(ctx.active_source_scope)
                if isinstance(parsed_scope, list):
                    raw_scope = parsed_scope
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_scope = []
        if not raw_scope:
            raw_scope = ctx.requested_sheet_scope

        for item in raw_scope:
            file_name = str(item.get("fileName", "")).strip()
            for sheet in item.get("sheets", []):
                sheet_name = str(sheet).strip()
                label = f"{file_name} / {sheet_name}" if file_name else sheet_name
                if label and label not in source_sheets:
                    source_sheets.append(label)
        if not source_sheets:
            source_sheets = list(dict.fromkeys(str(item) for item in ctx.selected_sheets))

        report = step.execution_report
        fields: List[str] = []
        for column in [
            *(report.fields if report else []),
            *step.required_source_columns,
            *map(str, result_df.columns),
        ]:
            if column and column not in fields:
                fields.append(column)
        fields = fields[:12]

        operation_labels = (
            {
                "filter": "筛选",
                "keyword_filter": "关键词筛选",
                "date_filter": "日期筛选",
                "aggregate": "分组汇总",
                "aggregation": "分组汇总",
                "groupby": "分组汇总",
                "which": "分组比较",
                "extreme": "极值比较",
                "sort": "排序",
                "top_n": "Top N 筛选",
                "trend": "趋势计算",
                "pivot": "透视汇总",
                "compare": "比较",
                "chart_data_prep": "图表数据整理",
                "complex_transform": "数据处理",
            }
            if response_language == "zh"
            else {
                "filter": "filtering",
                "keyword_filter": "filtering",
                "date_filter": "date filtering",
                "aggregate": "grouped aggregation",
                "aggregation": "grouped aggregation",
                "groupby": "grouped aggregation",
                "which": "group comparison",
                "extreme": "extreme value comparison",
                "sort": "sorting",
                "top_n": "Top N filtering",
                "trend": "trend calculation",
                "pivot": "pivot-style aggregation",
                "compare": "comparison",
                "chart_data_prep": "chart data preparation",
                "complex_transform": "data processing",
            }
        )
        operations: List[str] = []
        executed_operations = report.operations if report and report.operations else step.operation_intents
        for intent in executed_operations:
            label = operation_labels.get(intent, intent.replace("_", " "))
            if label and label not in operations:
                operations.append(label)
        if from_previous_result:
            operations.insert(
                0,
                "复用上一轮结果" if response_language == "zh" else "previous result reuse",
            )
        operations = list(dict.fromkeys(operations)) or [
            "数据处理" if response_language == "zh" else "data processing"
        ]

        source_text = ", ".join(f"\"{item}\"" for item in source_sheets) or user_text(
            response_language,
            en="the selected sheets",
            zh="已勾选的 Sheet",
        )
        field_text = ", ".join(f"\"{item}\"" for item in fields) or user_text(
            response_language,
            en="result fields",
            zh="结果字段",
        )
        operation_text = ", ".join(operations)
        source_row_count = report.source_rows if report is not None else None
        result_row_count = len(result_df)
        row_text = user_text(
            response_language,
            en=(
                f" Read {source_row_count:,} source rows and produced "
                f"{result_row_count:,} result rows."
                if source_row_count is not None
                else f" Produced {result_row_count:,} result rows."
            ),
            zh=(
                f"读取 {source_row_count:,} 行源数据，得到 {result_row_count:,} 行计算结果。"
                if source_row_count is not None
                else f"得到 {result_row_count:,} 行计算结果。"
            ),
        )
        return CalculationBasis(
            source_sheets=source_sheets,
            fields=fields,
            operations=operations,
            source_row_count=source_row_count,
            result_row_count=result_row_count,
            summary=user_text(
                response_language,
                en=f"Based on {source_text}, using {field_text}, with {operation_text}.{row_text}",
                zh=f"基于 {source_text}，使用 {field_text}，执行了 {operation_text}。{row_text}",
            ),
        )

    @staticmethod
    def _extract_summary(result: ResultBlocks) -> str:
        """Extract the first summary block's content for Turn.content."""
        summaries = result.all_summaries()
        return summaries[0] if summaries else ""
