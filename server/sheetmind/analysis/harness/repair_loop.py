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

  Max 2 repair attempts (attempt 1 + attempt 2 = 3 total executions).
  This matches the spec §12 "max 2 retries before returning error".
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from ..context import AnalysisContext
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
    ) -> None:
        self.code_gen = code_gen
        self.executor = executor

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: pd.DataFrame,
        data_summary: str = "",
        field_map: Optional[SemanticFieldMap] = None,
        wants_chart: bool = False,
        is_compound: bool = False,
        trace: Optional[Trace] = None,
        emit_progress: Optional[Callable] = None,
    ) -> Tuple[Optional[pd.DataFrame], str, int]:
        """
        Run code gen + executor with up to MAX_REPAIRS repair attempts.

        Returns:
            (result_df, final_code, repairs_used)
            result_df is None only if ALL attempts failed — caller should handle error.
        """
        error_feedback: Optional[str] = None
        last_code: str = ""
        repairs_used: int = 0

        for attempt in range(1 + MAX_REPAIRS):
            is_repair = attempt > 0

            if is_repair and emit_progress:
                await emit_progress(f"修复执行错误（第 {attempt} 次）...")

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
                )
            except Exception as exc:
                logger.warning("[RepairLoop] code gen failed attempt=%d: %s", attempt + 1, exc)
                if attempt < MAX_REPAIRS:
                    error_feedback = f"代码生成失败: {exc}"
                    continue
                return None, last_code, repairs_used

            last_code = code

            if trace:
                trace.add_event(
                    EVT_CODE_GENERATED,
                    output_summary=f"attempt={attempt+1} lines={len(code.splitlines())}",
                    metadata={"code": code[:500]},
                )

            # --- Execution ---
            if emit_progress and not is_repair:
                await emit_progress("正在执行数据查询...")

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

            # Failure — build error feedback for next attempt
            logger.warning(
                "[RepairLoop] attempt %d failed: %s",
                attempt + 1,
                (error or "")[:200],
            )
            error_feedback = (
                f"代码：\n{code}\n\n"
                f"执行错误：\n{error or '未知错误'}"
            )
            repairs_used = attempt + 1

            if trace:
                trace.add_event(EVT_REPAIR, output_summary=f"attempt {attempt+1} failed, retrying")

        # All attempts exhausted
        logger.error("[RepairLoop] all %d attempts failed", 1 + MAX_REPAIRS)
        return None, last_code, repairs_used
