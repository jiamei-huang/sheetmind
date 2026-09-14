"""SQLite connection and schema management."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sheetmind.db"


def database_path() -> Path:
    return Path(os.getenv("SHEETMIND_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()


def get_db_connection() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anonymous_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()
        _ensure_session_scoped_projects(conn)

        statements = (
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            session_id TEXT,
            project_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES anonymous_sessions(session_id) ON DELETE CASCADE,
            UNIQUE (session_id, project_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_data BLOB NOT NULL,
            content_sha256 TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
            UNIQUE (project_id, task_number)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_contexts (
            task_id TEXT PRIMARY KEY,
            context_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            payload BLOB NOT NULL,
            row_count INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_traces (
            trace_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_files_project_id ON files(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_task_id ON conversations(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_artifacts_task_id ON analysis_artifacts(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_traces_task_id ON analysis_traces(task_id)",
        )

        for statement in statements:
            conn.execute(statement)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "title" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN title TEXT")
        file_columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
        if "content_sha256" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN content_sha256 TEXT")
        _backfill_file_hashes(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_project_content "
            "ON files(project_id, file_name, content_sha256)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_session_id ON projects(session_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anonymous_sessions_expires_at "
            "ON anonymous_sessions(expires_at)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_session_scoped_projects(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if not table:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    if "session_id" in columns:
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE projects_new (
                project_id TEXT PRIMARY KEY,
                session_id TEXT,
                project_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES anonymous_sessions(session_id) ON DELETE CASCADE,
                UNIQUE (session_id, project_name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO projects_new (project_id, session_id, project_name, created_at)
            SELECT project_id, NULL, project_name, created_at FROM projects
            """
        )
        conn.execute("DROP TABLE projects")
        conn.execute("ALTER TABLE projects_new RENAME TO projects")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _backfill_file_hashes(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'files'"
    ).fetchone():
        return

    rows = conn.execute(
        """
        SELECT file_id, project_id, file_name, file_data, content_sha256
        FROM files
        ORDER BY created_at DESC, file_id DESC
        """
    ).fetchall()
    seen_files: set[tuple[str, str, str]] = set()
    for row in rows:
        digest = row[4] or hashlib.sha256(row[3]).hexdigest()
        file_key = (row[1], row[2], digest)
        if file_key in seen_files:
            conn.execute("DELETE FROM files WHERE file_id = ?", (row[0],))
            continue
        seen_files.add(file_key)
        if row[4] != digest:
            conn.execute(
                "UPDATE files SET content_sha256 = ? WHERE file_id = ?",
                (digest, row[0]),
            )


def get_db() -> sqlite3.Connection:
    """Compatibility alias for code that needs an explicit connection."""
    return get_db_connection()
