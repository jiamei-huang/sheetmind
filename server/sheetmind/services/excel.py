"""
Excel解析服务层
处理Excel文件的解析、sheet信息提取等业务逻辑
"""
import io
import pandas as pd
from typing import List, Dict, Any, Optional
from sheetmind.database import get_db_connection
from sheetmind.logging import get_logger

logger = get_logger("excel_service")


class ExcelService:
    """Excel解析服务"""

    def get_file_by_name(self, project_id: str, file_name: str) -> Optional[bytes]:
        """
        根据项目ID和文件名获取文件数据
        Args:
            project_id: 项目ID
            file_name: 文件名
        Returns:
            文件字节数据，如果不存在返回None
        """
        import time
        logger.debug(f"[ExcelService] get_file_by_name: project_id={project_id}, file_name={file_name}")

        logger.debug(f"[ExcelService] Step 1: 连接数据库...")
        conn = get_db_connection()
        cursor = conn.cursor()
        logger.debug(f"[ExcelService] ✅ 数据库连接成功")

        try:
            logger.debug(f"[ExcelService] Step 2: 执行 SQL 查询...")
            query_start = time.perf_counter()
            cursor.execute(
                """
                SELECT file_data
                FROM files
                WHERE project_id = ? AND file_name = ?
                ORDER BY created_at DESC, file_id DESC
                LIMIT 1
                """,
                (project_id, file_name)
            )
            query_elapsed = time.perf_counter() - query_start
            logger.debug(f"[ExcelService] ✅ SQL 查询完成，耗时: {query_elapsed:.2f}秒")

            logger.debug(f"[ExcelService] Step 3: 获取查询结果...")
            fetch_start = time.perf_counter()
            row = cursor.fetchone()
            fetch_elapsed = time.perf_counter() - fetch_start
            logger.debug(f"[ExcelService] ✅ 结果获取完成，耗时: {fetch_elapsed:.2f}秒")

            if row and row[0]:
                file_size_mb = len(row[0]) / (1024 * 1024)
                logger.debug(f"[ExcelService] ✅ 文件数据获取成功，大小: {file_size_mb:.2f} MB")
                return row[0]
            else:
                logger.debug(f"[ExcelService] ⚠️ 未找到文件数据")
                return None
        finally:
            logger.debug(f"[ExcelService] Step 4: 关闭数据库连接...")
            conn.close()
            logger.debug(f"[ExcelService] ✅ 数据库连接已关闭")

    def parse_excel(self, project_id: str, file_name: str) -> Dict[str, Any]:
        """
        解析Excel文件，返回sheet列表、字段和数据预览
        Args:
            project_id: 项目ID
            file_name: 文件名
        Returns:
            包含sheet信息的字典
        """
        # 获取文件数据
        file_bytes = self.get_file_by_name(project_id, file_name)
        if not file_bytes:
            raise ValueError(f"File '{file_name}' not found in project '{project_id}'")

        # 解析Excel
        sheets_info = []

        try:
            with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                sheet_names = xls.sheet_names

                for sheet_name in sheet_names:
                    try:
                        # 读取前20行数据，确保读取所有列
                        # 对于大文件，使用 dtype=object 确保所有列都被读取，不进行类型推断
                        try:
                            # 方法1：使用 dtype=object 读取所有列（推荐，确保所有列都被读取）
                            df = pd.read_excel(
                                xls,
                                sheet_name=sheet_name,
                                nrows=50,
                                engine='openpyxl',  # 使用 openpyxl 引擎，对大文件支持更好
                                dtype=object,  # 使用 object 类型，确保所有列都被读取，不进行类型推断
                                na_filter=False  # 不进行 NA 值过滤，提高性能
                            )
                        except Exception as e1:
                            logger.debug(f"[ExcelService] Warning: Failed to read with openpyxl engine and dtype=object, trying without dtype: {e1}")
                            try:
                                # 方法2：不使用 dtype，让 pandas 自动推断
                                df = pd.read_excel(
                                    xls,
                                    sheet_name=sheet_name,
                                    nrows=50,
                                    engine='openpyxl'
                                )
                            except Exception as e2:
                                logger.debug(f"[ExcelService] Warning: Failed to read with openpyxl engine, trying default engine: {e2}")
                                # 方法3：使用默认引擎
                                df = pd.read_excel(
                                    xls,
                                    sheet_name=sheet_name,
                                    nrows=50
                                )

                        # 获取列名（字段），强制转为字符串（避免 pandas 对无表头 Excel 生成 int 列名导致 Pydantic 验证失败）
                        columns = [str(c) for c in df.columns.tolist()]

                        # Keep structural diagnostics at debug level for malformed workbooks.
                        logger.debug(f"[ExcelService] Sheet '{sheet_name}': {len(df)} rows, {len(columns)} columns")
                        logger.debug(f"[ExcelService] Columns: {columns[:10]}..." if len(columns) > 10 else f"[ExcelService] Columns: {columns}")

                        # 将DataFrame转换为字典列表（处理NaN值）
                        # 注意：columns 已是 List[str]，但 DataFrame 原始列名可能是 int/float，
                        # 所以用 df.columns（原始）取值，用 str(orig_col) 作为 dict key
                        preview_data = []
                        for idx, row in df.iterrows():
                            row_dict = {}
                            for orig_col, str_col in zip(df.columns, columns):
                                value = row[orig_col]
                                # 处理NaN、None等特殊值
                                if pd.isna(value):
                                    row_dict[str_col] = None
                                else:
                                    # 确保值被正确转换（处理各种数据类型）
                                    if isinstance(value, (int, float)):
                                        # 如果是数字，保持原样
                                        row_dict[str_col] = value
                                    elif isinstance(value, str):
                                        # 如果是字符串，保持原样
                                        row_dict[str_col] = value
                                    else:
                                        # 其他类型转换为字符串
                                        row_dict[str_col] = str(value) if value is not None else None
                            preview_data.append(row_dict)

                        if preview_data:
                            first_row_keys = list(preview_data[0].keys())
                            logger.debug(f"[ExcelService] First row has {len(first_row_keys)} columns: {first_row_keys[:5]}...")
                            first_row_sample = {k: preview_data[0][k] for k in first_row_keys[:3]}
                            logger.debug(f"[ExcelService] First row sample: {first_row_sample}")

                        sheets_info.append({
                            "sheetName": sheet_name,
                            "columns": columns,
                            "preview": preview_data,
                            "rowCount": len(df)
                        })
                    except Exception as e:
                        # 如果某个sheet解析失败，记录错误但继续处理其他sheet
                        logger.debug(f"Error parsing sheet '{sheet_name}' in file '{file_name}': {e}")
                        sheets_info.append({
                            "sheetName": sheet_name,
                            "columns": [],
                            "preview": [],
                            "rowCount": 0,
                            "error": str(e)
                        })
        except Exception as e:
            raise ValueError(f"Failed to parse Excel file '{file_name}': {str(e)}")

        return {
            "fileName": file_name,
            "sheets": sheets_info
        }

    def delete_file(self, project_id: str, file_name: str) -> bool:
        """
        删除项目中的指定文件
        Args:
            project_id: 项目ID
            file_name: 文件名
        Returns:
            是否删除成功
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # 检查项目是否存在
            cursor.execute("SELECT project_id FROM projects WHERE project_id = ?", (project_id,))
            if not cursor.fetchone():
                raise ValueError(f"Project '{project_id}' not found")

            # 删除文件（级联删除相关的sheets）
            cursor.execute(
                "DELETE FROM files WHERE project_id = ? AND file_name = ?",
                (project_id, file_name)
            )
            conn.commit()
            return cursor.rowcount > 0
        except ValueError:
            raise
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
