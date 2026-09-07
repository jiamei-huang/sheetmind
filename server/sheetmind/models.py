"""Domain records returned by persistence services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Project:
    project_id: str
    project_name: str
    created_at: datetime


@dataclass(frozen=True)
class Task:
    task_id: str
    project_id: str
    task_number: int
    status: str
    created_at: datetime
    title: str | None = None
