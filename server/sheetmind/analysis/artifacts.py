"""Persistent full-result artifacts for follow-ups and exports."""
from __future__ import annotations

import gzip
import io
import logging
import sqlite3
import uuid
from typing import Optional

import pandas as pd

from sheetmind.database import get_db_connection


logger = logging.getLogger(__name__)


class AnalysisArtifactStore:
    """Store complete result DataFrames separately from bounded UI previews."""

    def save(self, task_id: str, question_id: str, df: pd.DataFrame) -> Optional[str]:
        artifact_id = uuid.uuid4().hex
        payload = gzip.compress(
            df.to_json(orient="table", date_format="iso").encode("utf-8")
        )
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO analysis_artifacts
                    (artifact_id, task_id, question_id, payload, row_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (artifact_id, task_id, question_id, payload, len(df)),
            )
            conn.commit()
        except sqlite3.DatabaseError as exc:
            logger.warning("Could not persist analysis artifact: %s", exc)
            return None
        finally:
            conn.close()
        return artifact_id

    def load(self, artifact_id: str, task_id: Optional[str] = None) -> Optional[pd.DataFrame]:
        conn = get_db_connection()
        try:
            query = "SELECT payload FROM analysis_artifacts WHERE artifact_id = ?"
            params: tuple[str, ...] = (artifact_id,)
            if task_id is not None:
                query += " AND task_id = ?"
                params += (task_id,)
            row = conn.execute(query, params).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            raw = gzip.decompress(row[0]).decode("utf-8")
            return pd.read_json(io.StringIO(raw), orient="table")
        except Exception:
            return None

    def to_excel(self, artifact_id: str, task_id: str) -> Optional[bytes]:
        df = self.load(artifact_id, task_id)
        if df is None:
            return None
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Analysis Data", index=False)
        return output.getvalue()


_default_store = AnalysisArtifactStore()


def get_artifact_store() -> AnalysisArtifactStore:
    return _default_store
