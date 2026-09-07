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
        for file_idx, file_name in enumerate(files):
            logger.debug(f"[SheetSelector]   处理文件 {file_idx+1}/{len(files)}: {file_name}")
            try:
                # 获取文件的sheet列表
                logger.debug(f"[SheetSelector]     获取文件字节...")
                file_bytes = self.excel_service.get_file_by_name(project_id, file_name)
                if file_bytes:
                    logger.debug(f"[SheetSelector]     解析 Excel Sheet 列表...")
                    with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                        sheet_names = xls.sheet_names
                        logger.debug(f"[SheetSelector]     ✅ 找到 {len(sheet_names)} 个 Sheet: {sheet_names[:5]}{'...' if len(sheet_names) > 5 else ''}")
                        file_sheets_info.append({
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

    def _get_project_files(self, project_id: str) -> List[str]:
        """
        获取项目下的文件名
        只返回最近上传的文件（避免加载历史文件）
        """
        import time
        logger.debug(f"[SheetSelector] _get_project_files: project_id={project_id}")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # 按创建时间排序，优先选择最近的文件
            # 获取所有文件，但按最新上传时间排序（避免加载很旧的历史文件）
            cursor.execute(
                """SELECT DISTINCT file_name, MAX(created_at) as latest_created_at
                   FROM files
                   WHERE project_id = ?
                   GROUP BY file_name
                   ORDER BY latest_created_at DESC""",
                (project_id,)
            )
            rows = cursor.fetchall()

            file_names = [row[0] for row in rows]
            logger.debug(f"[SheetSelector] 找到 {len(file_names)} 个文件（按时间排序，最新的在前）")
            for idx, file_name in enumerate(file_names):
                logger.debug(f"[SheetSelector]   {idx+1}. {file_name}")

            # ⚠️ 如果文件太多，只选择最近上传的（避免加载所有历史文件）
            # 例如：如果超过 3 个文件，只选择前 3 个（最新的）
            max_files = 3
            if len(file_names) > max_files:
                logger.debug(f"[SheetSelector] ⚠️ 文件数量超过 {max_files} 个，只选择最近 {max_files} 个文件")
                file_names = file_names[:max_files]

            return file_names
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
                    "fileName": file_info["fileName"],
                    "sheets": sheets
                })

        # 如果没有匹配到，返回第一个文件的所有sheets
        if not selected and file_sheets_info:
            sheets = self._select_relevant_sheets(file_sheets_info[0]["sheets"], query)
            selected.append({
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
