"""
SheetMind — DataFrame Loader Tool
======================================
Loads a pandas DataFrame from selected Excel files.

Handles:
  - Single file, single sheet → df
  - Single file, multiple sheets → concatenate if same columns, else first sheet
  - Multiple files → concatenate if same columns, else first file
  - FOLLOW_UP mode → use ctx._active_df (already loaded from previous turn)
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from ..context import AnalysisContext, MultiTurnMode
from .base import Tool, ToolError

logger = logging.getLogger(__name__)

# Cap rows loaded from Excel per sheet.
# None means read the full sheet. Aggregations and "which item costs the most"
# questions must use complete data; silent truncation caused real wrong totals.
# Reintroduce sampling only with an explicit UI/API warning and a query plan that
# cannot be mistaken for an exact answer.
MAX_ROWS_PER_SHEET: Optional[int] = None


class DataframeLoaderTool(Tool):
    """
    Load a pandas DataFrame from Excel file(s) stored in the project.

    Input kwargs:
        selected_files: List[Dict]  — from SheetSelectionSkill
                        [{"fileName": "...", "sheets": ["Sheet1"]}]
        multiturn_mode: MultiTurnMode — if FOLLOW_UP, reuse ctx._active_df

    Output:
        pd.DataFrame
    """

    name = "dataframe_loader"
    description = "Load pandas DataFrame from selected Excel files"

    def run(
        self,
        ctx: AnalysisContext,
        selected_files: Optional[List[Dict[str, Any]]] = None,
        multiturn_mode: Optional[MultiTurnMode] = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        # FOLLOW_UP: reuse the active working DataFrame from the previous turn.
        # `_active_df` is deliberately the current analysis workspace, not always
        # the last output table; aggregation outputs are kept separately so they
        # do not accidentally erase the richer source rows needed for later turns.
        if multiturn_mode == MultiTurnMode.FOLLOW_UP and ctx._active_df is not None:
            logger.debug("[DataframeLoader] FOLLOW_UP: reusing ctx._active_df")
            return ctx._active_df

        if not selected_files:
            raise ToolError(
                "没有选中任何文件，无法加载数据。",
                retryable=False,
            )

        try:
            from sheetmind.services.excel import ExcelService
        except ImportError as exc:
            raise ToolError(f"ExcelService import failed: {exc}") from exc

        excel_service = ExcelService()
        all_dfs: List[pd.DataFrame] = []

        for file_info in selected_files:
            file_name = file_info.get("fileName", "")
            sheet_names = file_info.get("sheets", [])
            if not file_name:
                continue

            file_bytes = excel_service.get_file_by_name(ctx.project_id, file_name)
            if not file_bytes:
                logger.warning("[DataframeLoader] file not found: %s", file_name)
                continue

            file_dfs = self._load_sheets(file_bytes, file_name, sheet_names)
            all_dfs.extend(file_dfs)

        if not all_dfs:
            raise ToolError(
                "文件加载失败：未能读取到有效数据。",
                detail=f"Tried files: {[f.get('fileName') for f in selected_files]}",
            )

        df = self._merge_dfs(all_dfs)
        # Store in context for FOLLOW_UP access in the next turn.
        ctx._source_df = df
        ctx._active_df = df
        logger.debug(
            "[DataframeLoader] loaded df shape=%s columns=%s",
            df.shape,
            list(df.columns[:5]),
        )
        return df

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_sheets(
        self,
        file_bytes: bytes,
        file_name: str,
        sheet_names: List[str],
    ) -> List[pd.DataFrame]:
        dfs: List[pd.DataFrame] = []
        try:
            xls = pd.ExcelFile(io.BytesIO(file_bytes))
        except Exception as exc:
            raise ToolError(
                f"Excel 文件解析失败：{file_name}",
                detail=str(exc),
            ) from exc

        available = xls.sheet_names
        # Fall back to all sheets if none specified
        targets = sheet_names if sheet_names else available

        for sheet in targets:
            if sheet not in available:
                logger.warning("[DataframeLoader] sheet not found: %s in %s", sheet, file_name)
                continue
            try:
                header_row = self._detect_header_row(file_bytes, sheet)
                if header_row > 0:
                    logger.debug(
                        "[DataframeLoader] sheet=%s: header detected at row %d, skipping %d leading row(s)",
                        sheet, header_row, header_row,
                    )
                df = pd.read_excel(
                    io.BytesIO(file_bytes),
                    sheet_name=sheet,
                    header=header_row,
                    nrows=MAX_ROWS_PER_SHEET,
                )
                df = self._clean_df(df)
                if not df.empty:
                    # Warn when the sheet likely has more rows than we loaded.
                    if MAX_ROWS_PER_SHEET is not None and len(df) >= MAX_ROWS_PER_SHEET:
                        logger.warning(
                            "[DataframeLoader] sheet=%s truncated to %d rows; "
                            "actual row count may be higher. "
                            "Analysis is based on the first %d rows only.",
                            sheet, MAX_ROWS_PER_SHEET, MAX_ROWS_PER_SHEET,
                        )
                    dfs.append(df)
            except Exception as exc:
                logger.warning("[DataframeLoader] failed to read sheet %s: %s", sheet, exc)

        return dfs

    @staticmethod
    def _detect_header_row(file_bytes: bytes, sheet: str, max_scan: int = 15) -> int:
        """
        Detect the true header row index (0-based) for AI analysis.

        Many Excel files have title/description rows before the actual column
        headers.  This method scans the first ``max_scan`` rows and returns
        the index of the first row that looks like a header:

        Criteria:
        - fill_ratio  ≥ 40 %  — enough cells are non-empty
        - string_ratio ≥ 50 %  — majority of values are plain strings, not numbers
        - known business header names get priority even when duplicate
          headers exist (pandas will suffix them as .1/.2)

        Returns 0 (pandas default) when no better candidate is found, so
        the caller's behaviour is unchanged for well-formed files.
        """
        try:
            raw = pd.read_excel(
                io.BytesIO(file_bytes),
                sheet_name=sheet,
                header=None,
                nrows=max_scan,
            )
        except Exception:
            return 0

        if raw.empty:
            return 0

        total_cols = raw.shape[1]
        header_keywords = {
            "月份", "日期", "平台", "店铺", "产品大类", "产品分类", "物流商",
            "费用类型", "费用金额", "人民币金额", "币别", "汇率", "数量",
            "国家", "目的地", "计费重量（KG）", "易仓SKU", "订单号", "产品名称",
            "销售", "金额", "收入", "成本",
        }

        for row_idx in range(len(raw)):
            row = raw.iloc[row_idx]
            non_null = row.dropna()

            if len(non_null) == 0:
                continue  # fully empty row — likely a spacer

            fill_ratio = len(non_null) / total_cols
            if fill_ratio < 0.4:
                continue  # too sparse — looks like a title / merged-cell row

            # Plain strings strongly suggest a header row; numbers suggest data
            str_count = sum(1 for v in non_null if isinstance(v, str))
            string_ratio = str_count / len(non_null)
            if string_ratio < 0.5:
                continue  # predominantly numeric — this is a data row

            labels = [str(v).strip() for v in non_null]
            exact_keyword_hits = sum(1 for label in labels if label in header_keywords)
            fuzzy_keyword_hits = sum(
                1
                for label in labels
                if any(keyword in label for keyword in header_keywords)
            )

            if exact_keyword_hits >= 2 or fuzzy_keyword_hits >= 3:
                return row_idx

            # Generic fallback: unique string-heavy rows still look like headers.
            if len(set(labels)) == len(labels):
                return row_idx

        return 0  # fallback: treat row 0 as header (pandas default)

    @staticmethod
    def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
        """Drop fully-empty rows and columns, strip column name whitespace."""
        df = df.dropna(how="all").dropna(axis=1, how="all")
        df.columns = [str(c).strip() for c in df.columns]
        return df.reset_index(drop=True)

    @staticmethod
    def _merge_dfs(dfs: List[pd.DataFrame]) -> pd.DataFrame:
        """Merge multiple DFs: concat if same columns, else use first."""
        if len(dfs) == 1:
            return dfs[0]

        col_sets = [frozenset(d.columns) for d in dfs]
        if len(set(col_sets)) == 1:
            return pd.concat(dfs, ignore_index=True)

        # Columns differ: try to concat anyway (pandas fills missing with NaN)
        try:
            return pd.concat(dfs, ignore_index=True)
        except Exception:
            return dfs[0]
