"""
SheetMind — Code Generation Skill
======================================
Generates pandas code from a natural-language query using the LLM.

Constraints enforced in the system prompt (same contract as old DataProcessingAgent):
  - Only use variable `df` (the input DataFrame)
  - Must set `result_df` as the final output variable
  - No import statements (pd/np pre-loaded)
  - No file I/O
  - No eval/exec/open
  - Keep code under ~30 lines
  - Handle null values gracefully

Returns the generated code as a plain string.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from ..context import AnalysisContext
from ..models.configs import ModelRole
from .base import Skill, SkillError
from .data_profiling import DataProfilingSkill
from .semantic_typing import SemanticFieldMap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (matches spec §7 + old DataProcessingAgent constraints)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一个专业的 pandas 代码生成专家。

你的任务：根据用户查询和数据信息，生成安全、正确的 pandas 代码处理数据。

【⚠️ 执行环境约束（必须严格遵守）】
1. 数据已在内存中，变量名为 df（pd.DataFrame），禁止重新读取文件
2. 禁止任何 import 语句（pd、np 已预加载）
3. 最终结果必须赋值给 result_df 变量
4. 禁止 eval、exec、open、os、sys、subprocess、requests 等危险操作
5. 禁止使用 df1、df2、dfs 等不存在的变量

【代码质量要求】
- 列名用 df['列名'] 或 df.loc[:, '列名'] 访问，不用 df.列名
- 日期列转换用 pd.to_datetime(df['列'], errors='coerce')
- 聚合后用 .reset_index() 保持 DataFrame 类型
- 空值处理：必要时使用 .dropna() 或 .fillna(0)
- 数值列转换：pd.to_numeric(df['列'], errors='coerce')
- 列名模糊匹配：如果确切列名不在 df.columns，用 [c for c in df.columns if '关键词' in c][0] 找到它

【输出格式】
只返回纯 Python 代码，不包含 ```python 标记、注释或解释文字。
代码长度控制在 30 行以内。
"""

# ---------------------------------------------------------------------------
# Skill implementation
# ---------------------------------------------------------------------------

class CodeGenerationSkill(Skill):
    """
    Generate pandas code for a query using the LLM.
    The caller (RepairLoop / run_pipeline) then executes the code.

    Returns: str — the generated code string
    """

    name = "code_generation"
    description = "Generate pandas code using LLM (result_df output)"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        df: Optional[pd.DataFrame] = None,
        data_summary: str = "",
        field_map: Optional[SemanticFieldMap] = None,
        error_feedback: Optional[str] = None,
        wants_chart: bool = False,
        is_compound: bool = False,
        **kwargs: Any,
    ) -> str:
        """
        Generate pandas code for `query`.

        Args:
            ctx           — AnalysisContext (conversation history injected into prompt)
            query         — user's query
            df            — current DataFrame (used for column list)
            data_summary  — output from DataProfilingSkill
            field_map     — column alias map from SemanticTypingSkill
            error_feedback — if set, previous code + error (triggers repair mode)
            wants_chart   — if True, hint to the model to produce chart-ready aggregated data

        Returns:
            Generated Python code string (no markdown, no imports).
        """
        provider = self.router.get_provider(ModelRole.CODE_GENERATION)

        # Build the user prompt
        user_msg = self._build_user_prompt(
            query=query,
            df=df,
            data_summary=data_summary,
            field_map=field_map,
            error_feedback=error_feedback,
            wants_chart=wants_chart,
            is_compound=is_compound,
            ctx=ctx,
        )

        response = await provider.complete(
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.1,
        )

        code = self._clean_code(response)
        if not code.strip():
            raise SkillError(self.name, "Code generation returned empty code")

        logger.debug("[CodeGen] generated %d lines:\n%s", len(code.splitlines()), code[:300])
        return code

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_user_prompt(
        self,
        query: str,
        df: Optional[pd.DataFrame],
        data_summary: str,
        field_map: Optional[SemanticFieldMap],
        error_feedback: Optional[str],
        wants_chart: bool,
        is_compound: bool,
        ctx: AnalysisContext,
    ) -> str:
        parts = []

        # Data summary
        if data_summary:
            parts.append(f"【数据信息】\n{data_summary}")
        elif df is not None:
            cols = list(df.columns)
            parts.append(f"【数据信息】\n列名：{cols}\n行数：{len(df)}")

        # Field map aliases (top 5 ambiguous columns)
        if field_map:
            aliases_lines = []
            for col, info in list(field_map.items())[:5]:
                if len(info.aliases) > 1:
                    aliases_lines.append(f"  {col!r} → 别名: {info.aliases[1:3]}, 类型: {info.type}")
            if aliases_lines:
                parts.append("【列别名参考】\n" + "\n".join(aliases_lines))

        # Compound query hint — instruct LLM to answer each sub-question separately
        # and stack results into one result_df with a '问题' label column
        if is_compound:
            parts.append(
                "【多问题提示】\n"
                "用户查询包含多个独立问题，请分别计算每个问题的答案，\n"
                "然后用以下方式合并到一个 result_df：\n"
                "  sub1 = ...  # 第一个问题的结果 DataFrame，加 '问题' 列\n"
                "  sub2 = ...  # 第二个问题的结果 DataFrame，加 '问题' 列\n"
                "  result_df = pd.concat([sub1, sub2], ignore_index=True)\n"
                "每个子结果必须有 '问题' 列标注对应的子问题描述。"
            )

        # Chart hint
        if wants_chart:
            parts.append(
                "【图表模式提示】\n"
                "用户想要生成图表。请生成适合直接绘图的聚合数据（如按类别汇总），"
                "result_df 应为少量行（如 < 50 行），包含明确的分类列和数值列。"
            )

        # Conversation history (for follow-up context)
        conv = ctx.conversation_text(max_turns=4)
        if conv:
            parts.append(f"【对话历史】\n{conv}")

        # User query
        parts.append(f"【用户查询】\n{query}")

        # Error repair mode
        if error_feedback:
            parts.append(
                f"【修复模式】\n上一次生成的代码执行出错。请修复以下错误：\n{error_feedback}"
            )

        parts.append(
            "请直接生成 pandas 代码，必须将最终结果赋值给 result_df，不要包含解释或 markdown 标记。"
        )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_code(response: str) -> str:
        """Strip markdown code fences and leading/trailing whitespace."""
        code = response.strip()
        # Remove ```python ... ``` or ``` ... ```
        if code.startswith("```"):
            lines = code.splitlines()
            # Drop first line (```python or ```) and last line (```)
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            code = "\n".join(inner)
        return code.strip()
