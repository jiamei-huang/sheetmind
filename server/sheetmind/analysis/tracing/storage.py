"""
SheetMind Runtime — Trace Storage
======================================
In-memory trace store with bounded capacity (FIFO eviction).
Traces are lost on server restart.  Attach a DB adapter for persistence.

Usage:
    from .storage import get_trace_store

    store = get_trace_store()
    store.save(trace)
    trace = store.get(trace_id)
    recent = store.list_recent(limit=20)
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from .trace import Trace


class TraceStore:
    """
    Thread-safe in-memory store for Trace objects.
    Oldest traces are evicted once `max_traces` is reached.
    """

    def __init__(self, max_traces: int = 1000) -> None:
        self._max = max_traces
        self._store: Dict[str, Trace] = {}
        self._order: Deque[str] = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, trace: Trace) -> None:
        """Insert or update a trace.  Evicts the oldest if at capacity."""
        with self._lock:
            if trace.trace_id in self._store:
                # Update in place; do not change ordering
                self._store[trace.trace_id] = trace
                return

            if len(self._order) >= self._max:
                oldest_id = self._order.popleft()
                self._store.pop(oldest_id, None)

            self._store[trace.trace_id] = trace
            self._order.append(trace.trace_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, trace_id: str) -> Optional[Trace]:
        """Return the trace for `trace_id`, or None if not found."""
        return self._store.get(trace_id)

    def list_recent(self, limit: int = 20) -> List[Trace]:
        """Return the most recent `limit` traces, newest first."""
        with self._lock:
            recent_ids = list(self._order)[-limit:]
        return [
            self._store[tid]
            for tid in reversed(recent_ids)
            if tid in self._store
        ]

    def list_by_task(self, task_id: str, limit: int = 50) -> List[Trace]:
        """Return traces for a specific task_id, newest first."""
        results = [
            t for t in self._store.values()
            if t.task_id == task_id
        ]
        results.sort(key=lambda t: t.started_at, reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Module-level singleton — use get_trace_store() everywhere
# ---------------------------------------------------------------------------

_default_store = TraceStore()


def get_trace_store() -> TraceStore:
    """Return the module-level TraceStore singleton."""
    return _default_store
