"""
SheetMind Runtime — Tool Base Class
=======================================
A Tool is a deterministic component that executes concrete actions.
Tools never call the model.  They may raise ToolError on failure.

Contrast with Skill (skills/base.py):
  - Tool  = deterministic, no model calls, synchronous run()
  - Skill = async, may call the model via ModelRouter
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import AnalysisContext

logger = logging.getLogger(__name__)


class Tool(ABC):
    """
    Base class for all SheetMind Tools.

    Each subclass must define:
        name        — unique snake_case identifier
        description — one-line human-readable description

    Each subclass must implement:
        run(ctx, **kwargs) → Any

    Tools run synchronously.  Wrap in asyncio.to_thread() if they block.
    """

    name: str = "unnamed_tool"
    description: str = ""

    @abstractmethod
    def run(
        self,
        ctx: "AnalysisContext",
        **kwargs: Any,
    ) -> Any:
        """
        Execute the tool.

        Args:
            ctx      — current AnalysisContext (typically read-only)
            **kwargs — tool-specific parameters

        Returns:
            A typed result defined by each subclass.

        Raises:
            ToolError — on known, recoverable failure.
            Exception — on unexpected failure (propagates up).
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Tool:{self.name}>"


class ToolError(Exception):
    """
    Raised when a Tool fails in an expected way the pipeline can handle.

    retryable=True means the caller may attempt to recover (e.g. by asking
    the model to fix the code that caused the error).
    """

    def __init__(
        self,
        message: str,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.user_message = message
        self.detail = detail
        self.retryable = retryable
