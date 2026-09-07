"""
SheetMind — Routing Classification Skill
============================================
Returns RoutingHint (rule / code / text) + MultiTurnMode (new / follow_up / reset).

Design:
- Rule-based keyword matching is the primary path (cheap, instant).
- LLM fallback only when confidence < 0.55 (rare ambiguous cases).
- Multi-turn mode is determined first — it takes priority and can influence routing.

Routing keywords (from spec §7 + phase-1 inventory):

  RULE_ENGINE:
    filter keywords:  筛选 过滤 filter where 显示 展示
    date keywords:    年 月 date year month
    sort keywords:    排序 sort order 从高到低 从低到高 ascending descending
    AND no CODE_GEN signals

  CODE_GEN (override RULE_ENGINE when present):
    agg/calc:         统计 汇总 计算 sum count avg group groupby 聚合
    numeric cond:     大于 小于 不低于 不高于 > < between 占比 增长率 环比 百分比
    chart/viz:        图表 折线图 柱状图 饼图 bar line pie chart plot trend 趋势图 可视化 图形 visualization
    transform:        透视 pivot 添加列 新增列 合并 merge 去重 dedup
    top-n:            top 最高 最低 前N 前 名 rank

  TEXT_ONLY (when no data op or chart signals):
    open-ended:       分析一下 有什么问题 说明什么 洞察 建议 为什么 什么原因 怎么看
                      什么特点 趋势如何 有没有异常 解读 解释

Multi-turn mode signals:
  RESET:     重新 全部数据 所有数据 完整数据 reset all data 从头
  FOLLOW_UP: 这些 继续 在此基础上 进一步 based on 这个 这批 刚才 上面 再 AND active_result exists
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..context import AnalysisContext, MultiTurnMode, RoutingHint
from ..models.configs import ModelRole
from .base import Skill, SkillError


# ---------------------------------------------------------------------------
# Keyword banks
# ---------------------------------------------------------------------------

_RESET_KWS = [
    "重新", "全部数据", "所有数据", "完整数据", "reset", "all data", "从头",
]

_FOLLOW_UP_KWS = [
    "这些", "继续", "在此基础上", "进一步", "based on",
    "这个", "这批", "刚才", "上面", "再",
    # Correction/adjustment of the previous result (e.g. "Y轴用错了，应该是...")
    "用错", "改成", "换成", "改为", "修改", "不对",
]

_CODE_GEN_KWS = [
    # aggregation / calculation
    "统计", "汇总", "计算", "sum", "count", "avg", "group", "groupby", "聚合",
    # numeric condition
    "大于", "小于", "不低于", "不高于", "between", "占比", "增长率", "环比",
    "百分比", "比例", "份额",
    # chart / visualization
    "图表", "折线图", "柱状图", "饼图", "bar", "line", "pie", "chart",
    "plot", "trend", "趋势图", "可视化", "图形", "visualization", "图",
    # trend / time-series words (even without 图 suffix)
    "趋势", "变化", "走势", "波动",
    # complex transform
    "透视", "pivot", "添加列", "新增列", "合并", "merge", "去重", "dedup",
    "rank",
    # top-n
    "top", "最高", "最低", "前", "名",
]

# Operator symbols handled separately
_CODE_GEN_OPS = re.compile(r"[><≥≤]")

_RULE_ONLY_KWS = [
    "筛选", "过滤", "filter", "where", "显示", "展示",
    "年", "月", "date", "year", "month",
    "排序", "sort", "order", "从高到低", "从低到高",
    "升序", "降序", "排列",
    "ascending", "descending",
]

_TEXT_ONLY_KWS = [
    "分析一下", "有什么问题", "说明什么", "说明了什么", "洞察", "建议", "为什么",
    "什么原因", "怎么看", "什么特点", "趋势如何", "有没有异常", "有什么异常",
    "解读", "解释", "什么规律", "如何改进",
    "什么问题", "什么意义", "什么意思", "什么情况",
    "能说明", "反映了什么", "体现了什么",
]

# Short vague queries → default to rule (pass-through data view)
_VAGUE_KWS = ["看看", "看一下", "数据", "全部", "所有", "一览"]

_DETERMINISTIC_EXTREME_KWS = [
    "最多", "最高", "最大", "最贵", "花费最多", "费用最高", "金额最高",
]

_DETERMINISTIC_EXTREME_SUBJECT_KWS = [
    "哪个", "哪家", "哪一个", "哪类", "哪种", "who", "which",
]

_COMPLEX_OVERRIDE_KWS = [
    "前", "top", "趋势", "变化", "增长率", "环比", "占比", "比例",
    "透视", "pivot", "图", "chart", "plot", "可视化",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RoutingResult:
    hint: RoutingHint
    mode: MultiTurnMode
    confidence: float
    reasoning: str
    is_compound: bool = False   # True when query contains multiple independent questions


# ---------------------------------------------------------------------------
# Skill implementation
# ---------------------------------------------------------------------------

class RoutingClassificationSkill(Skill):
    """
    Classify a query into a RoutingHint and MultiTurnMode.
    Uses rule-based detection first; LLM fallback for low-confidence cases.
    """

    name = "routing_classification"
    description = "Classify query into RoutingHint (rule/code/text) + MultiTurnMode (new/follow_up/reset)"

    # Confidence threshold below which we call the LLM
    LLM_THRESHOLD = 0.55

    # ---------------------------------------------------------------------------
    # Public
    # ---------------------------------------------------------------------------

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        **kwargs: Any,
    ) -> RoutingResult:
        q = query.strip()

        # Step 1: determine multi-turn mode
        mode = self._detect_multiturn_mode(q, ctx)

        # Step 2: rule-based routing hint
        hint, confidence, reasoning = self._rule_classify(q)

        # Step 3: LLM fallback if ambiguous
        if confidence < self.LLM_THRESHOLD:
            try:
                hint, reasoning = await self._llm_classify(q, ctx, hint)
                confidence = 0.80
            except Exception as exc:
                # Don't fail the whole request on routing LLM error — fall back to rule result
                reasoning += f" (LLM fallback failed: {exc})"

        # Step 4: detect compound / multi-part queries
        is_compound = self._detect_compound(q)

        return RoutingResult(
            hint=hint,
            mode=mode,
            confidence=confidence,
            reasoning=reasoning,
            is_compound=is_compound,
        )

    # ---------------------------------------------------------------------------
    # Multi-turn mode detection
    # ---------------------------------------------------------------------------

    def _detect_multiturn_mode(
        self, query: str, ctx: AnalysisContext
    ) -> MultiTurnMode:
        q_lower = query.lower()

        # RESET: explicit restart — only meaningful when there is an active result to reset
        if ctx.active_result is not None and any(kw in q_lower for kw in _RESET_KWS):
            return MultiTurnMode.RESET

        # FOLLOW_UP: explicit reference-to-previous signal AND there is an active result.
        # NOTE: We intentionally do NOT use query length as a signal — Chinese queries
        # are naturally short, so a 15-char query like "按地区汇总销售额" is a new question,
        # not a follow-up just because it's brief.  Only explicit keywords count.
        if ctx.active_result is not None:
            if any(kw in q_lower for kw in _FOLLOW_UP_KWS):
                return MultiTurnMode.FOLLOW_UP

        return MultiTurnMode.NEW_QUERY

    # ---------------------------------------------------------------------------
    # Compound query detection
    # ---------------------------------------------------------------------------

    @staticmethod
    def _detect_compound(query: str) -> bool:
        """
        Return True when the query contains two or more independent analytical
        questions that each need separate computation.

        Heuristic: split on Chinese sentence terminators (。？) and connector
        phrases; if ≥ 2 segments are non-trivial (≥ 6 chars) the query is
        treated as compound so CodeGenerationSkill can produce a combined result.
        """
        import re

        # Split on sentence boundaries and common compound connectors
        parts = re.split(r'[。？?]|另外|同时|还有|以及|并且|此外', query)
        meaningful = [
            p.strip()
            for p in parts
            if len(p.strip()) >= 6 and RoutingClassificationSkill._looks_independent_question(p)
        ]
        return len(meaningful) >= 2

    @staticmethod
    def _looks_independent_question(part: str) -> bool:
        """Filter out follow-up modifiers such as "更直观看尾程花费"."""
        p = part.strip().lower()
        if not p:
            return False

        modifier_prefixes = [
            "更直观", "直观", "方便", "便于", "用于", "用来", "看看",
            "看一下", "展示一下",
        ]
        if any(p.startswith(prefix) for prefix in modifier_prefixes):
            return False

        action_kws = (
            _CODE_GEN_KWS
            + _RULE_ONLY_KWS
            + _TEXT_ONLY_KWS
            + _DETERMINISTIC_EXTREME_SUBJECT_KWS
        )
        return any(kw in p for kw in action_kws)

    # Chart-reference words that appear in both chart-creation and text-insight contexts.
    # When a TEXT_ONLY signal is present alongside ONLY these words (no real agg/calc),
    # the query is likely asking *about* a chart, not asking to *create* one.
    _CHART_REF_ONLY_KWS = frozenset([
        "图表", "图", "折线图", "柱状图", "饼图", "bar", "line", "pie",
        "chart", "plot", "可视化", "visualization", "图形",
    ])

    # ---------------------------------------------------------------------------
    # Rule-based classification
    # ---------------------------------------------------------------------------

    def _rule_classify(self, query: str) -> tuple:
        """Returns (RoutingHint, confidence, reasoning)."""
        q_lower = query.lower()

        # Check TEXT_ONLY signals BEFORE CODE_GEN when all CODE_GEN hits are
        # chart-reference words only (no aggregation / calculation signals).
        # This prevents "这个图表说明什么" from being misclassified as CODE_GEN.
        text_hits = [kw for kw in _TEXT_ONLY_KWS if kw in q_lower]
        if text_hits:
            code_candidates = [kw for kw in _CODE_GEN_KWS if kw in q_lower]
            non_chart_code = [kw for kw in code_candidates if kw not in self._CHART_REF_ONLY_KWS]
            if not non_chart_code:
                # TEXT_ONLY wins: no real computation signals, just chart-ref words
                return (
                    RoutingHint.TEXT_ONLY,
                    0.85,
                    f"TEXT_ONLY signals: {text_hits[:3]} (no agg/calc CODE_GEN signals)",
                )

        # Deterministic "which category costs/sells the most?" questions are
        # safer in the rule engine than in free-form pandas code generation.
        if (
            any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_SUBJECT_KWS)
            and any(kw in q_lower for kw in _DETERMINISTIC_EXTREME_KWS)
            and not any(kw in q_lower for kw in _COMPLEX_OVERRIDE_KWS)
        ):
            return (
                RoutingHint.RULE_ENGINE,
                0.86,
                "RULE_ENGINE deterministic aggregate-extreme question",
            )

        # Check CODE_GEN signals (they take priority over RULE_ENGINE)
        code_hits = [kw for kw in _CODE_GEN_KWS if kw in q_lower]
        has_op = bool(_CODE_GEN_OPS.search(query))
        if code_hits or has_op:
            matched = code_hits[:3]  # keep first 3 for reasoning
            conf = 0.90 if len(code_hits) >= 2 else 0.82
            return (
                RoutingHint.CODE_GEN,
                conf,
                f"CODE_GEN signals: {matched}" + (" + numeric operator" if has_op else ""),
            )

        # Check TEXT_ONLY signals (open-ended, no data op) — second pass for pure text queries
        text_hits = [kw for kw in _TEXT_ONLY_KWS if kw in q_lower]
        rule_hits = [kw for kw in _RULE_ONLY_KWS if kw in q_lower]
        if text_hits and not rule_hits:
            return (
                RoutingHint.TEXT_ONLY,
                0.85,
                f"TEXT_ONLY signals: {text_hits[:3]}",
            )

        # Check RULE_ENGINE signals
        if rule_hits:
            return (
                RoutingHint.RULE_ENGINE,
                0.88,
                f"RULE_ENGINE signals: {rule_hits[:3]}",
            )

        # Vague / pass-through queries (e.g. "看看数据") → rule engine pass-through
        vague_hits = [kw for kw in _VAGUE_KWS if kw in q_lower]
        if vague_hits and len(query) < 15:
            return (
                RoutingHint.RULE_ENGINE,
                0.70,
                f"Vague query (pass-through): {vague_hits}",
            )

        # Low-confidence fallback — default to CODE_GEN (more flexible)
        return (
            RoutingHint.CODE_GEN,
            0.45,
            "No clear signal; defaulting to CODE_GEN",
        )

    # ---------------------------------------------------------------------------
    # LLM fallback
    # ---------------------------------------------------------------------------

    async def _llm_classify(
        self,
        query: str,
        ctx: AnalysisContext,
        rule_hint: RoutingHint,
    ) -> tuple:
        """Call the LLM for ambiguous routing.  Returns (RoutingHint, reasoning)."""
        provider = self.router.get_provider(ModelRole.ROUTING)

        conv_text = ctx.conversation_text(max_turns=4)
        system = (
            "你是 RoutingClassifier，专门判断数据分析查询的执行路径。\n\n"
            "只输出 JSON，格式：{\"routing\": \"rule\"|\"code\"|\"text\", \"reasoning\": \"一句话说明\"}\n\n"
            "routing 含义：\n"
            "  rule  — 简单筛选/排序，无需计算，规则引擎可直接执行\n"
            "  code  — 需要聚合/计算/图表/复杂条件，LLM生成pandas代码执行\n"
            "  text  — 开放式分析问题，无需执行代码，直接回答文字\n\n"
            "注意：判断执行路径，不判断输出格式。"
        )

        user_content = (
            f"用户查询：{query}\n"
            f"规则引擎预测：{rule_hint.value}\n"
        )
        if conv_text:
            user_content += f"\n对话历史（最近几轮）：\n{conv_text}"

        response = await provider.complete(
            messages=[{"role": "user", "content": user_content}],
            system=system,
            max_tokens=128,
            temperature=0.0,
            json_mode=True,
        )

        try:
            data = json.loads(response)
            hint_str = data.get("routing", rule_hint.value)
            reasoning = data.get("reasoning", "LLM routing")
            return RoutingHint(hint_str), reasoning
        except Exception:
            return rule_hint, f"LLM parse failed; kept rule result: {response[:80]}"
