"""
SheetMind end-to-end smoke test
====================================
用真实 DeepSeek API 跑 8 条代表性查询，验证端到端流程。

前提：
  .env 里已配置 OPENAI_API_KEY（DeepSeek key）和 OPENAI_BASE_URL

运行方式（从 server 目录）：
  python3 -m sheetmind.analysis.evals.smoke_test

测试覆盖：
  Q1  筛选某月数据               → RULE_ENGINE
  Q2  按地区汇总销售额            → CODE_GEN 聚合
  Q3  Top-N 产品               → CODE_GEN top-n
  Q4  柱状图                   → CODE_GEN + 图表
  Q5  洞察分析                  → INSIGHT_ONLY
  Q6  继续：月度趋势（Q2之后）     → CODE_GEN + FOLLOW_UP 多轮
  Q7  图表说明什么（Q4之后）      → INSIGHT_ONLY + FOLLOW_UP 多轮
  Q8  重新看所有数据（多轮后）     → RULE_ENGINE + RESET 多轮
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── 让 SheetMind 包可以 import ──────────────────────────────────────────────
_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent.parent  # server/
sys.path.insert(0, str(_ROOT))

# ── 加载 .env（必须在 import 运行时模块之前）────────────────────────────────
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from sheetmind.analysis.agent import SheetMindAgent
from sheetmind.analysis.context import AnalysisContext, MultiTurnMode


# ===========================================================================
# 1. 样本数据生成（~200行，仿真销售 Excel）
# ===========================================================================

def _make_sample_df() -> pd.DataFrame:
    """生成可重复的合成销售数据，200 行，6 列。"""
    rng = np.random.default_rng(42)
    n = 200

    regions    = ["华东", "华北", "华南", "西部"]
    categories = ["电子产品", "服装", "食品", "家居用品"]
    products: Dict[str, List[str]] = {
        "电子产品": ["手机", "笔记本", "耳机", "平板"],
        "服装":    ["T恤", "外套", "裤子", "鞋子"],
        "食品":    ["零食", "饮料", "调味品", "速食"],
        "家居用品": ["床品", "收纳盒", "灯具", "餐具"],
    }
    salespeople = ["张伟", "李娜", "王强", "陈静", "赵磊", "刘洋", None]

    dates      = pd.date_range("2024-01-01", "2024-12-31", periods=n)
    cat_arr    = rng.choice(categories, n)
    prod_arr   = [rng.choice(products[c]) for c in cat_arr]
    sales_arr  = rng.integers(500, 50_000, n).astype(float)
    qty_arr    = rng.integers(1, 100, n)
    sp_arr     = [salespeople[i % len(salespeople)] for i in range(n)]

    # 随机插入 10 个空值（模拟真实脏数据）
    null_idx = rng.choice(n, 10, replace=False)
    for i in null_idx:
        sales_arr[i] = float("nan")

    df = pd.DataFrame({
        "日期":     [d.strftime("%Y-%m-%d") for d in dates],
        "地区":     rng.choice(regions, n),
        "产品类别":  cat_arr,
        "产品名称":  prod_arr,
        "销售额":    sales_arr,
        "数量":      qty_arr,
        "销售员":    sp_arr,
    })
    return df


# ===========================================================================
# 2. 注入 DataFrame，绕过 ExcelService / 数据库（仅用于 smoke test）
# ===========================================================================

def _patch_agent(agent: SheetMindAgent, df: pd.DataFrame) -> None:
    """
    将 DataframeLoaderTool 和 SheetSelectionSkill 替换为内存存根。
    不改动任何主业务代码，仅在此测试进程中生效。
    """
    _df = df  # 闭包持有

    # -- 替换 DataframeLoader --------------------------------------------------
    def _fake_loader(
        ctx: AnalysisContext,
        selected_files: Optional[List[Dict]] = None,
        multiturn_mode: Optional[MultiTurnMode] = None,
        **_: Any,
    ) -> pd.DataFrame:
        if multiturn_mode == MultiTurnMode.FOLLOW_UP and ctx._active_df is not None:
            return ctx._active_df          # 多轮复用上一次结果
        ctx._active_df = _df.copy()
        return _df.copy()

    # -- 替换 SheetSelectionSkill ---------------------------------------------
    async def _fake_sheet_run(
        ctx: AnalysisContext,
        query: str,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        return [{"fileName": "sample_sales.xlsx", "sheets": ["销售数据"]}]

    agent.df_loader.run     = _fake_loader
    agent.sheet_skill.run   = _fake_sheet_run


# ===========================================================================
# 3. 单条测试用例结构
# ===========================================================================

@dataclass
class SmokeCase:
    id: str
    query: str
    desc: str
    expected_routing: str
    expected_mode: str


@dataclass
class SmokeResult:
    case: SmokeCase
    passed: bool
    actual_routing: str = ""
    actual_mode: str    = ""
    result_type: str    = ""
    suggestion_preview: str = ""
    latency_s: float    = 0.0
    error: str          = ""
    failures: List[str] = field(default_factory=list)


# ===========================================================================
# 4. 测试用例列表（顺序运行，共享同一个 AnalysisContext）
# ===========================================================================

CASES = [
    SmokeCase("Q1", "筛选2024年10月的数据",
              "RULE_ENGINE：日期筛选", "rule", "new"),
    SmokeCase("Q2", "按地区汇总销售额，从高到低排列",
              "CODE_GEN：分组聚合", "code", "new"),
    SmokeCase("Q3", "销售额最高的前10名产品",
              "CODE_GEN：Top-N", "code", "new"),
    SmokeCase("Q4", "画一个各地区销售额对比柱状图",
              "CODE_GEN：柱状图", "code", "new"),
    SmokeCase("Q5", "这份销售数据说明了什么问题，有什么值得关注的洞察",
              "INSIGHT_ONLY：洞察分析", "insight", "new"),
    SmokeCase("Q6", "继续，展示华东地区的月度销售趋势折线图",
              "CODE_GEN + FOLLOW_UP：月度趋势（接Q2）", "code", "follow_up"),
    SmokeCase("Q7", "这个图表说明了什么，有什么异常吗",
              "INSIGHT_ONLY + FOLLOW_UP：图表洞察（接Q4/Q6）", "insight", "follow_up"),
    SmokeCase("Q8", "重新看所有完整数据",
              "RULE_ENGINE + RESET：回到全量数据", "rule", "reset"),
]


# ===========================================================================
# 5. 运行单条用例
# ===========================================================================

async def _run_case(
    agent: SheetMindAgent,
    ctx: AnalysisContext,
    case: SmokeCase,
) -> SmokeResult:
    result = SmokeResult(case=case, passed=False)
    t0 = time.perf_counter()

    try:
        rb = await agent.run(ctx, case.query)
        result.latency_s = time.perf_counter() - t0

        # 找到 routing/mode（从最后一个 assistant turn）
        if ctx.conversation:
            last = ctx.conversation[-1]
            if last.role == "assistant" and last.routing_hint:
                result.actual_routing = last.routing_hint.value
            if last.role == "assistant" and last.multiturn_mode:
                result.actual_mode = last.multiturn_mode.value

        kinds = {block.kind for block in rb.blocks}
        if "table" in kinds and "chart" in kinds:
            result.result_type = "both"
        elif "table" in kinds:
            result.result_type = "data"
        elif "chart" in kinds:
            result.result_type = "chart"
        else:
            result.result_type = "insight_only"
        suggestion = "\n\n".join(rb.all_summaries())
        result.suggestion_preview = suggestion[:120].replace("\n", " ")

        # 检查
        failures: List[str] = []
        if result.actual_routing != case.expected_routing:
            failures.append(
                f"routing: 期望={case.expected_routing}，实际={result.actual_routing}"
            )
        if result.actual_mode != case.expected_mode:
            failures.append(
                f"mode: 期望={case.expected_mode}，实际={result.actual_mode}"
            )
        if not suggestion.strip():
            failures.append("suggestion 为空（insight_writing 没有输出）")

        result.failures = failures
        result.passed   = len(failures) == 0

    except Exception as exc:
        result.latency_s = time.perf_counter() - t0
        result.error  = str(exc)
        result.passed = False
        result.failures = [f"exception: {exc}"]

    return result


# ===========================================================================
# 6. 主运行流程
# ===========================================================================

async def _main() -> int:
    print("=" * 64)
    print("SheetMind end-to-end smoke test")
    print("=" * 64)

    # 验证 API key 存在
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    if not api_key:
        print("\n❌ 错误：OPENAI_API_KEY 未设置，请检查 .env 文件")
        return 1
    masked = api_key[:8] + "..." + api_key[-4:]
    print(f"\nAPI key : {masked}")
    print(f"Base URL: {base_url}")
    print(f"样本数据 : 200行 × 7列（合成销售数据）\n")

    # 初始化
    df    = _make_sample_df()
    agent = SheetMindAgent()
    _patch_agent(agent, df)

    # 一个 AnalysisContext 贯穿所有用例（模拟真实多轮对话）
    ctx = AnalysisContext(project_id="smoke-test", task_id="smoke-001")

    results: List[SmokeResult] = []

    for i, case in enumerate(CASES, 1):
        print(f"[{i}/{len(CASES)}] {case.id}  {case.desc}")
        print(f"       Query: {case.query}")

        sr = await _run_case(agent, ctx, case)
        results.append(sr)

        status = "✅ PASS" if sr.passed else "❌ FAIL"
        print(f"       {status}  routing={sr.actual_routing}  mode={sr.actual_mode}"
              f"  type={sr.result_type}  {sr.latency_s:.1f}s")
        if sr.suggestion_preview:
            print(f"       洞察: {sr.suggestion_preview}")
        for f in sr.failures:
            print(f"       ⚠ {f}")
        if sr.error:
            print(f"       🔴 ERROR: {sr.error[:200]}")
        print()

    # ── 汇总 ────────────────────────────────────────────────────────────────
    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    avg_latency = sum(r.latency_s for r in results) / total if total else 0

    print("=" * 64)
    print(f"结果汇总  {passed}/{total} PASS   平均耗时 {avg_latency:.1f}s")
    print("=" * 64)

    if passed < total:
        print("\n失败用例：")
        for r in results:
            if not r.passed:
                print(f"  [{r.case.id}] {r.case.desc}")
                for f in r.failures:
                    print(f"    • {f}")
                if r.error:
                    print(f"    🔴 {r.error[:300]}")

    target = 7
    if passed >= target:
        print(f"\n通过发布门槛（至少 {target}/8）")
    else:
        print(f"\n未通过发布门槛（少于 {target}/8）")

    # 保存结果到文件（方便粘贴给开发者看）
    out_path = _HERE / "smoke_test_result.txt"
    with open(out_path, "w", encoding="utf-8") as fp:
        for r in results:
            fp.write(f"[{r.case.id}] {'PASS' if r.passed else 'FAIL'}\n")
            fp.write(f"  routing={r.actual_routing} mode={r.actual_mode} "
                     f"type={r.result_type} latency={r.latency_s:.1f}s\n")
            fp.write(f"  suggestion: {r.suggestion_preview}\n")
            for f in r.failures:
                fp.write(f"  FAIL: {f}\n")
            if r.error:
                fp.write(f"  ERROR: {r.error[:400]}\n")
            fp.write("\n")
    print(f"\n详细结果已保存到: {out_path}")

    return 0 if passed >= target else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
