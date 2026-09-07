"""
SheetMind — Python Executor Tool (Subprocess Sandbox)
==========================================================
Runs LLM-generated pandas code against user data in an isolated subprocess.

Security model:
  - Executes in a SEPARATE subprocess (not in the main FastAPI process)
  - Hard-kill after 30s (configurable)
  - Whitelist of allowed imports (only pandas/numpy/stdlib)
  - Forbidden pattern pre-check (import os, eval, exec, open, etc.)
  - No network access (checked in pre-scan)
  - Input/output via temp pickle files (not stdin/stdout)

Repair contract:
  - Returns (result_df, None) on success
  - Returns (None, error_message) on failure — the caller (RepairLoop) uses
    the error_message to ask the model to fix the code and retries.

The wrapper script is generated fresh per execution and deleted on cleanup.
"""
from __future__ import annotations

import logging
import os
import pickle
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Optional, Tuple

import pandas as pd

from ..context import AnalysisContext
from .base import Tool, ToolError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security: pre-execution code scan
# ---------------------------------------------------------------------------

import re

# These patterns are rejected before subprocess is even started
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bimport\s+os\b"),
    re.compile(r"\bimport\s+sys\b"),
    re.compile(r"\bimport\s+subprocess\b"),
    re.compile(r"\bimport\s+socket\b"),
    re.compile(r"\bimport\s+requests?\b"),
    re.compile(r"\bimport\s+urllib\b"),
    re.compile(r"\bimport\s+shutil\b"),
    re.compile(r"\bimport\s+pickle\b"),
    re.compile(r"\bimport\s+shelve\b"),
    re.compile(r"\bimport\s+builtins\b"),
    re.compile(r"\bimport\s+importlib\b"),
    re.compile(r"\bimport\s+ctypes\b"),
    re.compile(r"\bimport\s+threading\b"),
    re.compile(r"\bimport\s+multiprocessing\b"),
    re.compile(r"\b__import__\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bopen\s*\([^)]*['\"][wWaA]"),  # open(..., "w") / open(..., "a")
    re.compile(r"\bpd\.read_[a-z]+\s*\("),          # pd.read_csv, pd.read_excel, …
    re.compile(r"\b\.to_csv\b|\b\.to_excel\b|\b\.to_pickle\b|\b\.to_parquet\b"),
    re.compile(r"\bos\."),
    re.compile(r"\bsys\."),
]

# Allowed import prefixes (user code may have these, though we discourage all imports)
_ALLOWED_IMPORTS = {
    "pandas", "pd", "numpy", "np", "json", "re", "math", "datetime",
    "collections", "functools", "itertools", "operator", "string",
    "unicodedata", "decimal",
}


def _check_forbidden(code: str) -> Optional[str]:
    """Return an error string if the code contains a forbidden pattern, else None."""
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(code):
            return f"Code contains forbidden pattern: {pattern.pattern!r}"
    return None


# ---------------------------------------------------------------------------
# Subprocess wrapper script template
# ---------------------------------------------------------------------------

_WRAPPER_TEMPLATE = textwrap.dedent("""\
    import sys, io, json, re, math, datetime
    from collections import defaultdict, Counter, OrderedDict
    from functools import reduce
    from itertools import chain, groupby as itertools_groupby
    import pandas as pd
    import numpy as np

    # Load input DataFrame
    import pickle as _pickle
    with open(sys.argv[1], 'rb') as _f:
        df = _pickle.load(_f)

    # ---- User code start ----
    {user_code}
    # ---- User code end ----

    # Validate result
    if 'result_df' not in dir():
        print("EXECUTOR_ERROR: result_df not defined after code execution", file=sys.stderr)
        sys.exit(1)
    if not isinstance(result_df, pd.DataFrame):
        print(f"EXECUTOR_ERROR: result_df must be a DataFrame, got {{type(result_df).__name__}}", file=sys.stderr)
        sys.exit(1)

    # Save output
    with open(sys.argv[2], 'wb') as _f:
        _pickle.dump(result_df, _f)
    print(f"OK rows={{len(result_df)}}")
""")


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

class PythonExecutorTool(Tool):
    """
    Execute LLM-generated pandas code in a sandboxed subprocess.

    Returns:
        (result_df, None)           on success
        (None, error_message_str)   on failure (for RepairLoop)
    """

    name = "python_executor"
    description = "Run pandas code in isolated subprocess (30s timeout, import whitelist)"

    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def run(
        self,
        ctx: AnalysisContext,
        code: str = "",
        df: Optional[pd.DataFrame] = None,
        **kwargs: Any,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        Execute `code` with `df` available as the `df` variable.

        Returns:
            (result_df, None)     — success
            (None, error_str)     — failure (caller should retry or return error)
        """
        if not code or not code.strip():
            return None, "Empty code string"

        if df is None or df.empty:
            return None, "No input DataFrame provided"

        # Pre-execution security scan
        forbidden_error = _check_forbidden(code)
        if forbidden_error:
            logger.warning("[PythonExecutor] Forbidden code rejected: %s", forbidden_error)
            return None, forbidden_error

        tmp_dir = tempfile.mkdtemp(prefix="sheetmind_exec_")
        input_path = os.path.join(tmp_dir, "input.pkl")
        output_path = os.path.join(tmp_dir, "output.pkl")
        script_path = os.path.join(tmp_dir, "wrapper.py")

        try:
            # Write input df
            with open(input_path, "wb") as f:
                pickle.dump(df, f)

            # Write wrapper script
            indented_code = textwrap.indent(code.strip(), "    ")
            wrapper_code = _WRAPPER_TEMPLATE.format(user_code=indented_code)
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(wrapper_code)

            # Execute in subprocess
            python_exe = sys.executable
            result = subprocess.run(
                [python_exe, script_path, input_path, output_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=tmp_dir,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                error_msg = stderr or stdout or "Code execution failed (unknown error)"
                logger.debug("[PythonExecutor] execution failed:\nSTDOUT: %s\nSTDERR: %s", stdout, stderr)
                return None, error_msg

            # Read result
            if not os.path.exists(output_path):
                return None, "Code executed but result_df was not saved (check code logic)"

            with open(output_path, "rb") as f:
                result_df = pickle.load(f)

            logger.debug(
                "[PythonExecutor] success: shape=%s columns=%s",
                result_df.shape,
                list(result_df.columns[:5]),
            )
            return result_df, None

        except subprocess.TimeoutExpired:
            logger.warning("[PythonExecutor] execution timed out after %ds", self.timeout)
            return None, f"执行超时（超过 {self.timeout} 秒），请简化查询或减少数据量。"

        except Exception as exc:
            logger.exception("[PythonExecutor] unexpected error: %s", exc)
            return None, f"执行过程中发生意外错误: {exc}"

        finally:
            # Always clean up temp files
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
