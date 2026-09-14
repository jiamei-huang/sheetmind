"""
Sheet选择器
根据用户query和项目文件，智能选择需要使用的Excel文件和Sheet
"""
import re
from typing import List, Dict, Any, Optional
from sheetmind.database import get_db_connection
from sheetmind.services.excel import ExcelService
import pandas as pd
import io
from sheetmind.logging import get_logger

logger = get_logger("sheet_selector")


class SheetSelector:
    """Sheet选择器"""

    def __init__(self):
        self.excel_service = ExcelService()

    def select_sheets(self, project_id: str, query: str) -> List[Dict[str, Any]]:
        """
        根据query智能选择需要使用的文件和Sheet
        Args:
            project_id: 项目ID
            query: 用户查询
        Returns:
            选中的文件和Sheet列表: [{"fileName": "...", "sheets": ["Sheet1", "Sheet2"]}]
        """
        logger.debug(f"[SheetSelector] ====== 开始选择 Sheet ======")
        logger.debug(f"[SheetSelector] Project ID: {project_id}")
        logger.debug(f"[SheetSelector] Query: {query[:100]}{'...' if len(query) > 100 else ''}")

        # 获取项目下的文件（只选择最新的）
        logger.debug(f"[SheetSelector] Step 1: 获取项目文件...")
        files = self._get_project_files(project_id)
        if not files:
            logger.debug(f"[SheetSelector] ⚠️ 未找到文件")
            return []

        logger.debug(f"[SheetSelector] ✅ 找到 {len(files)} 个文件")

        # 解析每个文件的sheet信息
        logger.debug(f"[SheetSelector] Step 2: 解析文件 Sheet 信息...")
        file_sheets_info = []
        for file_idx, file_ref in enumerate(files):
            file_id = file_ref["fileId"]
            file_name = file_ref["fileName"]
            logger.debug(f"[SheetSelector]   处理文件 {file_idx+1}/{len(files)}: {file_name}")
            try:
                # 获取文件的sheet列表
                logger.debug(f"[SheetSelector]     获取文件字节...")
                file_bytes = self.excel_service.get_file_by_id(project_id, file_id)
                if file_bytes:
                    logger.debug(f"[SheetSelector]     解析 Excel Sheet 列表...")
                    with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                        sheet_names = xls.sheet_names
                        logger.debug(f"[SheetSelector]     ✅ 找到 {len(sheet_names)} 个 Sheet: {sheet_names[:5]}{'...' if len(sheet_names) > 5 else ''}")
                        file_sheets_info.append({
                            "fileId": file_id,
                            "fileName": file_name,
                            "sheets": sheet_names,
                            "allSheets": sheet_names
                        })
                else:
                    logger.debug(f"[SheetSelector]     ⚠️ 文件字节为空")
            except Exception as e:
                logger.debug(f"[SheetSelector]     ❌ 错误: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        if not file_sheets_info:
            logger.debug(f"[SheetSelector] ⚠️ 未找到有效的文件信息")
            return []

        # 根据query智能选择
        logger.debug(f"[SheetSelector] Step 3: 智能选择 Sheet...")
        selected = self._intelligent_select(file_sheets_info, query)
        logger.debug(f"[SheetSelector] ✅ 选择了 {len(selected)} 个文件")
        for idx, item in enumerate(selected):
            logger.debug(f"[SheetSelector]   {idx+1}. {item['fileName']}: {len(item['sheets'])} 个 Sheet")

        logger.debug(f"[SheetSelector] ====== Sheet 选择完成 ======")
        return selected

    def list_sheet_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        """Return lightweight, query-independent metadata for every current sheet."""
        candidates: List[Dict[str, Any]] = []
        for recency_rank, file_ref in enumerate(self._get_project_files(project_id)):
            file_id = file_ref["fileId"]
            file_name = file_ref["fileName"]
            try:
                file_bytes = self.excel_service.get_file_by_id(project_id, file_id)
                if not file_bytes:
                    continue
                with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                    for sheet_name in xls.sheet_names:
                        metadata = self._read_sheet_metadata(
                            file_bytes,
                            sheet_name,
                        )
                        candidates.append({
                            "candidateId": f"{file_id}::{sheet_name}",
                            "fileId": file_id,
                            "fileName": file_name,
                            "sheetName": sheet_name,
                            "columns": metadata["columns"],
                            "sampleValues": metadata["sampleValues"],
                            "rowCount": metadata["rowCount"],
                            "recencyRank": recency_rank,
                        })
            except Exception as exc:
                logger.warning(
                    "[SheetSelector] failed to profile workbook %s: %s",
                    file_name,
                    exc,
                )
        return candidates

    @staticmethod
    def _read_sheet_metadata(file_bytes: bytes, sheet_name: str) -> Dict[str, Any]:
        """Read enough rows to identify headers and representative values."""
        raw = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=sheet_name,
            header=None,
            nrows=30,
        )
        if raw.empty:
            return {"columns": [], "sampleValues": [], "rowCount": 0}

        header_row = SheetSelector._detect_header_row(raw)
        headers = []
        for index, value in enumerate(raw.iloc[header_row].tolist(), start=1):
            if pd.isna(value) or not str(value).strip():
                headers.append(f"Unnamed: {index}")
            else:
                headers.append(str(value).strip())
        data = raw.iloc[header_row + 1:].copy()
        sample_values: List[str] = []
        for value in data.to_numpy().flatten().tolist():
            if pd.isna(value):
                continue
            text = str(value).strip()
            if text and text not in sample_values:
                sample_values.append(text)
            if len(sample_values) >= 12:
                break
        return {
            "columns": headers,
            "sampleValues": sample_values,
            "rowCount": max(len(data), 0),
        }

    @staticmethod
    def _detect_header_row(raw: pd.DataFrame) -> int:
        business_terms = (
            "日期", "时间", "月份", "平台", "店铺", "产品", "sku", "金额",
            "销售", "费用", "数量", "地区", "区域", "客户", "订单",
        )
        best_index = 0
        best_score = -1.0
        for index, row in raw.head(15).iterrows():
            values = [value for value in row.tolist() if pd.notna(value) and str(value).strip()]
            if not values:
                continue
            fill_ratio = len(values) / max(len(row), 1)
            string_ratio = sum(isinstance(value, str) for value in values) / len(values)
            keyword_hits = sum(
                any(term in str(value).lower() for term in business_terms)
                for value in values
            )
            uniqueness = len({str(value) for value in values}) / len(values)
            score = fill_ratio + string_ratio + uniqueness + min(keyword_hits, 4) * 0.35
            if fill_ratio >= 0.35 and string_ratio >= 0.45 and score > best_score:
                best_index = int(index)
                best_score = score
        return best_index

    def _get_project_files(self, project_id: str) -> List[Dict[str, str]]:
        """
        获取项目下的全部文件版本，并按上传时间排序。
        """
        import time
        logger.debug(f"[SheetSelector] _get_project_files: project_id={project_id}")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """SELECT file_id, file_name
                   FROM files
                   WHERE project_id = ?
                   ORDER BY created_at DESC, file_id DESC""",
                (project_id,)
            )
            rows = cursor.fetchall()

            file_refs = [
                {"fileId": str(row[0]), "fileName": str(row[1])}
                for row in rows
            ]
            logger.debug(f"[SheetSelector] 找到 {len(file_refs)} 个文件（按时间排序，最新的在前）")
            for idx, file_ref in enumerate(file_refs):
                logger.debug(f"[SheetSelector]   {idx+1}. {file_ref['fileName']} ({file_ref['fileId']})")

            return file_refs
        finally:
            conn.close()

    def _intelligent_select(self, file_sheets_info: List[Dict], query: str) -> List[Dict[str, Any]]:
        """
        智能选择文件和Sheet
        简化版：如果只有一个文件，返回所有sheets；如果有多个文件，尝试根据文件名匹配
        """
        query_lower = query.lower()
        selected = []

        # 如果只有一个文件，返回所有sheets
        if len(file_sheets_info) == 1:
            file_info = file_sheets_info[0]
            sheets = self._select_relevant_sheets(file_info["sheets"], query)
            return [{
                "fileId": file_info.get("fileId"),
                "fileName": file_info["fileName"],
                "sheets": sheets
            }]

        # 多个文件时，尝试根据query中的关键词匹配文件名
        for file_info in file_sheets_info:
            file_name_lower = file_info["fileName"].lower()

            # 简单的关键词匹配
            # 如果query中包含文件名中的关键词，或者文件名包含query中的关键词
            if self._should_include_file(file_name_lower, query_lower):
                sheets = self._select_relevant_sheets(file_info["sheets"], query)
                selected.append({
                    "fileId": file_info.get("fileId"),
                    "fileName": file_info["fileName"],
                    "sheets": sheets
                })

        # 如果没有匹配到，返回第一个文件的所有sheets
        if not selected and file_sheets_info:
            sheets = self._select_relevant_sheets(file_sheets_info[0]["sheets"], query)
            selected.append({
                "fileId": file_sheets_info[0].get("fileId"),
                "fileName": file_sheets_info[0]["fileName"],
                "sheets": sheets
            })

        return selected

    def _select_relevant_sheets(self, sheet_names: List[str], query: str) -> List[str]:
        """
        Select sheets mentioned by the query.

        Many uploaded finance workbooks include helper sheets such as "平台匹配"
        beside the real data sheet. Loading every sheet into one DataFrame gives
        the model duplicate columns and makes wrong column/value mappings more
        likely. If the user names a sheet, only load that sheet; otherwise use
        all sheets from the selected file.
        """
        if not sheet_names:
            return []

        query_lower = query.lower()
        exact_matches = [
            sheet for sheet in sheet_names
            if sheet and sheet.lower() in query_lower
        ]
        if exact_matches:
            return exact_matches

        token_matches = []
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))
        for sheet in sheet_names:
            sheet_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", sheet.lower()))
            if sheet_tokens and sheet_tokens & query_tokens:
                token_matches.append(sheet)

        return token_matches or sheet_names

    def _should_include_file(self, file_name: str, query: str) -> bool:
        """判断是否应该包含该文件"""
        # 提取文件名中的关键词（去掉扩展名和常见分隔符）
        file_keywords = re.sub(r'[._\-\s]+', ' ', file_name.replace('.xlsx', '').replace('.xls', '')).split()

        # 检查query中是否包含文件名关键词
        for keyword in file_keywords:
            if len(keyword) > 2 and keyword in query:  # 关键词长度>2才匹配
                return True

        return False
