"""Anonymous browser-session persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sheetmind.database import get_db_connection, transaction


SESSION_COOKIE_NAME = "sheetmind_anonymous_session"
SESSION_LIFETIME_DAYS = 30


@dataclass(frozen=True)
class AnonymousSession:
    session_id: str
    expires_at: datetime
    is_new: bool = False


class AnonymousSessionService:
    def resolve(self, candidate: str | None) -> AnonymousSession:
        now = datetime.now(timezone.utc)
        if candidate:
            conn = get_db_connection()
            try:
                row = conn.execute(
                    "SELECT expires_at FROM anonymous_sessions WHERE session_id = ?",
                    (candidate,),
                ).fetchone()
            finally:
                conn.close()
            if row:
                expires_at = datetime.fromisoformat(str(row[0]))
                if expires_at > now:
                    renewed = now + timedelta(days=SESSION_LIFETIME_DAYS)
                    with transaction() as conn:
                        conn.execute(
                            """
                            UPDATE anonymous_sessions
                            SET last_seen_at = ?, expires_at = ?
                            WHERE session_id = ?
                            """,
                            (now.isoformat(), renewed.isoformat(), candidate),
                        )
                    return AnonymousSession(candidate, renewed)

        session_id = str(uuid.uuid4())
        expires_at = now + timedelta(days=SESSION_LIFETIME_DAYS)
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO anonymous_sessions
                    (session_id, created_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, now.isoformat(), now.isoformat(), expires_at.isoformat()),
            )
        return AnonymousSession(session_id, expires_at, is_new=True)
