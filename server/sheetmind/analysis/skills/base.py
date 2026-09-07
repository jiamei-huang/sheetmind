"""
SheetMind Runtime — Skill Base Class
========================================
A Skill is a unit of intelligence that may call the model.
Skills are composed by SheetMindAgent.run() in a fixed pipeline order.

Contrast with Tool (tools/base.py):
  - Tool  = deterministic, no model calls, may raise ToolError
  - Skill = may call the model via ModelRouter, returns typed result
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import AnalysisContext
    from ..models.router import ModelRouter

logger = logging.getLogger(__name__)


class Skill(ABC):
    """
    Base class for all SheetMind Skills.

    Each subclass must define:
        name        — unique snake_case identifier
        description — one-line human-readable description

    Each subclass must implement:
        run(ctx, query, **kwargs) → Any
    """

    name: str = "unnamed_skill"
    description: str = ""

    def __init__(self, router: "ModelRouter") -> None:
        self.router = router

    @abstractmethod
    async def run(
        self,
        ctx: "AnalysisContext",
        query: str,
        **kwargs: Any,
    ) -> Any:
        """
        Execute the skill.

        Args:
            ctx   — current AnalysisContext (read and write)
            query — the user's current natural-language query
            **kwargs — skill-specific extra parameters

        Returns:
            A typed result defined by each subclass.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Skill:{self.name}>"


class SkillError(Exception):
    """
    Raised when a Skill fails in a way the pipeline should catch and handle
    (as opposed to unexpected exceptions which propagate up).
    """

    def __init__(
        self,
        skill_name: str,
        message: str,
        retryable: bool = False,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.skill_name = skill_name
        self.user_message = message
        self.retryable = retryable
        self.detail = detail
