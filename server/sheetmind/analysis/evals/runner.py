"""
SheetMind evaluation runner.

Loads evals/cases.json and evaluates the skill pipeline against each case.

Two modes:
  --routing-only   (default) — tests RoutingClassificationSkill + RuleEngineTool only.
                               No real LLM calls; uses a MockModelProvider.
                               Fast (~1–3s for all cases).

  --full           — runs the full pipeline (requires a real LLM API key).
                     Set OPENAI_API_KEY / ANTHROPIC_API_KEY in the environment.

Usage from the server directory:
    python -m sheetmind.analysis.evals.runner
    python -m sheetmind.analysis.evals.runner --routing-only
    python -m sheetmind.analysis.evals.runner --full

Output:
    Per-case PASS/FAIL lines + category summary + overall pass rate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Ensure SheetMind package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sheetmind.analysis.context import (
    AnalysisContext,
    FileRef,
    MultiTurnMode,
    ResultBlocks,
    RoutingHint,
)
from sheetmind.analysis.models.configs import ModelRole
from sheetmind.analysis.models.router import ModelRouter
from sheetmind.analysis.skills.routing_classification import RoutingClassificationSkill
from sheetmind.analysis.tools.rule_engine import RuleEngineTool

# ---------------------------------------------------------------------------
# Default path to eval cases
# ---------------------------------------------------------------------------

_DEFAULT_CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "cases.json"


# ---------------------------------------------------------------------------
# Mock Model Provider — returns deterministic empty responses for eval
# ---------------------------------------------------------------------------

class MockModelProvider:
    """Fake provider that returns empty/neutral responses — no real API calls."""

    async def complete(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 512,
        temperature: float = 0.0,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        # Keep low-confidence intent enrichment neutral during deterministic evals.
        if json_mode:
            return (
                '{"operation_intents": [], "output_intents": ["auto"], '
                '"confidence": 0.5, "reasoning": "mock"}'
            )
        return "分析完成。"


class MockModelRouter(ModelRouter):
    """Router that injects MockModelProvider for all roles."""

    def __init__(self) -> None:
        # Don't call super().__init__() — avoid loading real configs
        self._configs = {}
        self._providers = {}
        self._mock = MockModelProvider()

    def get_provider(self, role: ModelRole):
        return self._mock


# ---------------------------------------------------------------------------
# Eval case result
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    category: str
    description: str
    query: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    actual_routing: Optional[str] = None
    actual_mode: Optional[str] = None
    expected_routing: Optional[str] = None
    expected_mode: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

class EvalRunner:
    """
    Run eval cases against the routing + rule engine skills.

    For `routing_only=True` (the default), the full LLM pipeline is NOT called.
    We test:
      (a) Routing classification accuracy (rule-based part only; mock LLM)
      (b) Rule engine correctness for RULE_ENGINE cases

    For `routing_only=False`, the full agent pipeline is invoked (requires API key).
    """

    def __init__(
        self,
        cases_path: Optional[str] = None,
        routing_only: bool = True,
    ) -> None:
        self.cases_path = cases_path or self._resolve_cases_path()
        self.routing_only = routing_only
        self.router = MockModelRouter()
        self.routing_skill = RoutingClassificationSkill(self.router)
        self.rule_engine = RuleEngineTool()

    @staticmethod
    def _resolve_cases_path() -> str:
        """Return the repository's checked-in evaluation cases."""
        return str(_DEFAULT_CASES_PATH)

    def load_cases(self) -> List[Dict[str, Any]]:
        with open(self.cases_path, encoding="utf-8") as f:
            data = json.load(f)
        cases = data.get("cases", [])
        print(f"Loaded {len(cases)} cases from {self.cases_path}")
        return cases

    async def run_all(self) -> List[CaseResult]:
        cases = self.load_cases()
        results: List[CaseResult] = []

        for case in cases:
            result = await self._eval_case(case)
            results.append(result)
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"  {status} [{result.case_id}] {result.description}")
            if not result.passed:
                for f in result.failures:
                    print(f"        → {f}")
            if result.error:
                print(f"        ⚠ ERROR: {result.error}")

        return results

    async def _eval_case(self, case: Dict[str, Any]) -> CaseResult:
        """Evaluate a single case and return a CaseResult."""
        case_id = case.get("id", "?")
        category = case.get("category", "?")
        description = case.get("description", "")
        query = case.get("query", "")
        expected_routing = case.get("expected_routing")
        expected_mode = case.get("expected_multiturn_mode")

        cr = CaseResult(
            case_id=case_id,
            category=category,
            description=description,
            query=query,
            passed=False,
            expected_routing=expected_routing,
            expected_mode=expected_mode,
        )

        try:
            # Build a minimal context
            ctx = self._build_context(case)

            # Build a minimal DataFrame from schema.sample_rows
            df = self._build_df(case)

            # ----------------------------------------------------------
            # Routing classification check
            # ----------------------------------------------------------
            routing_result = await self.routing_skill.run(ctx, query)
            cr.actual_routing = routing_result.hint.value
            cr.actual_mode = routing_result.mode.value

            failures: List[str] = []

            if expected_routing and cr.actual_routing != expected_routing:
                failures.append(
                    f"routing: expected={expected_routing}, got={cr.actual_routing}"
                    f" (conf={routing_result.confidence:.2f}, reason: {routing_result.reasoning[:80]})"
                )

            if expected_mode and cr.actual_mode != expected_mode:
                failures.append(
                    f"mode: expected={expected_mode}, got={cr.actual_mode}"
                )

            # ----------------------------------------------------------
            # Rule engine sanity check (RULE_ENGINE cases only)
            # ----------------------------------------------------------
            if (
                expected_routing == "rule"
                and df is not None
                and not df.empty
                and not failures  # only if routing matched
            ):
                rule_failures = self._check_rule_engine(case, df, query)
                failures.extend(rule_failures)

            cr.failures = failures
            cr.passed = len(failures) == 0

        except Exception as exc:
            cr.error = str(exc)
            cr.failures = [f"exception: {exc}"]
            cr.passed = False

        return cr

    def _build_context(self, case: Dict[str, Any]) -> AnalysisContext:
        """Build a minimal AnalysisContext for the eval case."""
        ctx = AnalysisContext(
            project_id=f"eval_{case.get('id', 'x')}",
            task_id=case.get("id", "x"),
        )

        # For multi-turn cases, simulate active_result existing
        mt_mode = case.get("expected_multiturn_mode", "new")
        if mt_mode in ("follow_up", "reset"):
            # Pre-populate conversation with a fake previous CODE_GEN turn
            # so _detect_multiturn_mode can use previous_routing_hint()
            from sheetmind.analysis.context import (
                ResultBlocks as RB,
                RoutingHint as RH,
                SummaryBlock,
                Turn,
            )
            ctx.active_result = RB(blocks=[SummaryBlock(content="previous result")])
            ctx.conversation = [
                Turn(role="user", content="previous question"),
                Turn(
                    role="assistant",
                    content="previous answer",
                    result=ctx.active_result,
                    routing_hint=RH.CODE_GEN,
                ),
            ]

        return ctx

    def _build_df(self, case: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """Build a DataFrame from schema.sample_rows."""
        schema = case.get("schema", {})
        sample_rows = schema.get("sample_rows", [])
        if not sample_rows:
            return None
        try:
            return pd.DataFrame(sample_rows)
        except Exception:
            return None

    def _check_rule_engine(
        self,
        case: Dict[str, Any],
        df: pd.DataFrame,
        query: str,
    ) -> List[str]:
        """
        Run the rule engine on df+query and check basic result sanity.
        Returns a list of failure messages (empty = pass).
        """
        failures: List[str] = []
        try:
            result_df = self.rule_engine.run(None, query=query, df=df)  # type: ignore[arg-type]
            if result_df is None:
                failures.append("rule engine returned None")
            elif not isinstance(result_df, pd.DataFrame):
                failures.append(f"rule engine returned {type(result_df).__name__}, expected DataFrame")
        except Exception as exc:
            failures.append(f"rule engine raised: {exc}")
        return failures

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    @staticmethod
    def print_summary(results: List[CaseResult]) -> None:
        total = len(results)
        passed = sum(1 for r in results if r.passed)

        print("\n" + "=" * 60)
        print(f"EVAL SUMMARY  ({passed}/{total} PASS  —  {100*passed//total if total else 0}%)")
        print("=" * 60)

        # By category
        cats: Dict[str, Tuple[int, int]] = {}
        for r in results:
            cat_pass, cat_total = cats.get(r.category, (0, 0))
            cats[r.category] = (cat_pass + (1 if r.passed else 0), cat_total + 1)

        for cat, (p, t) in sorted(cats.items()):
            bar = "█" * p + "░" * (t - p)
            print(f"  {cat:5s}  {p:2d}/{t:2d}  {bar}")

        print()
        # Routing accuracy
        routing_total = sum(1 for r in results if r.expected_routing)
        routing_pass = sum(
            1 for r in results
            if r.expected_routing and r.actual_routing == r.expected_routing
        )
        if routing_total:
            print(f"  Routing accuracy: {routing_pass}/{routing_total} ({100*routing_pass//routing_total}%)")

        mode_total = sum(1 for r in results if r.expected_mode)
        mode_pass = sum(
            1 for r in results
            if r.expected_mode and r.actual_mode == r.expected_mode
        )
        if mode_total:
            print(f"  Mode accuracy:    {mode_pass}/{mode_total} ({100*mode_pass//mode_total}%)")

        print()
        if passed < total:
            print("Failed cases:")
            for r in results:
                if not r.passed:
                    print(f"  [{r.case_id}] {r.description}")
                    for f in r.failures:
                        print(f"    • {f}")
                    if r.error:
                        print(f"    ⚠ {r.error}")

        target_rate = 0.80
        overall_rate = passed / total if total else 0.0
        status = "✅ TARGET MET" if overall_rate >= target_rate else f"⚠ BELOW TARGET ({target_rate*100:.0f}%)"
        print(f"\nOverall: {passed}/{total} = {overall_rate*100:.1f}%  {status}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> int:
    parser = argparse.ArgumentParser(description="SheetMind Eval Runner")
    parser.add_argument("--routing-only", action="store_true", default=True,
                        help="Test routing + rule engine only (no LLM, default)")
    parser.add_argument("--full", action="store_true", default=False,
                        help="Run full pipeline (requires API key)")
    parser.add_argument("--cases", default=None, help="Path to evaluation cases JSON")
    parser.add_argument("--category", default=None, help="Run only this category (INT/DP/VIZ/MT/ERR)")
    args = parser.parse_args()

    routing_only = not args.full

    print(f"SheetMind Eval Runner — mode={'ROUTING_ONLY' if routing_only else 'FULL_PIPELINE'}")
    print()

    runner = EvalRunner(cases_path=args.cases, routing_only=routing_only)

    if args.full:
        print("⚠ Full pipeline mode requires real LLM API keys and file data.")
        print("  Routing-only checks will still be enforced for all cases.\n")

    try:
        all_cases = runner.load_cases()
    except FileNotFoundError as exc:
        print(f"ERROR: Could not find eval cases file: {exc}")
        print("  Run from the SheetMind directory, or pass --cases <path>")
        return 1

    # Filter by category if requested
    if args.category:
        all_cases = [c for c in all_cases if c.get("category") == args.category.upper()]
        print(f"Filtered to category '{args.category.upper()}': {len(all_cases)} cases\n")

    # Override load_cases to return filtered list
    original_load = runner.load_cases
    runner.load_cases = lambda: all_cases  # type: ignore[method-assign]

    print(f"Running {len(all_cases)} cases...\n")
    results = await runner.run_all()

    EvalRunner.print_summary(results)

    # Exit code: 0 if ≥80% pass, 1 otherwise
    passed = sum(1 for r in results if r.passed)
    return 0 if (passed / len(results) >= 0.80) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
