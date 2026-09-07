"""Project persistence operations."""

from __future__ import annotations

import base64
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from sheetmind.database import get_db_connection, transaction
from sheetmind.models import Project


def _as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class ProjectService:
    def create_project(self, project_name: str, files: list[Any]) -> str:
        name = project_name.strip()
        if not name:
            raise ValueError("Project name is required")

        project_id = str(uuid.uuid4())
        try:
            with transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM projects WHERE project_name = ?", (name,)
                ).fetchone():
                    raise ValueError(f"Project name '{name}' already exists")
                conn.execute(
                    "INSERT INTO projects (project_id, project_name, created_at) VALUES (?, ?, ?)",
                    (project_id, name, datetime.now()),
                )
                self._insert_files(conn, project_id, files)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project name '{name}' already exists") from exc
        return project_id

    def add_files_to_project(self, project_id: str, files: list[Any]) -> None:
        with transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if not exists:
                raise ValueError(f"Project '{project_id}' not found")
            self._insert_files(conn, project_id, files)

    @staticmethod
    def _insert_files(conn, project_id: str, files: list[Any]) -> None:
        for item in files or []:
            try:
                payload = base64.b64decode(item.base64, validate=True)
            except Exception as exc:
                raise ValueError(f"Invalid base64 data for '{item.fileName}'") from exc
            conn.execute(
                """
                INSERT INTO files (file_id, project_id, file_name, file_data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), project_id, item.fileName, payload, datetime.now()),
            )

    def get_project(self, project_id: str) -> Project | None:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT project_id, project_name, created_at FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return Project(row[0], row[1], _as_datetime(row[2]))
        finally:
            conn.close()

    def list_projects(self) -> list[Project]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT project_id, project_name, created_at FROM projects ORDER BY created_at DESC"
            ).fetchall()
            return [Project(row[0], row[1], _as_datetime(row[2])) for row in rows]
        finally:
            conn.close()

    def rename_project(self, project_id: str, project_name: str) -> bool:
        name = project_name.strip()
        if not name:
            raise ValueError("Project name is required")
        try:
            with transaction() as conn:
                duplicate = conn.execute(
                    "SELECT 1 FROM projects WHERE project_name = ? AND project_id <> ?",
                    (name, project_id),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"Project name '{name}' already exists")
                cursor = conn.execute(
                    "UPDATE projects SET project_name = ? WHERE project_id = ?",
                    (name, project_id),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project name '{name}' already exists") from exc

    def delete_project(self, project_id: str) -> bool:
        with transaction() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            return cursor.rowcount > 0
