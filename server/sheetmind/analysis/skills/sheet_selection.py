"""
SheetMind — Sheet Selection Skill
======================================
Selects which files and sheets to use for a query using deterministic
keyword and recency rules.

Returns a list of SheetSelection dicts:
  [{"fileName": "sales.xlsx", "sheets": ["Sheet1", "Sheet2"]}]
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..context import AnalysisContext
from .base import Skill, SkillError

logger = logging.getLogger(__name__)


class SheetSelectionSkill(Skill):
    """
    Select relevant files and sheets for a query.
    Delegates to the storage-aware sheet selector.
    """

    name = "sheet_selection"
    description = "Select relevant Excel files and sheets for a query"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """
        Returns:
            [{"fileName": "...", "sheets": ["Sheet1", ...]}]
            Empty list if no files found.
        """
        try:
            from sheetmind.services.sheet_selection import SheetSelector
        except ImportError as exc:
            raise SkillError(
                self.name,
                "SheetSelector could not be imported",
                detail=str(exc),
            ) from exc

        selector = SheetSelector()
        try:
            selected = selector.select_sheets(ctx.project_id, query)
        except Exception as exc:
            logger.warning("SheetSelector failed: %s", exc)
            raise SkillError(
                self.name,
                f"Sheet selection failed: {exc}",
                detail=str(exc),
            ) from exc

        if not selected:
            raise SkillError(
                self.name,
                "没有找到可用的Excel文件，请先上传数据文件。",
            )

        # Update ctx.selected_sheets with the flat list of sheet names
        flat_sheets = []
        for item in selected:
            flat_sheets.extend(item.get("sheets", []))
        ctx.selected_sheets = flat_sheets

        return selected
