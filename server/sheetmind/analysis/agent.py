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

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .context import (
    AnalysisContext,
    ChartBlock,
    ColumnMeta,
    ExecutionPlan,
    ExecutionStep,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
    SummaryBlock,
    TableBlock,
)
from .harness.repair_loop import RepairLoop
from .models.router import ModelRouter
from .skills.chart_planning import ChartPlanningSkill
from .skills.code_generation import CodeGenerationSkill
from .skills.data_profiling import DataProfilingSkill
from .skills.field_resolution import FieldResolver, query_qualifiers
from .skills.insight_writing import InsightWritingSkill
from .skills.query_planning import QueryPlan, QueryPlanningSkill
from .skills.routing_classification import (
    RoutingClassificationSkill,
    RoutingResult,
)
from .skills.semantic_typing import SemanticFieldMap, SemanticTypingSkill
from .skills.sheet_selection import SheetSelectionSkill
from .streaming.emitter import StreamEmitter
from .tools.dataframe_loader import DataframeLoaderTool
from .tools.python_executor import PythonExecutorTool
from .tools.rule_engine import RuleEngineTool
from .tracing.storage import get_trace_store
from .tracing.trace import EVT_ERROR, EVT_RESULT_ASSEMBLED, EVT_ROUTING, Trace
from .validators.result_validator import validate_result

logger = logging.getLogger(__name__)

# Maximum rows in TableBlock.rows returned to frontend
_MAX_TABLE_ROWS = 1000


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

        # Instantiate all skills and tools once (reusable, stateless)
        self.routing_skill = RoutingClassificationSkill(self.router)
        self.planning_skill = QueryPlanningSkill(self.router, self.routing_skill)
        self.sheet_skill = SheetSelectionSkill(self.router)
        self.semantic_skill = SemanticTypingSkill(self.router)
        self.profiling_skill = DataProfilingSkill(self.router)
        self.code_gen_skill = CodeGenerationSkill(self.router)
        self.chart_skill = ChartPlanningSkill(self.router)
        self.insight_skill = InsightWritingSkill(self.router)

        self.df_loader = DataframeLoaderTool()
        self.rule_engine = RuleEngineTool()
        self.executor = PythonExecutorTool()

        self.repair_loop = RepairLoop(self.code_gen_skill, self.executor)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        emitter: Optional[StreamEmitter] = None,
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

        try:
            if emitter:
                await emitter.emit_thinking()

            result, routing_hint, multiturn_mode = await self._run_pipeline(
                ctx, query, trace, emitter
            )

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
        # ----------------------------------------------------------------
        # 1. Routing classification
        # ----------------------------------------------------------------
        if emitter:
            await emitter.emit_progress("正在理解您的问题...", step_id="routing")

        routing = await self.routing_skill.run(ctx, query)
        hint: RoutingHint = routing.hint
        mode: MultiTurnMode = routing.mode
        wants_chart = hint != RoutingHint.INSIGHT_ONLY and routing.facets.wants_chart

        previous_result_df = self._previous_result_dataframe(ctx)
        if (
            mode == MultiTurnMode.FOLLOW_UP
            and not routing.structure.needs_semantic_planning
            and wants_chart
            and self._query_targets_previous_result(query)
            and previous_result_df is not None
            and not previous_result_df.empty
        ):
            shortcut_plan = QueryPlan(
                steps=[QueryPlanningSkill._single_step(query, routing)],
                confidence=routing.confidence,
                reasoning="Direct presentation from the previous result.",
            )
            ctx.execution_plan = self._build_execution_plan(shortcut_plan, routing, wants_chart)
            self._trace_execution_plan(trace, ctx.execution_plan, routing)
            return await self._run_previous_result_chart(
                ctx=ctx,
                query=query,
                previous_result_df=previous_result_df,
                hint=hint,
                mode=mode,
                emitter=emitter,
            )

        if routing.structure.needs_semantic_planning and emitter:
            await emitter.emit_progress("正在识别问题结构...", step_id="query_planning")
        query_plan = await self.planning_skill.run(ctx, query, routing=routing)
        execution_plan = self._build_execution_plan(query_plan, routing, wants_chart)
        ctx.execution_plan = execution_plan
        hint = execution_plan.route
        wants_chart = execution_plan.wants_chart
        self._trace_execution_plan(trace, execution_plan, routing)
        logger.debug(
            "[Pipeline] routing=%s mode=%s steps=%d planner=%s",
            hint.value,
            mode.value,
            len(query_plan.steps),
            query_plan.source,
        )

        # ----------------------------------------------------------------
        # 2. Sheet selection
        # ----------------------------------------------------------------
        selected_files: List[Dict[str, Any]] = []
        df: Optional[pd.DataFrame] = None
        can_reuse_followup_df = mode == MultiTurnMode.FOLLOW_UP and ctx._active_df is not None
        should_try_load_data = (
            any(step.needs_new_computation for step in query_plan.steps)
            or can_reuse_followup_df
            or ctx.active_result is None
        )

        if should_try_load_data and not can_reuse_followup_df:
            if emitter:
                await emitter.emit_progress("正在选择数据文件...", step_id="sheet_selection")

            try:
                selected_files = await self.sheet_skill.run(ctx, query)
                execution_plan.target_sheets = list(ctx.selected_sheets)
            except Exception as exc:
                logger.warning("[Pipeline] sheet selection failed: %s", exc)
                # Text-only questions can still be answered from conversation if
                # data selection is unavailable. Data/code paths should surface
                # the selection error because they cannot execute without rows.
                if hint == RoutingHint.INSIGHT_ONLY:
                    selected_files = []
                elif not ctx.files:
                    hint = RoutingHint.INSIGHT_ONLY
                    for step in query_plan.steps:
                        step.route = RoutingHint.INSIGHT_ONLY
                        step.needs_new_computation = False
                    execution_plan.route = RoutingHint.INSIGHT_ONLY
                    execution_plan.needs_new_computation = False
                else:
                    raise

        # ----------------------------------------------------------------
        # 3. DataFrame loading
        # ----------------------------------------------------------------
        if should_try_load_data and (selected_files or can_reuse_followup_df):
            if emitter:
                await emitter.emit_progress("正在加载数据...", step_id="data_loading")

            df = self.df_loader.run(
                ctx,
                selected_files=selected_files,
                multiturn_mode=mode,
            )

        # ----------------------------------------------------------------
        # 4. Execute validated plan steps in dependency order
        # ----------------------------------------------------------------
        result_df, visible_results, step_outputs, execution_failed = await self._execute_plan_steps(
            ctx=ctx,
            plan=query_plan,
            source_df=df,
            previous_result_df=previous_result_df,
            execution_plan=execution_plan,
            trace=trace,
            emitter=emitter,
        )
        if execution_failed:
            error_msg = (
                "数据查询执行失败，请尝试换一种方式描述您的问题，"
                "或检查列名是否正确。"
            )
            logger.warning("[Pipeline] planned execution failed for task=%s", ctx.task_id)
            return ResultBlocks(blocks=[SummaryBlock(content=error_msg)]), hint, mode

        # ----------------------------------------------------------------
        # 5. Chart planning for the result requested by each chart step
        # ----------------------------------------------------------------
        chart_blocks: List[ChartBlock] = []
        chart_requests = [step for step in query_plan.steps if step.wants_chart]
        if wants_chart and not chart_requests and query_plan.steps:
            chart_requests = [query_plan.steps[-1]]

        for step in chart_requests[:3]:
            chart_df = step_outputs.get(step.step_id)
            if chart_df is None:
                chart_df = result_df
            if chart_df is None or chart_df.empty:
                continue
            if emitter:
                await emitter.emit_progress("正在生成图表...", step_id="chart_planning")
            result_field_map = await self.semantic_skill.run(ctx, step.query, df=chart_df)
            chart = await self.chart_skill.run(
                ctx,
                step.query,
                result_df=chart_df,
                field_map=result_field_map,
            )
            if chart is not None:
                chart.title = step.query
                chart_blocks.append(chart)

        chart_block = chart_blocks[0] if chart_blocks else None

        # ----------------------------------------------------------------
        # 8. Insight writing
        # ----------------------------------------------------------------
        if emitter:
            await emitter.emit_progress("正在生成分析洞察...", step_id="insight_writing")

        last_step_is_insight = bool(
            query_plan.steps and query_plan.steps[-1].route == RoutingHint.INSIGHT_ONLY
        )
        if hint == RoutingHint.INSIGHT_ONLY or last_step_is_insight:
            scenario = "insight_only"
        elif chart_block is not None:
            scenario = "chart"
        else:
            scenario = "processing"

        table_blocks: List[TableBlock] = []
        for step, step_df in visible_results:
            table = self._build_table_block(step_df)
            if table is not None:
                if len(visible_results) > 1:
                    table.title = step.query
                table_blocks.append(table)
        table_block = table_blocks[-1] if table_blocks else None

        insight_df = result_df
        if insight_df is None and hint == RoutingHint.INSIGHT_ONLY:
            if df is not None:
                insight_df = df
            elif ctx._result_df is not None:
                insight_df = ctx._result_df
            else:
                insight_df = ctx._active_df

        summary_text = await self.insight_skill.run(
            ctx,
            query,
            scenario=scenario,
            result_df=insight_df,
            chart_block=chart_block,
            table_block=table_block,
        )

        # ----------------------------------------------------------------
        # 9. Assemble ResultBlocks
        # ----------------------------------------------------------------
        blocks: List[Any] = []

        # Summary always first
        if summary_text:
            blocks.append(SummaryBlock(content=summary_text))

        # Tables from independent terminal branches, or the final sequential step.
        blocks.extend(table_blocks)

        blocks.extend(chart_blocks)

        result = ResultBlocks(blocks=blocks)

        if emitter:
            await emitter.emit_progress("正在校验结果...", step_id="validation")
        result = validate_result(result, degrade_invalid_charts=True)

        return result, hint, mode

    async def _execute_plan_steps(
        self,
        ctx: AnalysisContext,
        plan: QueryPlan,
        source_df: Optional[pd.DataFrame],
        previous_result_df: Optional[pd.DataFrame],
        execution_plan: ExecutionPlan,
        trace: Trace,
        emitter: Optional[StreamEmitter],
    ) -> tuple[
        Optional[pd.DataFrame],
        List[tuple[ExecutionStep, pd.DataFrame]],
        Dict[str, pd.DataFrame],
        bool,
    ]:
        """Execute an already validated plan; dependencies may only point backward."""
        outputs: Dict[str, pd.DataFrame] = {}
        computed: List[tuple[ExecutionStep, pd.DataFrame]] = []
        required_columns: List[str] = []

        for index, step in enumerate(plan.steps, start=1):
            input_df = self._step_input_dataframe(
                step,
                outputs=outputs,
                source_df=source_df,
                previous_result_df=previous_result_df,
            )
            if input_df is None:
                if step.needs_new_computation:
                    logger.warning("[Pipeline] no input dataframe for step=%s", step.step_id)
                    return None, [], outputs, True
                continue

            if not step.needs_new_computation or step.route == RoutingHint.INSIGHT_ONLY:
                outputs[step.step_id] = input_df
                continue

            if emitter:
                await emitter.emit_progress(
                    f"正在执行第 {index}/{len(plan.steps)} 步...",
                    step_id="execution",
                )

            if input_df.empty:
                outputs[step.step_id] = input_df.copy()
                computed.append((step, outputs[step.step_id]))
                continue

            if emitter:
                await emitter.emit_progress("正在识别字段类型...", step_id="semantic_typing")
            field_map = await self.semantic_skill.run(ctx, step.query, df=input_df)
            step.required_source_columns = self._required_source_columns(step.query, field_map)
            for column in step.required_source_columns:
                if column not in required_columns:
                    required_columns.append(column)

            if emitter:
                await emitter.emit_progress("正在生成数据画像...", step_id="data_profiling")
            data_summary = await self.profiling_skill.run(
                ctx,
                step.query,
                df=input_df,
                field_map=field_map,
            )

            result: Optional[pd.DataFrame] = None
            if step.route == RoutingHint.RULE_ENGINE:
                try:
                    result = self.rule_engine.run(
                        ctx,
                        query=step.query,
                        df=input_df,
                        field_map=field_map,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Pipeline] rule step %s fell back to CODE_GEN: %s",
                        step.step_id,
                        exc,
                    )
                    step.route = RoutingHint.CODE_GEN

            if step.route == RoutingHint.CODE_GEN and result is None:
                if emitter:
                    async def emit_fn(message: str) -> None:
                        await emitter.emit_progress(message, step_id="execution")
                else:
                    emit_fn = None
                result, _code, _repairs = await self.repair_loop.run(
                    ctx=ctx,
                    query=step.query,
                    df=input_df,
                    data_summary=data_summary,
                    field_map=field_map,
                    wants_chart=step.wants_chart,
                    is_compound=False,
                    required_columns=step.required_source_columns,
                    trace=trace,
                    emit_progress=emit_fn,
                )

            if result is None:
                return None, [], outputs, True

            outputs[step.step_id] = result
            computed.append((step, result))
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
        return final_result, visible, outputs, False

    @staticmethod
    def _step_input_dataframe(
        step: ExecutionStep,
        outputs: Dict[str, pd.DataFrame],
        source_df: Optional[pd.DataFrame],
        previous_result_df: Optional[pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        if len(step.depends_on) > 1:
            return None
        if step.depends_on:
            return outputs.get(step.depends_on[0])
        if step.input_source == "previous_result" and previous_result_df is not None:
            return previous_result_df
        return source_df

    @staticmethod
    def _build_execution_plan(
        plan: QueryPlan,
        routing: RoutingResult,
        wants_chart: bool,
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

        operation_types = unique([
            operation
            for step in plan.steps
            for operation in step.operation_types
        ]) or list(routing.facets.operation_types)
        target_fields = unique([
            target
            for step in plan.steps
            for target in step.target_fields
        ]) or list(routing.facets.target_fields)

        return ExecutionPlan(
            route=primary_route,
            mode=routing.mode,
            operation_types=operation_types,
            needs_new_computation=any(step.needs_new_computation for step in plan.steps),
            wants_chart=wants_chart or any(step.wants_chart for step in plan.steps),
            uses_previous_result=(
                routing.facets.uses_previous_result
                or any(step.input_source == "previous_result" for step in plan.steps)
            ),
            target_fields=target_fields,
            confidence=plan.confidence,
            steps=plan.steps,
            planner_used=plan.is_multi_step,
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
                    "score": routing.structure.score,
                    "signals": routing.structure.signals,
                },
                "execution_plan": execution_plan.model_dump(mode="json"),
            },
        )

    # ------------------------------------------------------------------
    # Field resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _required_source_columns(query: str, field_map: Optional[SemanticFieldMap]) -> List[str]:
        if not field_map or not query_qualifiers(query):
            return []

        match = FieldResolver().resolve(query, field_map, aggregate_only=True)
        return [match.column] if match else []

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
    def _previous_result_dataframe(ctx: AnalysisContext) -> Optional[pd.DataFrame]:
        """Rebuild the previous tabular result as a DataFrame when available."""
        if isinstance(ctx._result_df, pd.DataFrame) and not ctx._result_df.empty:
            return ctx._result_df

        result = ctx.active_result or ctx.last_assistant_result()
        if result is None:
            return None

        table = result.first_table()
        if table is None or not table.rows or not table.columns:
            return None

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
    ) -> tuple:
        """Render a chart from the last table result without rerunning codegen."""
        if emitter:
            await emitter.emit_progress("正在基于上一次结果生成图表...", step_id="chart_planning")

        field_map = await self.semantic_skill.run(ctx, query, df=previous_result_df)
        chart_block = await self.chart_skill.run(
            ctx,
            query,
            result_df=previous_result_df,
            field_map=field_map,
        )
        if chart_block is None:
            result = ResultBlocks(blocks=[
                SummaryBlock(content="上一次结果无法直接生成图表，请换一种图表描述。")
            ])
            return result, hint, mode

        if emitter:
            await emitter.emit_progress("正在生成分析洞察...", step_id="insight_writing")

        summary_text = await self.insight_skill.run(
            ctx,
            query,
            scenario="chart",
            result_df=previous_result_df,
            chart_block=chart_block,
            table_block=None,
        )

        blocks: List[Any] = []
        if summary_text:
            blocks.append(SummaryBlock(content=summary_text))
        blocks.append(chart_block)

        result = ResultBlocks(blocks=blocks)
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
    def _build_table_block(df: pd.DataFrame) -> Optional[TableBlock]:
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
            columns_metadata=col_meta,
        )

    @staticmethod
    def _extract_summary(result: ResultBlocks) -> str:
        """Extract the first summary block's content for Turn.content."""
        summaries = result.all_summaries()
        return summaries[0] if summaries else ""
