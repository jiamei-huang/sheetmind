"""
Excel解析服务层
处理Excel文件的解析、sheet信息提取等业务逻辑
"""
import io
import pandas as pd
from datetime import date, datetime, time
from numbers import Real
from typing import List, Dict, Any, Optional
from openpyxl.utils.datetime import from_excel
from sheetmind.database import get_db_connection
from sheetmind.logging import get_logger

logger = get_logger("excel_service")


class ExcelService:
    """Excel解析服务"""

    def get_file_by_id(self, project_id: str, file_id: str) -> Optional[bytes]:
        """Return one exact workbook version by its stable identifier."""
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT file_data FROM files WHERE project_id = ? AND file_id = ?",
                (project_id, file_id),
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()

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

    def parse_excel(
        self,
        project_id: str,
        file_name: str,
        file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        解析Excel文件，返回sheet列表、字段和数据预览
        Args:
            project_id: 项目ID
            file_name: 文件名
        Returns:
            包含sheet信息的字典
        """
        # 获取文件数据
        file_bytes = (
            self.get_file_by_id(project_id, file_id)
            if file_id
            else self.get_file_by_name(project_id, file_name)
        )
        if not file_bytes:
            raise ValueError(f"File '{file_name}' not found in project '{project_id}'")

        # 解析Excel
        sheets_info = []

        try:
            with pd.ExcelFile(io.BytesIO(file_bytes)) as xls:
                sheet_names = xls.sheet_names
                workbook_epoch = getattr(xls.book, "epoch", None)

                for sheet_name in sheet_names:
                    try:
                        header_row = self._detect_header_row(file_bytes, sheet_name)
                        try:
                            df = pd.read_excel(
                                xls,
                                sheet_name=sheet_name,
                                header=header_row,
                                nrows=50,
                                dtype=object,
                                na_filter=False,
                            )
                        except Exception as exc:
                            logger.debug(
                                "[ExcelService] dtype-preserving preview read failed; retrying: %s",
                                exc,
                            )
                            df = pd.read_excel(
                                xls,
                                sheet_name=sheet_name,
                                header=header_row,
                                nrows=50,
                            )

                        # 获取列名（字段），强制转为字符串（避免 pandas 对无表头 Excel 生成 int 列名导致 Pydantic 验证失败）
                        columns = [str(c) for c in df.columns.tolist()]

                        # Keep structural diagnostics at debug level for malformed workbooks.
                        logger.debug(f"[ExcelService] Sheet '{sheet_name}': {len(df)} rows, {len(columns)} columns")
                        logger.debug(f"[ExcelService] Columns: {columns[:10]}..." if len(columns) > 10 else f"[ExcelService] Columns: {columns}")

                        preview_data = []
                        for _, row in df.iterrows():
                            row_dict = {}
                            for orig_col, str_col in zip(df.columns, columns):
                                row_dict[str_col] = self._preview_value(
                                    row[orig_col],
                                    str_col,
                                    workbook_epoch,
                                )
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
            "fileId": file_id,
            "fileName": file_name,
            "sheets": sheets_info
        }

    @staticmethod
    def _detect_header_row(file_bytes: bytes, sheet_name: str) -> int:
        """Find a business header below optional title or summary rows."""
        try:
            raw = pd.read_excel(
                io.BytesIO(file_bytes),
                sheet_name=sheet_name,
                header=None,
                nrows=15,
            )
        except Exception:
            return 0
        if raw.empty:
            return 0

        keywords = (
            "月份", "日期", "时间", "平台", "店铺", "产品", "sku", "物流商",
            "费用", "金额", "币别", "汇率", "数量", "国家", "订单",
        )
        total_columns = max(raw.shape[1], 1)
        for row_index, row in raw.iterrows():
            values = [value for value in row.tolist() if pd.notna(value) and str(value).strip()]
            if not values or len(values) / total_columns < 0.4:
                continue
            strings = [str(value).strip() for value in values if isinstance(value, str)]
            if len(strings) / len(values) < 0.5:
                continue
            keyword_hits = sum(
                any(keyword in label.lower() for keyword in keywords)
                for label in strings
            )
            if keyword_hits >= 2 or len(set(strings)) == len(strings):
                return int(row_index)
        return 0

    @classmethod
    def _preview_value(cls, value: Any, column_name: str, workbook_epoch: Any) -> Any:
        """Serialize previews without exposing Excel date serial numbers."""
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return None
        if isinstance(value, (datetime, date, time)):
            return cls._format_temporal_value(value, column_name)
        if cls._is_temporal_column(column_name) and isinstance(value, Real) and not isinstance(value, bool):
            serial = float(value)
            if 1 <= serial <= 80000:
                try:
                    converted = from_excel(serial, epoch=workbook_epoch) if workbook_epoch else from_excel(serial)
                    return cls._format_temporal_value(converted, column_name)
                except (TypeError, ValueError, OverflowError):
                    pass
        if isinstance(value, Real) or isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _is_temporal_column(column_name: str) -> bool:
        normalized = str(column_name).strip().lower()
        return any(token in normalized for token in ("日期", "时间", "月份", "年月", "date", "month", "time"))

    @staticmethod
    def _format_temporal_value(value: Any, column_name: str) -> str:
        normalized = str(column_name).strip().lower()
        if isinstance(value, time):
            return value.isoformat(timespec="seconds")
        if isinstance(value, datetime):
            if "月份" in normalized or "年月" in normalized or "month" in normalized:
                return value.strftime("%Y-%m")
            if value.time() != time.min and ("时间" in normalized or "time" in normalized):
                return value.isoformat(sep=" ", timespec="seconds")
            return value.date().isoformat()
        if isinstance(value, date):
            if "月份" in normalized or "年月" in normalized or "month" in normalized:
                return value.strftime("%Y-%m")
            return value.isoformat()
        return str(value)

    def delete_file_by_id(self, project_id: str, file_id: str) -> bool:
        """Delete one exact workbook version without affecting same-name files."""
        conn = get_db_connection()
        try:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone():
                raise ValueError(f"Project '{project_id}' not found")
            cursor = conn.execute(
                "DELETE FROM files WHERE project_id = ? AND file_id = ?",
                (project_id, file_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
