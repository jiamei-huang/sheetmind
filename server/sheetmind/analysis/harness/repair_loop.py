"""
SheetMind — Repair Loop Harness
=====================================
Coordinates code generation + execution with automatic repair on failure.

Flow:
  Attempt 1:
    CodeGenerationSkill.run(query) → code_str
    PythonExecutorTool.run(code_str, df) → (result_df, error) or (result_df, None)
    → success: return result_df
    → fail: capture error, go to attempt 2

  Attempt 2 (repair):
    CodeGenerationSkill.run(query, error_feedback=code+error) → repaired_code
    PythonExecutorTool.run(repaired_code, df) → (result_df, error) or (result_df, None)
    → success: return result_df
    → fail: return ToolError (caller wraps in error ResultBlock)

  At most 2 repair attempts follow the initial execution (3 total executions).
  Retries stop early for repeated code/errors, safety rejection, or time budget.
"""
from __future__ import annotations

import logging
from time import monotonic
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from ..context import AnalysisContext, QuerySemantics
from ..skills.code_generation import CodeGenerationSkill
from ..skills.semantic_typing import SemanticFieldMap
from ..tools.python_executor import PythonExecutorTool
from ..tracing.trace import (
    EVT_CODE_EXECUTED,
    EVT_CODE_GENERATED,
    EVT_REPAIR,
    Trace,
)

logger = logging.getLogger(__name__)

MAX_REPAIRS = 2  # total retries after first failure = 2 (spec §12)
DEFAULT_REPAIR_TIME_BUDGET_SECONDS = 120.0


class RepairLoop:
    """
    Orchestrate code generation + execution with repair on failure.

    Usage:
        loop = RepairLoop(code_gen_skill, executor_tool)
        result_df, final_code, repairs = await loop.run(
            ctx, query, df, data_summary, field_map, wants_chart, trace, emit_progress
        )
    """

    def __init__(
        self,
        code_gen: CodeGenerationSkill,
        executor: PythonExecutorTool,
        time_budget_seconds: float = DEFAULT_REPAIR_TIME_BUDGET_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Configure collaborators and the elapsed budget for repair retries."""
        self.code_gen = code_gen
        self.executor = executor
        self.time_budget_seconds = max(0.0, float(time_budget_seconds))
        self.clock = clock

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: pd.DataFrame,
        data_summary: str = "",
        field_map: Optional[SemanticFieldMap] = None,
        wants_chart: bool = False,
        is_compound: bool = False,
        required_columns: Optional[List[str]] = None,
        semantics: Optional[QuerySemantics] = None,
        trace: Optional[Trace] = None,
        emit_progress: Optional[Callable] = None,
    ) -> Tuple[Optional[pd.DataFrame], str, int]:
        """
        Run code gen + executor with up to MAX_REPAIRS repair attempts.

        The initial attempt always runs. Repair attempts stop when generated
        code or errors repeat, safety policy rejects code, or the elapsed
        repair time budget has been exhausted.

        Returns:
            (result_df, final_code, repairs_used)
            result_df is None only if ALL attempts failed — caller should handle error.
        """
        error_feedback: Optional[str] = None
        last_code: str = ""
        repairs_used: int = 0
        seen_code: set[str] = set()
        seen_errors: set[str] = set()
        started_at = self.clock()

        for attempt in range(1 + MAX_REPAIRS):
            is_repair = attempt > 0

            if is_repair and self.clock() - started_at >= self.time_budget_seconds:
                logger.warning("[RepairLoop] repair time budget exhausted; stopping retries")
                if trace:
                    trace.add_event(
                        EVT_REPAIR,
                        output_summary="stopped: repair time budget exhausted",
                    )
                return None, last_code, repairs_used

            if is_repair and emit_progress:
                await emit_progress(f"Fixing an execution error (attempt {attempt})...")

            # --- Code generation ---
            try:
                code = await self.code_gen.run(
                    ctx=ctx,
                    query=query,
                    df=df,
                    data_summary=data_summary,
                    field_map=field_map,
                    error_feedback=error_feedback,
                    wants_chart=wants_chart,
                    is_compound=is_compound,
                    required_columns=required_columns,
                    semantics=semantics,
                )
            except Exception as exc:
                logger.warning("[RepairLoop] code gen failed attempt=%d: %s", attempt + 1, exc)
                error_key = f"code_generation:{type(exc).__name__}:{exc}"
                if error_key in seen_errors:
                    repairs_used = attempt
                    logger.warning("[RepairLoop] repeated code generation error; stopping retries")
                    if trace:
                        trace.add_event(
                            EVT_REPAIR,
                            output_summary="stopped: repeated code generation error",
                        )
                    return None, last_code, repairs_used
                seen_errors.add(error_key)
                if attempt < MAX_REPAIRS:
                    error_feedback = f"Code generation failed: {exc}"
                    continue
                return None, last_code, repairs_used

            code_key = code.strip()
            if is_repair and code_key in seen_code:
                logger.warning("[RepairLoop] repeated generated code; stopping retries")
                if trace:
                    trace.add_event(
                        EVT_REPAIR,
                        output_summary="stopped: repeated generated code",
                    )
                return None, code, repairs_used
            seen_code.add(code_key)
            last_code = code

            if trace:
                trace.add_event(
                    EVT_CODE_GENERATED,
                    output_summary=f"attempt={attempt+1} lines={len(code.splitlines())}",
                    metadata={"code": code[:500]},
                )

            # --- Execution ---
            if emit_progress and not is_repair:
                await emit_progress("Running the data query...")

            field_error = CodeGenerationSkill.validate_required_columns(
                code,
                required_columns,
                available_columns=df.columns,
            )
            if field_error is None:
                field_error = CodeGenerationSkill.validate_currency_safety(
                    code,
                    query,
                    df,
                )
            if field_error is None:
                field_error = CodeGenerationSkill.validate_extreme_evidence(
                    code,
                    query,
                    semantics,
                )
            if field_error:
                logger.warning("[RepairLoop] field contract failed attempt=%d: %s", attempt + 1, field_error)
                if trace:
                    trace.add_event(
                        EVT_CODE_EXECUTED,
                        output_summary=f"attempt={attempt+1} FIELD_CONTRACT_FAILED",
                        error=field_error,
                    )
                repairs_used = attempt + 1
                error_key = field_error.strip()
                if error_key in seen_errors:
                    logger.warning("[RepairLoop] repeated field contract error; stopping retries")
                    if trace:
                        trace.add_event(
                            EVT_REPAIR,
                            output_summary="stopped: repeated field contract error",
                        )
                    return None, last_code, repairs_used
                seen_errors.add(error_key)
                error_feedback = f"Code:\n{code}\n\nExecution error:\n{field_error}"
                continue

            result_df, error = self.executor.run(
                ctx=ctx,
                code=code,
                df=df,
            )

            if trace:
                trace.add_event(
                    EVT_CODE_EXECUTED,
                    output_summary=(
                        f"attempt={attempt+1} "
                        f"{'OK rows=' + str(len(result_df)) if result_df is not None else 'FAILED'}"
                    ),
                    error=error,
                )

            if result_df is not None:
                # Success
                if is_repair:
                    repairs_used = attempt
                    if trace:
                        trace.add_event(EVT_REPAIR, output_summary=f"repaired after {attempt} retries")
                return result_df, code, repairs_used

            # Safety rejections are deterministic policy decisions. Re-prompting
            # with the rejected code adds cost without a legitimate repair path.
            if error and "Code contains forbidden pattern" in error:
                logger.warning("[RepairLoop] safety violation; stopping without retry")
                return None, last_code, repairs_used

            repairs_used = attempt + 1
            error_key = (error or "Unknown error").strip()
            if error_key in seen_errors:
                logger.warning("[RepairLoop] repeated execution error; stopping retries")
                if trace:
                    trace.add_event(
                        EVT_REPAIR,
                        output_summary="stopped: repeated execution error",
                    )
                return None, last_code, repairs_used
            seen_errors.add(error_key)

            # Failure — build error feedback for next attempt
            logger.warning(
                "[RepairLoop] attempt %d failed: %s",
                attempt + 1,
                (error or "")[:200],
            )
            error_feedback = (
                f"Code:\n{code}\n\n"
                f"Execution error:\n{error or 'Unknown error'}"
            )
            if trace:
                trace.add_event(EVT_REPAIR, output_summary=f"attempt {attempt+1} failed, retrying")

        # All attempts exhausted
        logger.error("[RepairLoop] all %d attempts failed", 1 + MAX_REPAIRS)
        return None, last_code, repairs_used
