"""
SheetMind — Result Validator
=================================
Validates ResultBlocks before they are returned to the caller.

Checks performed:
  TableBlock:
    - columns list is non-empty
    - rows is a list of dicts
    - all rows have the same keys as columns
    - no column name is None or empty string
    - warn if >50% of cells in any numeric column are null (data quality hint)

  ChartBlock:
    - chart_type is one of "bar" | "line" | "pie"
    - labels list is non-empty
    - at least one series with non-empty values
    - series values length matches labels length
    - y_axis_label is set (warns, doesn't fail)

  SummaryBlock:
    - content is non-empty

Raises ResultValidationError (subclass of ValueError) if a fatal error is found.
Logs warnings for non-fatal quality issues.
"""
from __future__ import annotations

import logging
from numbers import Real
from typing import Any

from ..context import ChartBlock, ResultBlocks, SummaryBlock, TableBlock
from ..language import user_text

logger = logging.getLogger(__name__)


class ResultValidationError(ValueError):
    """Raised when a ResultBlocks object fails fatal validation."""


_VALID_CHART_TYPES = {"bar", "line", "pie"}
_MAX_CHART_POINTS = 500
_MAX_CHART_SERIES = 5


def validate_result(
    result: ResultBlocks,
    *,
    degrade_invalid_charts: bool = False,
) -> ResultBlocks:
    """
    Validate all blocks in `result` in place.

    Fatal errors raise ResultValidationError.
    Non-fatal issues are logged as warnings.
    Returns the same result object (for chaining).
    """
    if not result.blocks:
        logger.warning("[ResultValidator] ResultBlocks has no blocks")
        if not result.questions:
            return result

    question_ids = [question.question_id for question in result.questions]
    if len(question_ids) != len(set(question_ids)):
        raise ResultValidationError("QuestionResult question_id values must be unique")
    for question in result.questions:
        if question.status == "success" and not question.blocks:
            raise ResultValidationError(
                f"QuestionResult[{question.question_id}] is successful but has no blocks"
            )

    valid_blocks = []
    removed_charts = 0
    for i, block in enumerate(result.blocks):
        kind = _block_kind(block)
        try:
            if kind == "table":
                _validate_table(block, index=i)
            elif kind == "chart":
                _validate_chart(block, index=i)
            elif kind == "summary":
                _validate_summary(block, index=i)
            elif kind == "field_resolution":
                _validate_field_resolution(block, index=i)
            elif kind == "sheet_resolution":
                _validate_sheet_resolution(block, index=i)
            elif kind == "status":
                _validate_status(block, index=i)
            # metric blocks and unknown kinds are passed through without validation
        except ResultValidationError as exc:
            if kind == "chart" and degrade_invalid_charts:
                logger.warning("[ResultValidator] dropping invalid chart block[%d]: %s", i, exc)
                removed_charts += 1
                continue
            raise
        except Exception as exc:
            # Unexpected validation failure should never crash the pipeline
            logger.warning("[ResultValidator] block[%d] unexpected error: %s", i, exc)
        valid_blocks.append(block)

    if removed_charts:
        has_summary = any(_block_kind(block) == "summary" for block in valid_blocks)
        if not has_summary:
            valid_blocks.insert(0, SummaryBlock(content=user_text(
                result.response_language,
                en="The chart data is incomplete, so the available analysis result was preserved.",
                zh="图表数据不完整，已保留当前可用的分析结果。",
            )))
        result.blocks = valid_blocks
        for question in result.questions:
            question.blocks = [
                block
                for block in question.blocks
                if _block_kind(block) != "chart" or _chart_is_valid(block)
            ]
            if question.status == "success" and not question.blocks:
                question.blocks = [
                    SummaryBlock(content=user_text(
                        result.response_language,
                        en="The chart data is incomplete and cannot be displayed yet.",
                        zh="图表数据不完整，暂时无法显示。",
                    ))
                ]

    return result


def _chart_is_valid(block: Any) -> bool:
    try:
        _validate_chart(block, index=0)
    except ResultValidationError:
        return False
    return True


# ---------------------------------------------------------------------------
# TableBlock validation
# ---------------------------------------------------------------------------

def _validate_table(block: Any, index: int) -> None:
    if isinstance(block, dict):
        columns = block.get("columns", [])
        rows = block.get("rows", [])
    else:
        columns = getattr(block, "columns", [])
        rows = getattr(block, "rows", [])

    # Fatal: no columns
    if not columns:
        raise ResultValidationError(f"TableBlock[{index}]: columns list is empty")

    # Fatal: columns contains empty/None names
    bad_cols = [c for c in columns if not c or not str(c).strip()]
    if bad_cols:
        raise ResultValidationError(
            f"TableBlock[{index}]: column names contain empty/None values: {bad_cols}"
        )

    # Fatal: rows is not a list
    if not isinstance(rows, list):
        raise ResultValidationError(
            f"TableBlock[{index}]: rows must be a list, got {type(rows).__name__}"
        )

    if len(rows) > 1000:
        raise ResultValidationError(f"TableBlock[{index}]: rows exceed frontend cap (1000)")

    # Warn: row key mismatch
    col_set = set(columns)
    for j, row in enumerate(rows[:5]):  # spot-check first 5 rows
        if not isinstance(row, dict):
            raise ResultValidationError(f"TableBlock[{index}] row[{j}] must be a dict")
        row_keys = set(row.keys())
        extra = row_keys - col_set
        missing = col_set - row_keys
        if extra:
            logger.warning(
                "[ResultValidator] TableBlock[%d] row[%d] has extra keys: %s",
                index, j, extra,
            )
        if missing:
            logger.warning(
                "[ResultValidator] TableBlock[%d] row[%d] missing keys: %s",
                index, j, missing,
            )
        if any(isinstance(value, (dict, list, tuple, set)) for value in row.values()):
            raise ResultValidationError(f"TableBlock[{index}] row[{j}] contains nested values")

    # Warn: high null rate in any numeric-looking column
    if rows and len(rows) > 0:
        for col in columns:
            vals = [r.get(col) for r in rows if isinstance(r, dict)]
            null_count = sum(1 for v in vals if v is None or v == "" or str(v) in ("nan", "NaN", "None"))
            if vals and null_count / len(vals) > 0.5:
                logger.warning(
                    "[ResultValidator] TableBlock[%d] column '%s' has %.0f%% null values",
                    index, col, 100 * null_count / len(vals),
                )


# ---------------------------------------------------------------------------
# ChartBlock validation
# ---------------------------------------------------------------------------

def _validate_chart(block: Any, index: int) -> None:
    if isinstance(block, dict):
        chart_type = block.get("chart_type", "")
        labels = block.get("labels", [])
        series = block.get("series", [])
        y_axis_label = block.get("y_axis_label")
    else:
        chart_type = getattr(block, "chart_type", "")
        labels = getattr(block, "labels", [])
        series = getattr(block, "series", [])
        y_axis_label = getattr(block, "y_axis_label", None)

    # Fatal: unknown chart type
    if chart_type not in _VALID_CHART_TYPES:
        raise ResultValidationError(
            f"ChartBlock[{index}]: chart_type '{chart_type}' not in {_VALID_CHART_TYPES}"
        )

    # Fatal: no labels
    if not labels:
        raise ResultValidationError(f"ChartBlock[{index}]: labels list is empty")

    # Fatal: no series
    if not series:
        raise ResultValidationError(f"ChartBlock[{index}]: series list is empty")

    if len(labels) > _MAX_CHART_POINTS:
        raise ResultValidationError(
            f"ChartBlock[{index}]: labels exceed frontend cap ({_MAX_CHART_POINTS})"
        )
    if len(series) > _MAX_CHART_SERIES:
        raise ResultValidationError(
            f"ChartBlock[{index}]: series exceed frontend cap ({_MAX_CHART_SERIES})"
        )

    # Fatal: series values length must match labels
    n_labels = len(labels)
    for k, s in enumerate(series):
        if isinstance(s, dict):
            vals = s.get("values", [])
        else:
            vals = getattr(s, "values", [])

        if len(vals) != n_labels:
            raise ResultValidationError(
                f"ChartBlock[{index}] series[{k}]: "
                f"values length {len(vals)} != labels length {n_labels}"
            )
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, Real)) for value in vals):
            raise ResultValidationError(
                f"ChartBlock[{index}] series[{k}] contains non-numeric values"
            )

    # Warn: missing y_axis_label
    if not y_axis_label:
        logger.warning("[ResultValidator] ChartBlock[%d]: y_axis_label is not set", index)


# ---------------------------------------------------------------------------
# SummaryBlock validation
# ---------------------------------------------------------------------------

def _validate_summary(block: Any, index: int) -> None:
    if isinstance(block, dict):
        content = block.get("content", "")
    else:
        content = getattr(block, "content", "")

    if not content or not str(content).strip():
        # Non-fatal: empty summary is weird but not a crash
        logger.warning("[ResultValidator] SummaryBlock[%d]: content is empty", index)


def _validate_field_resolution(block: Any, index: int) -> None:
    status = block.get("status") if isinstance(block, dict) else getattr(block, "status", "")
    candidates = block.get("candidates", []) if isinstance(block, dict) else getattr(block, "candidates", [])
    selected = block.get("selected_column") if isinstance(block, dict) else getattr(block, "selected_column", None)
    if status == "needs_clarification" and len(candidates) < 2:
        raise ResultValidationError(
            f"FieldResolutionBlock[{index}]: clarification requires at least two candidates"
        )
    if status == "assumed" and not selected:
        raise ResultValidationError(
            f"FieldResolutionBlock[{index}]: assumed field requires selected_column"
        )


def _validate_sheet_resolution(block: Any, index: int) -> None:
    status = block.get("status") if isinstance(block, dict) else getattr(block, "status", "")
    candidates = block.get("candidates", []) if isinstance(block, dict) else getattr(block, "candidates", [])
    if status not in {"scope_conflict", "needs_clarification"}:
        raise ResultValidationError(
            f"SheetResolutionBlock[{index}]: invalid status {status!r}"
        )
    if not candidates:
        raise ResultValidationError(
            f"SheetResolutionBlock[{index}]: at least one candidate is required"
        )


def _validate_status(block: Any, index: int) -> None:
    status = block.get("status") if isinstance(block, dict) else getattr(block, "status", "")
    message = block.get("message") if isinstance(block, dict) else getattr(block, "message", "")
    if status not in {"empty", "failed", "partial", "needs_input"}:
        raise ResultValidationError(f"StatusBlock[{index}]: invalid status {status!r}")
    if not str(message or "").strip():
        raise ResultValidationError(f"StatusBlock[{index}]: message is required")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _block_kind(b: Any) -> str:
    if isinstance(b, dict):
        return b.get("kind", "")
    return getattr(b, "kind", "")
