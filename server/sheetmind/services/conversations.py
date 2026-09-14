"""
对话历史管理服务
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sheetmind.database import get_db_connection


class ConversationService:
    """对话历史服务"""

    def add_message(
        self,
        task_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加对话消息
        Args:
            task_id: 任务ID
            role: 角色 ("user" 或 "assistant")
            content: 消息内容
        Returns:
            conversation_id
        """
        conversation_id = str(uuid.uuid4())
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO conversations (
                    conversation_id, task_id, role, content, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    task_id,
                    role,
                    content,
                    json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
                    datetime.now(),
                ),
            )
            conn.commit()
            return conversation_id
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def get_conversation_history(
        self,
        task_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        获取对话历史
        Args:
            task_id: 任务ID
            limit: 返回的最大消息数
        Returns:
            对话历史列表 [{"role": "user/assistant", "content": "...", "createdAt": "..."}]
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT role, content, metadata, created_at
                FROM conversations
                WHERE task_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (task_id, limit)
            )
            rows = cursor.fetchall()

            return [
                {
                    "role": row[0],
                    "content": row[1],
                    "metadata": self._parse_metadata(row[2]),
                    "createdAt": (
                        row[3].isoformat()
                        if row[3] and hasattr(row[3], "isoformat")
                        else str(row[3]) if row[3] else None
                    ),
                }
                for row in rows
            ]
        finally:
            conn.close()

    @staticmethod
    def _parse_metadata(raw: Any) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def clear_conversation(self, task_id: str) -> bool:
        """清空对话历史"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM conversations WHERE task_id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
