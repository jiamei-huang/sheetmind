"""
文件上传处理服务
处理Excel文件的上传、解析、验证等业务逻辑
"""
import base64
import io
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
from sheetmind.logging import get_logger

logger = get_logger("file_uploader")

SUPPORTED_EXCEL_EXTENSIONS = {".xlsx", ".xls"}


class FileUploader:
    """文件上传处理服务"""

    def parse_files(self, files: List[Any]) -> List[Dict[str, Any]]:
        """
        解析base64编码的文件列表，提取Excel文件的sheet信息

        Args:
            files: 文件列表，每个文件包含 fileName 和 base64 字段

        Returns:
            文件信息列表，每个元素包含 fileName 和 sheets（sheet名称列表）
        """
        file_info_list = []
        files_to_process = files or []

        for file_upload in files_to_process:
            file_bytes = self.decode_base64_file(file_upload.base64)
            sheet_names = self.validate_workbook(file_bytes, file_upload.fileName)
            file_info_list.append({
                "fileName": file_upload.fileName,
                "sheets": sheet_names
            })
            logger.info(f"成功解析文件: {file_upload.fileName}, 包含 {len(sheet_names)} 个sheet")

        return file_info_list

    def validate_file(self, file_bytes: bytes, file_name: str) -> bool:
        """
        验证文件是否为有效的Excel文件

        Args:
            file_bytes: 文件字节数据
            file_name: 文件名

        Returns:
            是否为有效的Excel文件
        """
        try:
            self.validate_workbook(file_bytes, file_name)
            return True
        except ValueError as e:
            logger.error(f"文件验证失败 {file_name}: {e}")
            return False

    @staticmethod
    def validate_workbook(file_bytes: bytes, file_name: str) -> List[str]:
        suffix = Path(file_name).suffix.lower()
        if suffix not in SUPPORTED_EXCEL_EXTENSIONS:
            raise ValueError(
                f"不支持文件“{file_name}”。当前仅支持 Excel 文件"
                "（.xlsx、.xls）。"
            )
        if not file_bytes:
            raise ValueError(f"文件“{file_name}”为空，无法作为 Excel 工作簿上传。")
        try:
            with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                sheet_names = [str(sheet) for sheet in xls.sheet_names]
        except Exception as exc:
            raise ValueError(
                f"文件“{file_name}”不是有效的 Excel 工作簿，或文件已损坏。"
            ) from exc
        if not sheet_names:
            raise ValueError(f"Excel 文件“{file_name}”不包含任何 Sheet。")
        return sheet_names

    def decode_base64_file(self, base64_data: str) -> bytes:
        """
        解码base64编码的文件数据

        Args:
            base64_data: base64编码的字符串

        Returns:
            解码后的文件字节数据
        """
        try:
            return base64.b64decode(base64_data, validate=True)
        except Exception as e:
            logger.error(f"Base64解码失败: {e}")
            raise ValueError(f"无效的base64数据: {e}")
