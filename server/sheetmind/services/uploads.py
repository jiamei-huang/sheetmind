"""
文件上传处理服务
处理Excel文件的上传、解析、验证等业务逻辑
"""
import base64
import io
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from sheetmind.config import settings
from sheetmind.exceptions import FileTooLargeError
from sheetmind.logging import get_logger

logger = get_logger("file_uploader")

SUPPORTED_EXCEL_EXTENSIONS = {".xlsx", ".xls"}


class FileUploader:
    """文件上传处理服务"""

    def __init__(self, max_file_size_bytes: Optional[int] = None) -> None:
        self.max_file_size_bytes = (
            max_file_size_bytes
            if max_file_size_bytes is not None
            else settings.max_excel_file_size_bytes
        )

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
            self.validate_base64_size(file_upload.base64, file_upload.fileName)
            file_bytes = self.decode_base64_file(file_upload.base64)
            sheet_names = self.validate_workbook(file_bytes, file_upload.fileName)
            file_info_list.append({
                "fileName": file_upload.fileName,
                "sheets": sheet_names
            })
            logger.info(f"成功解析文件: {file_upload.fileName}, 包含 {len(sheet_names)} 个sheet")

        return file_info_list

    def validate_base64_size(self, base64_data: str, file_name: str) -> None:
        """Reject oversized uploads before allocating decoded workbook bytes."""
        encoded = str(base64_data or "")
        padding = len(encoded) - len(encoded.rstrip("="))
        estimated_size = max(0, (len(encoded) * 3) // 4 - padding)
        if estimated_size <= self.max_file_size_bytes:
            return

        limit_mb = self.max_file_size_bytes / (1024 * 1024)
        raise FileTooLargeError(
            f'File "{file_name}" is too large. The maximum Excel file size is {limit_mb:g} MB.',
            error_code="FILE_TOO_LARGE",
        )

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
                f"Unsupported file \"{file_name}\". SheetMind currently supports Excel files only "
                "(.xlsx, .xls)."
            )
        if not file_bytes:
            raise ValueError(f"File \"{file_name}\" is empty and cannot be uploaded as an Excel workbook.")
        try:
            with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                sheet_names = [str(sheet) for sheet in xls.sheet_names]
        except Exception as exc:
            raise ValueError(
                f"File \"{file_name}\" is not a valid Excel workbook or may be corrupted."
            ) from exc
        if not sheet_names:
            raise ValueError(f"Excel file \"{file_name}\" does not contain any sheets.")
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
