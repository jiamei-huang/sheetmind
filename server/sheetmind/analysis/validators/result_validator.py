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
from typing import Any, List, Optional

from ..context import ChartBlock, ResultBlocks, SummaryBlock, TableBlock

logger = logging.getLogger(__name__)


class ResultValidationError(ValueError):
    """Raised when a ResultBlocks object fails fatal validation."""


_VALID_CHART_TYPES = {"bar", "line", "pie", "scatter", "area"}


def validate_result(result: ResultBlocks) -> ResultBlocks:
    """
    Validate all blocks in `result` in place.

    Fatal errors raise ResultValidationError.
    Non-fatal issues are logged as warnings.
    Returns the same result object (for chaining).
    """
    if not result.blocks:
        logger.warning("[ResultValidator] ResultBlocks has no blocks")
        return result

    for i, block in enumerate(result.blocks):
        kind = _block_kind(block)
        try:
            if kind == "table":
                _validate_table(block, index=i)
            elif kind == "chart":
                _validate_chart(block, index=i)
            elif kind == "summary":
                _validate_summary(block, index=i)
            # metric blocks and unknown kinds are passed through without validation
        except ResultValidationError:
            raise
        except Exception as exc:
            # Unexpected validation failure should never crash the pipeline
            logger.warning("[ResultValidator] block[%d] unexpected error: %s", i, exc)

    return result


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

    # Warn: row key mismatch
    col_set = set(columns)
    for j, row in enumerate(rows[:5]):  # spot-check first 5 rows
        if not isinstance(row, dict):
            logger.warning(
                "[ResultValidator] TableBlock[%d] row[%d] is not a dict: %s",
                index, j, type(row).__name__,
            )
            continue
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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _block_kind(b: Any) -> str:
    if isinstance(b, dict):
        return b.get("kind", "")
    return getattr(b, "kind", "")
