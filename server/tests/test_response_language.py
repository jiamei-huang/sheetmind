"""Response-language contract tests for planning and insight output."""
import asyncio
import json

import pandas as pd

from sheetmind.analysis.context import AnalysisContext, MultiTurnMode
from sheetmind.analysis.language import infer_response_language, resolve_response_language
from sheetmind.analysis.skills.insight_writing import InsightWritingSkill
from sheetmind.analysis.skills.query_planning import QueryPlanningSkill


class StubProvider:
    def __init__(self, response: str):
        self.response = response
        self.requests = []

    async def complete(self, messages, system="", **kwargs):
        self.requests.append({"messages": messages, "system": system})
        return self.response


class StubRouter:
    def __init__(self, response: str):
        self.provider = StubProvider(response)

    def get_provider(self, _role):
        return self.provider


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def make_ctx() -> AnalysisContext:
    return AnalysisContext(project_id="project-1", task_id="task-1")


def test_language_detection_uses_query_grammar_not_english_identifiers():
    assert infer_response_language("哪个 SKU cost 最高") == "zh"
    assert infer_response_language("Which 易仓SKU has the highest cost?") == "en"
    assert infer_response_language("这个结果里哪些 SKU 费用偏高") == "zh"


def test_explicit_language_request_overrides_planner_value():
    assert resolve_response_language("请用英文回答：哪个平台最高", "zh") == "en"
    assert resolve_response_language("Answer in Chinese: which platform leads?", "en") == "zh"


def test_semantic_planner_returns_validated_response_language():
    response = json.dumps({
        "mode": "new",
        "response_language": "zh",
        "steps": [{
            "id": "s1",
            "query": "比较各平台费用",
            "depends_on": [],
            "input_source": "source",
            "scope_from": None,
            "target_fields": [],
            "needs_new_computation": True,
            "semantics": {},
        }],
        "reasoning": "Chinese query",
        "confidence": 0.9,
    })
    skill = QueryPlanningSkill(StubRouter(response))

    _, _, language, _, _ = run(skill._llm_decompose(
        make_ctx(),
        "比较各平台费用",
        MultiTurnMode.NEW_QUERY,
    ))

    assert language == "zh"


def test_insight_prompt_and_fallback_follow_response_language():
    router = StubRouter("### 关键结论\n\n乐天费用最高。")
    skill = InsightWritingSkill(router)
    result = pd.DataFrame({"平台": ["乐天"], "费用金额": [100.0]})

    text = run(skill.run(
        make_ctx(),
        "哪个平台费用最高",
        result_df=result,
        response_language="zh",
    ))

    assert "乐天费用最高" in text
    assert "Simplified Chinese" in router.provider.requests[0]["system"]
    assert "费用金额" in router.provider.requests[0]["messages"][0]["content"]
    assert InsightWritingSkill._fallback_summary(
        "processing",
        result,
        None,
        response_language="zh",
    ) == "数据处理完成，共 1 行、2 列。"
