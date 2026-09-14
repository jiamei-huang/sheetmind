"""Anonymous browser-session persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sheetmind.database import transaction


SESSION_COOKIE_NAME = "sheetmind_anonymous_session"


@dataclass(frozen=True)
class AnonymousSession:
    session_id: str
    expires_at: datetime
    is_new: bool = False


class AnonymousSessionService:
    def __init__(self, lifetime_days: int = 30) -> None:
        if lifetime_days < 1:
            raise ValueError("Anonymous session lifetime must be at least 1 day")
        self.lifetime_days = lifetime_days

    def resolve(self, candidate: str | None) -> AnonymousSession:
        now = datetime.now(timezone.utc)
        with transaction() as conn:
            conn.execute(
                "DELETE FROM anonymous_sessions WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            if candidate:
                row = conn.execute(
                    "SELECT expires_at FROM anonymous_sessions WHERE session_id = ?",
                    (candidate,),
                ).fetchone()
                if row:
                    expires_at = datetime.fromisoformat(str(row[0]))
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at > now:
                        renewed = now + timedelta(days=self.lifetime_days)
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
            expires_at = now + timedelta(days=self.lifetime_days)
            conn.execute(
                """
                INSERT INTO anonymous_sessions
                    (session_id, created_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, now.isoformat(), now.isoformat(), expires_at.isoformat()),
            )
        return AnonymousSession(session_id, expires_at, is_new=True)

    def delete(self, session_id: str) -> list[str]:
        """Delete one anonymous workspace and return its task ids for cache eviction."""
        with transaction() as conn:
            task_ids = [
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT tasks.task_id
                    FROM tasks
                    JOIN projects ON projects.project_id = tasks.project_id
                    WHERE projects.session_id = ?
                    """,
                    (session_id,),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM anonymous_sessions WHERE session_id = ?",
                (session_id,),
            )
        return task_ids
