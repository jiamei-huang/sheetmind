"""Subprocess pandas executor with explicit output and safety contracts."""
from __future__ import annotations

import logging
import os
import pickle
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Optional, Tuple

import pandas as pd

from ..context import AnalysisContext
from .base import Tool

logger = logging.getLogger(__name__)

_FORBIDDEN_PATTERNS = [
    re.compile(pattern) for pattern in (
        r"\bimport\s+(?:os|sys|subprocess|socket|requests?|urllib|shutil|pickle|shelve|builtins|importlib|ctypes|threading|multiprocessing)\b",
        r"\b__import__\s*\(", r"\b(?:eval|exec|compile)\s*\(",
        r"\bopen\s*\([^)]*['\"][wWaA]", r"\bpd\.read_[a-z]+\s*\(",
        r"\b\.to_(?:csv|excel|pickle|parquet)\b", r"\b(?:os|sys)\.",
    )
]

_WRAPPER_TEMPLATE = textwrap.dedent("""\
    import sys, math, datetime
    import pandas as pd
    import numpy as np
    import pickle as _pickle

    with open(sys.argv[1], 'rb') as _f:
        df = _pickle.load(_f)

    # ---- User code start ----
    {user_code}
    # ---- User code end ----

    if 'result_df' not in dir():
        print('EXECUTOR_ERROR: result_df not defined after code execution', file=sys.stderr)
        sys.exit(1)
    if not isinstance(result_df, pd.DataFrame):
        print(f'EXECUTOR_ERROR: result_df must be a DataFrame, got {{type(result_df).__name__}}', file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[2], 'wb') as _f:
        _pickle.dump(result_df, _f)
""")


@dataclass
class ExecutionResult:
    success: bool
    result_df: Optional[pd.DataFrame]
    error: Optional[str]
    runtime_ms: int
    output_shape: Optional[Tuple[int, int]]
    safety_violation: bool = False
    error_type: Optional[str] = None


class PythonExecutorTool(Tool):
    name = "python_executor"
    description = "Run pandas code in an isolated process with output caps"

    DEFAULT_TIMEOUT = 30
    MAX_OUTPUT_ROWS = 100_000
    MAX_OUTPUT_COLUMNS = 100
    MAX_MEMORY_BYTES = 768 * 1024 * 1024

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def run(
        self,
        ctx: AnalysisContext,
        code: str = "",
        df: Optional[pd.DataFrame] = None,
        **kwargs: Any,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """Legacy tuple API retained for the repair loop and external callers."""
        result = self.run_detailed(ctx, code=code, df=df, **kwargs)
        return result.result_df, result.error

    def run_detailed(
        self,
        ctx: AnalysisContext,
        code: str = "",
        df: Optional[pd.DataFrame] = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        started = perf_counter()

        def fail(message: str, error_type: str, *, safety: bool = False) -> ExecutionResult:
            return ExecutionResult(
                success=False,
                result_df=None,
                error=message,
                runtime_ms=int((perf_counter() - started) * 1000),
                output_shape=None,
                safety_violation=safety,
                error_type=error_type,
            )

        if not code or not code.strip():
            return fail("Empty code string", "empty_code")
        if df is None or df.empty:
            return fail("No input DataFrame provided", "missing_input")

        forbidden = self._check_forbidden(code)
        if forbidden:
            logger.warning("[PythonExecutor] forbidden code rejected: %s", forbidden)
            return fail(forbidden, "safety_violation", safety=True)

        tmp_dir = tempfile.mkdtemp(prefix="sheetmind_exec_")
        input_path = os.path.join(tmp_dir, "input.pkl")
        output_path = os.path.join(tmp_dir, "output.pkl")
        script_path = os.path.join(tmp_dir, "wrapper.py")
        try:
            with open(input_path, "wb") as file:
                pickle.dump(df, file)
            wrapper = _WRAPPER_TEMPLATE.format(user_code=textwrap.indent(code.strip(), "    "))
            with open(script_path, "w", encoding="utf-8") as file:
                file.write(wrapper)

            completed = subprocess.run(
                [sys.executable, script_path, input_path, output_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=tmp_dir,
                preexec_fn=self._limit_resources if os.name == "posix" else None,
            )
            if completed.returncode != 0:
                error = completed.stderr.strip() or completed.stdout.strip() or "Code execution failed"
                return fail(error, self._classify_error(error))
            if not os.path.exists(output_path):
                return fail("Code executed but result_df was not saved", "output_contract")

            with open(output_path, "rb") as file:
                result_df = pickle.load(file)
            contract_error = self._validate_output(result_df)
            if contract_error:
                return fail(contract_error, "output_contract")

            return ExecutionResult(
                success=True,
                result_df=result_df,
                error=None,
                runtime_ms=int((perf_counter() - started) * 1000),
                output_shape=(len(result_df), len(result_df.columns)),
            )
        except subprocess.TimeoutExpired:
            return fail(f"执行超时（超过 {self.timeout} 秒），请简化查询或减少数据量。", "timeout")
        except Exception as exc:
            logger.exception("[PythonExecutor] unexpected error: %s", exc)
            return fail(f"执行过程中发生意外错误: {exc}", "runtime_error")
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _check_forbidden(code: str) -> Optional[str]:
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(code):
                return f"Code contains forbidden pattern: {pattern.pattern!r}"
        return None

    @classmethod
    def _validate_output(cls, result_df: Any) -> Optional[str]:
        if not isinstance(result_df, pd.DataFrame):
            return "result_df must be a DataFrame"
        if len(result_df) > cls.MAX_OUTPUT_ROWS:
            return f"result_df exceeds row cap ({cls.MAX_OUTPUT_ROWS})"
        if len(result_df.columns) == 0 or len(result_df.columns) > cls.MAX_OUTPUT_COLUMNS:
            return f"result_df must contain 1-{cls.MAX_OUTPUT_COLUMNS} columns"
        if result_df.columns.isna().any() or any(not str(column).strip() for column in result_df.columns):
            return "result_df contains invalid column names"
        return None

    @staticmethod
    def _classify_error(error: str) -> str:
        lowered = error.lower()
        if "syntaxerror" in lowered:
            return "syntax_error"
        if "keyerror" in lowered:
            return "missing_column"
        if "memoryerror" in lowered:
            return "memory_limit"
        if "result_df" in lowered:
            return "output_contract"
        return "runtime_error"

    def _limit_resources(self) -> None:
        """Best-effort POSIX limits; unavailable platforms continue with timeout only."""
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (self.MAX_MEMORY_BYTES, self.MAX_MEMORY_BYTES))
            resource.setrlimit(resource.RLIMIT_CPU, (self.timeout, self.timeout + 1))
        except Exception:
            pass
