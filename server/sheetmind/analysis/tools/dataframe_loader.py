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
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

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

_SPREADSHEET_XML_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_UNSUPPORTED_FILTER_VALUE_ERROR = (
    "Value must be either numerical or a string containing a wildcard"
)


@dataclass
class LoadResult:
    df: pd.DataFrame
    source_files: List[str] = field(default_factory=list)
    source_sheets: List[str] = field(default_factory=list)
    detected_header_rows: Dict[str, int] = field(default_factory=dict)
    dropped_empty_rows: int = 0
    dropped_empty_columns: int = 0
    warnings: List[str] = field(default_factory=list)


class DataframeLoaderTool(Tool):
    """
    Load a pandas DataFrame from Excel file(s) stored in the project.

    Input kwargs:
        selected_files: List[Dict]  — from SheetSelectionSkill
                        [{"fileName": "...", "sheets": ["Sheet1"]}]
        multiturn_mode: MultiTurnMode — if FOLLOW_UP, reuse ctx._active_df

    Output: pd.DataFrame by default, or LoadResult when return_report=True.
    """

    name = "dataframe_loader"
    description = "Load pandas DataFrame from selected Excel files"

    def run(
        self,
        ctx: AnalysisContext,
        selected_files: Optional[List[Dict[str, Any]]] = None,
        multiturn_mode: Optional[MultiTurnMode] = None,
        return_report: bool = False,
        **kwargs: Any,
    ) -> pd.DataFrame:
        # FOLLOW_UP: reuse the active working DataFrame from the previous turn.
        # `_active_df` is deliberately the current analysis workspace, not always
        # the last output table; aggregation outputs are kept separately so they
        # do not accidentally erase the richer source rows needed for later turns.
        if multiturn_mode == MultiTurnMode.FOLLOW_UP and ctx._active_df is not None:
            logger.debug("[DataframeLoader] FOLLOW_UP: reusing ctx._active_df")
            report = LoadResult(df=ctx._active_df, warnings=["Reused active dataframe for follow-up."])
            ctx._load_report = report
            return report if return_report else report.df

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
        report = LoadResult(df=pd.DataFrame())

        for file_info in selected_files:
            file_name = file_info.get("fileName", "")
            sheet_names = file_info.get("sheets", [])
            if not file_name:
                continue

            file_bytes = excel_service.get_file_by_name(ctx.project_id, file_name)
            if not file_bytes:
                logger.warning("[DataframeLoader] file not found: %s", file_name)
                continue

            file_dfs = self._load_sheets(file_bytes, file_name, sheet_names, report=report)
            all_dfs.extend(file_dfs)
            if file_dfs:
                report.source_files.append(file_name)

        if not all_dfs:
            raise ToolError(
                "文件加载失败：未能读取到有效数据。",
                detail=f"Tried files: {[f.get('fileName') for f in selected_files]}",
            )

        df = self._merge_dfs(all_dfs)
        report.df = df
        duplicate_columns = df.columns[df.columns.duplicated()].tolist()
        if duplicate_columns:
            report.warnings.append(f"Duplicate column names: {duplicate_columns}")
        # Store in context for FOLLOW_UP access in the next turn.
        ctx._source_df = df
        ctx._active_df = df
        ctx._load_report = report
        logger.debug(
            "[DataframeLoader] loaded df shape=%s columns=%s",
            df.shape,
            list(df.columns[:5]),
        )
        return report if return_report else df

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_sheets(
        self,
        file_bytes: bytes,
        file_name: str,
        sheet_names: List[str],
        report: Optional[LoadResult] = None,
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
        sanitized_file_bytes: Optional[bytes] = None
        removed_filter_count = 0

        for sheet in targets:
            if sheet not in available:
                logger.warning("[DataframeLoader] sheet not found: %s in %s", sheet, file_name)
                continue
            try:
                read_bytes = file_bytes
                try:
                    header_row = self._detect_header_row(
                        read_bytes, sheet, raise_errors=True
                    )
                    df = self._read_sheet(read_bytes, sheet, header_row)
                except Exception as exc:
                    if not self._is_unsupported_filter_error(exc):
                        raise
                    if sanitized_file_bytes is None:
                        sanitized_file_bytes, removed_filter_count = (
                            self._remove_filter_metadata(file_bytes)
                        )
                    if removed_filter_count == 0:
                        raise
                    read_bytes = sanitized_file_bytes
                    header_row = self._detect_header_row(
                        read_bytes, sheet, raise_errors=True
                    )
                    df = self._read_sheet(read_bytes, sheet, header_row)
                    warning = (
                        f"Ignored unsupported Excel filter metadata while reading "
                        f"{file_name}:{sheet}."
                    )
                    logger.warning("[DataframeLoader] %s", warning)
                    if report is not None and warning not in report.warnings:
                        report.warnings.append(warning)

                if report is not None:
                    report.source_sheets.append(sheet)
                    report.detected_header_rows[f"{file_name}:{sheet}"] = header_row
                if header_row > 0:
                    logger.debug(
                        "[DataframeLoader] sheet=%s: header detected at row %d, skipping %d leading row(s)",
                        sheet, header_row, header_row,
                    )
                original_rows, original_columns = df.shape
                df = self._clean_df(df)
                if report is not None:
                    report.dropped_empty_rows += original_rows - len(df)
                    report.dropped_empty_columns += original_columns - len(df.columns)
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
                if report is not None:
                    report.warnings.append(f"Failed to read {file_name}:{sheet}: {exc}")

        return dfs

    @staticmethod
    def _read_sheet(file_bytes: bytes, sheet: str, header_row: int) -> pd.DataFrame:
        return pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=sheet,
            header=header_row,
            nrows=MAX_ROWS_PER_SHEET,
        )

    @staticmethod
    def _is_unsupported_filter_error(exc: Exception) -> bool:
        current: Optional[BaseException] = exc
        while current is not None:
            if _UNSUPPORTED_FILTER_VALUE_ERROR in str(current):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _remove_filter_metadata(file_bytes: bytes) -> tuple[bytes, int]:
        """Remove display-only filters from a temporary XLSX read copy."""
        source = io.BytesIO(file_bytes)
        output = io.BytesIO()
        auto_filter_tag = f"{{{_SPREADSHEET_XML_NAMESPACE}}}autoFilter"
        removed = 0

        with zipfile.ZipFile(source, "r") as zin:
            with zipfile.ZipFile(output, "w") as zout:
                for item in zin.infolist():
                    payload = zin.read(item.filename)
                    is_filter_container = (
                        item.filename.startswith("xl/worksheets/")
                        or item.filename.startswith("xl/tables/")
                    ) and item.filename.endswith(".xml")
                    if is_filter_container and b"autoFilter" in payload:
                        root = ElementTree.fromstring(payload)
                        for parent in root.iter():
                            for child in list(parent):
                                if child.tag == auto_filter_tag:
                                    parent.remove(child)
                                    removed += 1
                        payload = ElementTree.tostring(
                            root, encoding="utf-8", xml_declaration=True
                        )
                    zout.writestr(item, payload)

        return output.getvalue(), removed

    @staticmethod
    def _detect_header_row(
        file_bytes: bytes,
        sheet: str,
        max_scan: int = 15,
        raise_errors: bool = False,
    ) -> int:
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
            if raise_errors:
                raise
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
