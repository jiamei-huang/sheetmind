"""Task persistence operations."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

from sheetmind.database import get_db_connection, transaction
from sheetmind.models import Task


def _to_task(row: Any) -> Task:
    created_at = datetime.fromisoformat(row[4]) if isinstance(row[4], str) else row[4]
    title = row[5] if len(row) > 5 else None
    return Task(row[0], row[1], row[2], row[3], created_at, title)


class TaskService:
    _SELECT = "task_id, project_id, task_number, status, created_at, title"
    _STATUSES = {"draft", "running", "completed", "failed"}

    def create_task(self, project_id: str) -> Task:
        for _ in range(3):
            try:
                with transaction() as conn:
                    if not conn.execute(
                        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
                    ).fetchone():
                        raise ValueError(f"Project '{project_id}' not found")
                    row = conn.execute(
                        "SELECT COALESCE(MAX(task_number), 0) + 1 FROM tasks WHERE project_id = ?",
                        (project_id,),
                    ).fetchone()
                    task = Task(
                        task_id=str(uuid.uuid4()),
                        project_id=project_id,
                        task_number=row[0],
                        status="draft",
                        created_at=datetime.now(),
                    )
                    conn.execute(
                        """
                        INSERT INTO tasks (task_id, project_id, task_number, status, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            task.task_id,
                            task.project_id,
                            task.task_number,
                            task.status,
                            task.created_at,
                        ),
                    )
                    return task
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not allocate a task number")

    def get_task(self, task_id: str) -> Task | None:
        conn = get_db_connection()
        try:
            row = conn.execute(
                f"SELECT {self._SELECT} FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return _to_task(row) if row else None
        finally:
            conn.close()

    def list_tasks(self, project_id: str) -> list[Task]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                f"SELECT {self._SELECT} FROM tasks WHERE project_id = ? ORDER BY task_number ASC",
                (project_id,),
            ).fetchall()
            return [_to_task(row) for row in rows]
        finally:
            conn.close()

    def rename_task(self, task_id: str, title: str) -> bool:
        with transaction() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET title = ? WHERE task_id = ?", (title.strip() or None, task_id)
            )
            return cursor.rowcount > 0

    def update_status(self, task_id: str, status: str) -> bool:
        if status not in self._STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        with transaction() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (status, task_id),
            )
            return cursor.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with transaction() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            return cursor.rowcount > 0
