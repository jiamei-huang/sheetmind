"""
SheetMind analysis context store.
================================
Maintains AnalysisContext objects keyed by task_id for multi-turn sessions,
with optional SQLite persistence across process restarts.

Design:
- OrderedDict as LRU backing store (oldest at front, newest at back)
- threading.Lock for thread-safety (FastAPI may run sync code in threadpool)
- Max 200 entries; evicts least-recently-used when full
- Module-level singleton via get_context_store()

Usage:
    store = get_context_store()
    ctx = store.get_or_create(task_id, project_id)
    # ... run pipeline on ctx ...
    store.set(task_id, ctx)   # not needed if mutated in place, but explicit is fine
"""
from __future__ import annotations

import threading
import logging
from collections import OrderedDict
from typing import Optional

from .context import AnalysisContext
from sheetmind.database import get_db_connection

logger = logging.getLogger(__name__)

_MAX_SIZE = 200


class ContextStore:
    """
    Thread-safe LRU cache for AnalysisContext objects.

    Keys are task_id strings. On get/set, the entry is promoted to MRU
    (back of the OrderedDict).  When max_size is exceeded, the LRU entry
    (front of the OrderedDict) is evicted. Persistent stores lazily restore
    cache misses from SQLite.
    """

    def __init__(self, max_size: int = _MAX_SIZE, persist: bool = False) -> None:
        self._max_size = max_size
        self._persist_enabled = persist
        self._store: OrderedDict[str, AnalysisContext] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Optional[AnalysisContext]:
        """Return the context for task_id, or None if not present.  Promotes to MRU."""
        with self._lock:
            if task_id in self._store:
                # Promote to MRU
                self._store.move_to_end(task_id)
                return self._store[task_id]
        if not self._persist_enabled:
            return None
        ctx = self._load_persisted(task_id)
        if ctx is not None:
            self._remember(task_id, ctx)
        return ctx

    def set(self, task_id: str, ctx: AnalysisContext) -> None:
        """Store/update a context.  Promotes to MRU; evicts LRU if over capacity."""
        self._remember(task_id, ctx)
        if self._persist_enabled:
            self._persist(ctx)

    def _remember(self, task_id: str, ctx: AnalysisContext) -> None:
        with self._lock:
            if task_id in self._store:
                self._store.move_to_end(task_id)
            else:
                if len(self._store) >= self._max_size:
                    # Evict least-recently-used (front)
                    self._store.popitem(last=False)
            self._store[task_id] = ctx

    def get_or_create(self, task_id: str, project_id: str) -> AnalysisContext:
        """
        Return the existing context for task_id, or create a fresh one.

        This is the primary entry point for multi-turn support: the same
        AnalysisContext (with its conversation history and active_result)
        is returned across turns that share the same task_id.
        """
        ctx = self.get(task_id)
        if ctx is None:
            ctx = AnalysisContext(project_id=project_id, task_id=task_id)
            self.set(task_id, ctx)
        return ctx

    def delete(self, task_id: str) -> None:
        """Remove a context from the store (e.g. on task deletion)."""
        with self._lock:
            self._store.pop(task_id, None)
        if self._persist_enabled:
            try:
                conn = get_db_connection()
                try:
                    conn.execute("DELETE FROM analysis_contexts WHERE task_id = ?", (task_id,))
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Failed to delete persisted analysis context: %s", exc)

    def size(self) -> int:
        """Return number of contexts currently held."""
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Remove all contexts (test helper)."""
        with self._lock:
            self._store.clear()

    @staticmethod
    def _load_persisted(task_id: str) -> Optional[AnalysisContext]:
        try:
            conn = get_db_connection()
            try:
                row = conn.execute(
                    "SELECT context_json FROM analysis_contexts WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            return AnalysisContext.model_validate_json(row[0])
        except Exception as exc:
            logger.debug("Persisted analysis context unavailable: %s", exc)
            return None

    @staticmethod
    def _persist(ctx: AnalysisContext) -> None:
        try:
            conn = get_db_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO analysis_contexts (task_id, context_json, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(task_id) DO UPDATE SET
                        context_json = excluded.context_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (ctx.task_id, ctx.model_dump_json()),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to persist analysis context: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: Optional[ContextStore] = None
_singleton_lock = threading.Lock()


def get_context_store() -> ContextStore:
    """Return the module-level ContextStore singleton (created on first call)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ContextStore(persist=True)
    return _singleton
