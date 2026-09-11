"""Project persistence operations."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sheetmind.database import get_db_connection, transaction
from sheetmind.models import Project


def _as_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class FileWriteResult:
    file_id: str
    file_name: str
    status: str


@dataclass(frozen=True)
class ProjectWriteResult:
    project_id: str
    created: bool
    files: list[FileWriteResult]


class ProjectService:
    def create_project(
        self,
        project_name: str,
        files: list[Any],
        session_id: str | None = None,
    ) -> str:
        name = project_name.strip()
        if not name:
            raise ValueError("Project name is required")

        project_id = str(uuid.uuid4())
        try:
            with transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM projects WHERE session_id IS ? AND project_name = ?",
                    (session_id, name),
                ).fetchone():
                    raise ValueError(f"Project name '{name}' already exists")
                conn.execute(
                    """
                    INSERT INTO projects (project_id, session_id, project_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, session_id, name, datetime.now()),
                )
                self._insert_files(conn, project_id, files)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project name '{name}' already exists") from exc
        return project_id

    def get_or_create_project(
        self,
        project_name: str,
        files: list[Any],
        session_id: str,
    ) -> ProjectWriteResult:
        name = project_name.strip()
        if not name:
            raise ValueError("Project name is required")

        with transaction() as conn:
            existing = conn.execute(
                """
                SELECT project_id FROM projects
                WHERE session_id = ? AND project_name = ?
                """,
                (session_id, name),
            ).fetchone()
            if existing:
                file_results = self._insert_files(conn, existing[0], files)
                return ProjectWriteResult(existing[0], False, file_results)

            project_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO projects (project_id, session_id, project_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, session_id, name, datetime.now()),
            )
            file_results = self._insert_files(conn, project_id, files)
            return ProjectWriteResult(project_id, True, file_results)

    def add_files_to_project(
        self,
        project_id: str,
        files: list[Any],
        session_id: str | None = None,
    ) -> list[FileWriteResult]:
        with transaction() as conn:
            query = "SELECT 1 FROM projects WHERE project_id = ?"
            params: tuple[Any, ...] = (project_id,)
            if session_id is not None:
                query += " AND session_id = ?"
                params += (session_id,)
            exists = conn.execute(query, params).fetchone()
            if not exists:
                raise ValueError(f"Project '{project_id}' not found")
            return self._insert_files(conn, project_id, files)

    @staticmethod
    def _insert_files(
        conn: sqlite3.Connection,
        project_id: str,
        files: list[Any],
    ) -> list[FileWriteResult]:
        results: list[FileWriteResult] = []
        for item in files or []:
            try:
                payload = base64.b64decode(item.base64, validate=True)
            except Exception as exc:
                raise ValueError(f"Invalid base64 data for '{item.fileName}'") from exc
            digest = hashlib.sha256(payload).hexdigest()
            existing = conn.execute(
                """
                SELECT file_id, content_sha256, file_data
                FROM files
                WHERE project_id = ? AND file_name = ?
                ORDER BY created_at DESC, file_id DESC
                LIMIT 1
                """,
                (project_id, item.fileName),
            ).fetchone()
            if existing:
                existing_digest = existing[1] or hashlib.sha256(existing[2]).hexdigest()
                if existing_digest == digest:
                    results.append(FileWriteResult(existing[0], item.fileName, "reused"))
                    continue
                conn.execute(
                    """
                    UPDATE files
                    SET file_data = ?, content_sha256 = ?, created_at = ?
                    WHERE file_id = ?
                    """,
                    (payload, digest, datetime.now(), existing[0]),
                )
                results.append(FileWriteResult(existing[0], item.fileName, "replaced"))
                continue

            file_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO files
                    (file_id, project_id, file_name, file_data, content_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_id, project_id, item.fileName, payload, digest, datetime.now()),
            )
            results.append(FileWriteResult(file_id, item.fileName, "created"))
        return results

    def get_project(
        self,
        project_id: str,
        session_id: str | None = None,
    ) -> Project | None:
        conn = get_db_connection()
        try:
            query = (
                "SELECT project_id, project_name, created_at, session_id "
                "FROM projects WHERE project_id = ?"
            )
            params: tuple[Any, ...] = (project_id,)
            if session_id is not None:
                query += " AND session_id = ?"
                params += (session_id,)
            row = conn.execute(query, params).fetchone()
            if not row:
                return None
            return Project(row[0], row[1], _as_datetime(row[2]), row[3])
        finally:
            conn.close()

    def list_projects(self, session_id: str | None = None) -> list[Project]:
        with transaction() as conn:
            if session_id is not None:
                self._claim_legacy_projects(conn, session_id)
            query = "SELECT project_id, project_name, created_at, session_id FROM projects"
            params: tuple[Any, ...] = ()
            if session_id is not None:
                query += " WHERE session_id = ?"
                params = (session_id,)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [Project(row[0], row[1], _as_datetime(row[2]), row[3]) for row in rows]

    @staticmethod
    def _claim_legacy_projects(conn: sqlite3.Connection, session_id: str) -> None:
        used_names = {
            row[0]
            for row in conn.execute(
                "SELECT project_name FROM projects WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        }
        legacy = conn.execute(
            """
            SELECT project_id, project_name
            FROM projects
            WHERE session_id IS NULL
            ORDER BY created_at DESC, project_id DESC
            """
        ).fetchall()
        for row in legacy:
            base_name = row[1]
            name = base_name
            suffix = 1
            while name in used_names:
                name = f"{base_name} ({suffix})"
                suffix += 1
            conn.execute(
                """
                UPDATE projects
                SET session_id = ?, project_name = ?
                WHERE project_id = ? AND session_id IS NULL
                """,
                (session_id, name, row[0]),
            )
            used_names.add(name)

    def rename_project(
        self,
        project_id: str,
        project_name: str,
        session_id: str | None = None,
    ) -> bool:
        name = project_name.strip()
        if not name:
            raise ValueError("Project name is required")
        try:
            with transaction() as conn:
                duplicate_query = (
                    "SELECT 1 FROM projects WHERE session_id IS ? "
                    "AND project_name = ? AND project_id <> ?"
                )
                duplicate = conn.execute(
                    duplicate_query, (session_id, name, project_id)
                ).fetchone()
                if duplicate:
                    raise ValueError(f"Project name '{name}' already exists")
                update_query = "UPDATE projects SET project_name = ? WHERE project_id = ?"
                params: tuple[Any, ...] = (name, project_id)
                if session_id is not None:
                    update_query += " AND session_id = ?"
                    params += (session_id,)
                cursor = conn.execute(update_query, params)
                return cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project name '{name}' already exists") from exc

    def delete_project(self, project_id: str, session_id: str | None = None) -> bool:
        with transaction() as conn:
            query = "DELETE FROM projects WHERE project_id = ?"
            params: tuple[Any, ...] = (project_id,)
            if session_id is not None:
                query += " AND session_id = ?"
                params += (session_id,)
            cursor = conn.execute(query, params)
            return cursor.rowcount > 0
