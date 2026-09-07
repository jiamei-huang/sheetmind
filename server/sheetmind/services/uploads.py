"""
文件上传处理服务
处理Excel文件的上传、解析、验证等业务逻辑
"""
import base64
import io
from typing import List, Dict, Any
import pandas as pd
from sheetmind.logging import get_logger

logger = get_logger("file_uploader")


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
            try:
                # 解码base64文件数据
                file_bytes = base64.b64decode(file_upload.base64)

                # 读取Excel文件获取sheet列表
                with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                    sheet_names = xls.sheet_names

                file_info_list.append({
                    "fileName": file_upload.fileName,
                    "sheets": sheet_names
                })
                logger.info(f"成功解析文件: {file_upload.fileName}, 包含 {len(sheet_names)} 个sheet")
            except Exception as e:
                # 如果某个文件解析失败，记录错误但继续处理其他文件
                logger.warning(f"解析文件失败 {file_upload.fileName}: {e}")
                file_info_list.append({
                    "fileName": file_upload.fileName,
                    "sheets": []
                })

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
            # 尝试读取Excel文件
            with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                # 检查是否有至少一个sheet
                if len(xls.sheet_names) == 0:
                    logger.warning(f"文件 {file_name} 没有包含任何sheet")
                    return False
            return True
        except Exception as e:
            logger.error(f"文件验证失败 {file_name}: {e}")
            return False

    def decode_base64_file(self, base64_data: str) -> bytes:
        """
        解码base64编码的文件数据

        Args:
            base64_data: base64编码的字符串

        Returns:
            解码后的文件字节数据
        """
        try:
            return base64.b64decode(base64_data)
        except Exception as e:
            logger.error(f"Base64解码失败: {e}")
            raise ValueError(f"无效的base64数据: {e}")
