"""Resolve requested output intents into concrete result-block behavior."""
from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel

from ..context import AnalysisContext, RoutingHint
from .base import Skill


class OutputPlan(BaseModel):
    include_summary: bool = True
    include_table: bool = False
    include_chart: bool = False
    export_excel: bool = False


class OutputPlanningSkill(Skill):
    """Apply output intent after routing without reinterpreting the query."""

    name = "output_planning"
    description = "Turn output intents into table, chart, summary, and export behavior"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        output_intents: List[str],
        route: RoutingHint,
        has_computation: bool,
        **kwargs: Any,
    ) -> OutputPlan:
        formats = set(output_intents or ["auto"])
        has_auto_output = not formats or "auto" in formats
        return OutputPlan(
            include_summary=True,
            include_table=(
                "table" in formats
                or "export_excel" in formats
                or (
                    has_auto_output
                    and has_computation
                    and route != RoutingHint.INSIGHT_ONLY
                )
            ),
            include_chart="chart" in formats,
            export_excel="export_excel" in formats,
        )
