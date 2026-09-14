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

import ast
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import pandas as pd

from ..context import AnalysisContext, QuerySemantics
from ..models.configs import ModelRole
from .base import Skill, SkillError
from .data_profiling import DataProfile
from .field_resolution import FieldResolver
from .semantic_typing import SemanticFieldMap

logger = logging.getLogger(__name__)

_MONEY_TERMS = ("花费", "花钱", "费用", "金额", "成本", "cost", "spend", "expense", "amount")
_MONEY_COLUMN_TERMS = ("金额", "花费", "费用", "成本", "amount", "cost", "expense")
_CURRENCY_COLUMN_NAMES = {"币别", "币种", "货币", "货币类型", "currency", "currencycode"}
_CURRENCY_ALIASES = {
    "CNY": ("人民币", "cny", "rmb"),
    "USD": ("美元", "usd", "美金"),
    "EUR": ("欧元", "eur"),
    "JPY": ("日元", "jpy", "日币"),
    "GBP": ("英镑", "gbp"),
    "HKD": ("港币", "港元", "hkd"),
}
_CURRENCY_PRIORITY = ("CNY", "USD", "EUR", "JPY", "GBP", "HKD")


@dataclass(frozen=True)
class _CurrencySafetyPolicy:
    currency_column: str
    currencies: tuple[str, ...]
    required_column: str
    normalized_amount_column: Optional[str] = None
    explicit_currency: Optional[str] = None

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
- 只能使用下方“字段元数据”列出的列名；禁止猜测或按位置选择列
- identifier 类型（SKU、订单号、客户 ID 等）绝不能求和、均值或作为图表数值轴；需要统计时用 nunique()
- 用户查询带货币/单位限定词时，必须选用同样限定词的字段（如“人民币金额”优先“金额（RMB）”）

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
        data_summary: str | DataProfile = "",
        field_map: Optional[SemanticFieldMap] = None,
        error_feedback: Optional[str] = None,
        wants_chart: bool = False,
        is_compound: bool = False,
        required_columns: Optional[list[str]] = None,
        semantics: Optional[QuerySemantics] = None,
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
            required_columns — source dataframe columns that must be referenced

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
            required_columns=required_columns,
            semantics=semantics,
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
        data_summary: str | DataProfile,
        field_map: Optional[SemanticFieldMap],
        error_feedback: Optional[str],
        wants_chart: bool,
        is_compound: bool,
        required_columns: Optional[list[str]],
        semantics: Optional[QuerySemantics],
        ctx: AnalysisContext,
    ) -> str:
        parts = []

        # Data summary
        if data_summary:
            parts.append(f"【数据信息】\n{data_summary}")
        elif df is not None:
            cols = list(df.columns)
            parts.append(f"【数据信息】\n列名：{cols}\n行数：{len(df)}")

        # Field metadata gives the model a constrained, explicit field contract.
        if field_map:
            metadata_lines = []
            for col, info in list(field_map.items())[:20]:
                metadata_lines.append(
                    f"  {col!r}: type={info.type}, role={info.semantic_role}, "
                    f"should_aggregate={info.should_aggregate}, qualifiers={info.qualifiers}, "
                    f"recommended_aggregation={info.recommended_aggregation}, "
                    f"recommended_aliases={info.aliases[:4]}"
                )
            parts.append("【字段元数据】\n" + "\n".join(metadata_lines))

            matches = FieldResolver().rank(query, field_map, aggregate_only=True)[:3]
            if matches:
                parts.append(
                    "【字段解析建议】\n"
                    + "\n".join(f"  {match.column!r}（{match.reason}）" for match in matches)
                )

        if required_columns:
            parts.append(
                "【强制字段约束】\n"
                f"用户查询已解析到这些源数据列，生成代码必须直接引用：{required_columns!r}\n"
                "不要改用相似但未限定的列名；例如用户问 USD/RMB 时，必须使用对应货币字段。"
            )

        if semantics is not None:
            parts.append(
                "【已验证语义计划】\n"
                + json.dumps(semantics.model_dump(mode="json"), ensure_ascii=False)
                + "\n必须按该计划中的维度、指标、筛选、排序和 limit 生成 result_df；"
                "禁止自行更换字段、Sheet 语义或聚合方式。"
            )

        if self._requires_complete_extreme_evidence(query, semantics):
            parts.append(
                "【极值证据表约束】\n"
                "这是隐式最高/最低类问题。result_df 必须返回完整的分组排序结果，"
                "让结论可核验；禁止只保留最高或最低的1行，禁止使用 head(1)、"
                "nlargest(1)、nsmallest(1) 或 idxmax/idxmin 裁掉其余分组。"
                "只有用户明确要求数字 Top N 时才能按该数字截取。"
            )

        currency_policy = self._currency_safety_policy(query, df)
        if currency_policy is not None:
            currencies = "、".join(currency_policy.currencies)
            if currency_policy.normalized_amount_column is not None:
                instruction = (
                    f"数据包含多个币种（{currencies}）。必须使用完整的统一币种金额列 "
                    f"{currency_policy.normalized_amount_column!r} 比较，禁止使用原始金额列跨币种求和。"
                )
            elif currency_policy.explicit_currency is not None:
                instruction = (
                    f"数据包含多个币种（{currencies}）。用户指定了 "
                    f"{currency_policy.explicit_currency}，必须先用 "
                    f"{currency_policy.currency_column!r} 筛选该币种，再汇总原始金额；"
                    "禁止把其他币种计入结果。"
                )
            else:
                instruction = (
                    f"数据包含多个币种（{currencies}），且没有完整的统一币种金额列。"
                    "禁止把不同币种的原始金额直接相加；必须先按 "
                    f"{currency_policy.currency_column!r} 分组，在每个币种内分别计算，"
                    "并在 result_df 中保留币种列。"
                )
            parts.append(f"【币种安全约束】\n{instruction}")

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

    @staticmethod
    def _requires_complete_extreme_evidence(
        query: str,
        semantics: Optional[QuerySemantics],
    ) -> bool:
        if semantics is not None and semantics.limit is not None:
            return False
        q_lower = query.lower()
        extreme_terms = (
            "最高", "最低", "最多", "最少", "最大", "最小", "最贵", "最便宜",
            "highest", "lowest", "most", "least", "maximum", "minimum",
        )
        explicit_limit = re.search(
            r"(?:前\s*|top\s*|最高(?:的)?\s*|最低(?:的)?\s*)(\d+)",
            q_lower,
            re.IGNORECASE,
        )
        return any(term in q_lower for term in extreme_terms) and explicit_limit is None

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

    @staticmethod
    def validate_required_columns(
        code: str,
        required_columns: Optional[list[str]],
        available_columns: Optional[Iterable[str]] = None,
    ) -> Optional[str]:
        """Return an error message when generated code ignores required source columns."""
        if not required_columns:
            return None

        available = set(str(col) for col in available_columns) if available_columns is not None else set(required_columns)
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        referenced = CodeGenerationSkill._result_lineage_columns(tree, available)

        missing = [column for column in required_columns if column not in referenced]
        if not missing:
            return None

        return (
            "字段约束失败：生成代码的 result_df 数据流没有实际使用必须字段 "
            f"{missing!r}。请让这些精确列名参与筛选、分组、排序或计算，"
            "不能只把列名写在无关变量或输出标签中。"
        )

    @classmethod
    def validate_extreme_evidence(
        cls,
        code: str,
        query: str,
        semantics: Optional[QuerySemantics],
    ) -> Optional[str]:
        """Reject generated code that collapses implicit extrema to one winner."""
        if not cls._requires_complete_extreme_evidence(query, semantics):
            return None
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        truncating_calls = {"head", "tail", "nlargest", "nsmallest", "idxmax", "idxmin"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in truncating_calls
            ):
                return (
                    "极值证据约束失败：隐式最高/最低问题必须在 result_df 中保留完整的"
                    "分组排序结果，不能只截取极值行。结论层会从完整结果中指出最高或最低项。"
                )
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "iloc"
            ):
                return (
                    "极值证据约束失败：隐式最高/最低问题不能通过 iloc 截取极值行；"
                    "result_df 必须保留完整的分组排序结果。"
                )
        return None

    @classmethod
    def validate_currency_safety(
        cls,
        code: str,
        query: str,
        df: Optional[pd.DataFrame],
    ) -> Optional[str]:
        """Reject generated aggregations that ignore a mixed-currency contract."""
        policy = cls._currency_safety_policy(query, df)
        if policy is None or df is None:
            return None
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        referenced = cls._result_lineage_columns(
            tree,
            {str(column) for column in df.columns},
        )
        if policy.required_column in referenced:
            return None
        if policy.normalized_amount_column is not None:
            return (
                "币种安全约束失败：数据包含多个币种，result_df 必须使用完整的统一币种金额列 "
                f"{policy.normalized_amount_column!r}，不能直接聚合原始金额。"
            )
        if policy.explicit_currency is not None:
            return (
                "币种安全约束失败：result_df 的数据流必须使用 "
                f"{policy.currency_column!r} 筛选用户指定的 {policy.explicit_currency}，"
                "不能把其他币种计入结果。"
            )
        return (
            "币种安全约束失败：数据包含多个币种且没有完整换算金额，result_df 的数据流必须使用 "
            f"{policy.currency_column!r} 按币种分别聚合并保留币种，不能跨币种直接求和。"
        )

    @classmethod
    def _currency_safety_policy(
        cls,
        query: str,
        df: Optional[pd.DataFrame],
    ) -> Optional[_CurrencySafetyPolicy]:
        if df is None or df.empty or not any(term in query.lower() for term in _MONEY_TERMS):
            return None

        currency_column = next(
            (
                str(column)
                for column in df.columns
                if cls._normalise_currency_text(column) in _CURRENCY_COLUMN_NAMES
            ),
            None,
        )
        if currency_column is None:
            return None

        currency_values = (
            df[currency_column]
            .dropna()
            .astype(str)
            .str.strip()
        )
        currency_values = currency_values[currency_values != ""]
        currencies = tuple(sorted(currency_values.drop_duplicates().tolist()))
        if len(currencies) <= 1:
            return None

        raw_amount_column = next(
            (
                str(column)
                for column in df.columns
                if any(term in str(column).lower() for term in _MONEY_COLUMN_TERMS)
                and cls._column_currency_code(str(column)) is None
            ),
            None,
        )
        required_rows = (
            pd.to_numeric(df[raw_amount_column], errors="coerce").notna()
            if raw_amount_column is not None
            else pd.Series(True, index=df.index)
        )
        required_count = int(required_rows.sum())
        normalized_candidates: list[tuple[int, str, str]] = []
        for column in df.columns:
            column_name = str(column)
            code = cls._column_currency_code(column_name)
            if code is None or not any(
                term in column_name.lower() for term in _MONEY_COLUMN_TERMS
            ):
                continue
            populated = pd.to_numeric(
                df.loc[required_rows, column_name], errors="coerce"
            ).notna().sum()
            if int(populated) != required_count:
                continue
            normalized_candidates.append(
                (_CURRENCY_PRIORITY.index(code), column_name, code)
            )

        explicit_currency = cls._query_currency_code(query)
        if normalized_candidates:
            if explicit_currency is not None:
                matching = [
                    candidate
                    for candidate in normalized_candidates
                    if candidate[2] == explicit_currency
                ]
                if matching:
                    normalized_candidates = matching
                else:
                    return _CurrencySafetyPolicy(
                        currency_column=currency_column,
                        currencies=currencies,
                        required_column=currency_column,
                        explicit_currency=explicit_currency,
                    )
            _, normalized_column, _ = min(normalized_candidates)
            return _CurrencySafetyPolicy(
                currency_column=currency_column,
                currencies=currencies,
                required_column=normalized_column,
                normalized_amount_column=normalized_column,
                explicit_currency=explicit_currency,
            )

        return _CurrencySafetyPolicy(
            currency_column=currency_column,
            currencies=currencies,
            required_column=currency_column,
            explicit_currency=explicit_currency,
        )

    @staticmethod
    def _normalise_currency_text(value: Any) -> str:
        return re.sub(r"[\s_\-（()）【】\[\].]", "", str(value).lower())

    @classmethod
    def _column_currency_code(cls, value: str) -> Optional[str]:
        normalized = cls._normalise_currency_text(value)
        for code, aliases in _CURRENCY_ALIASES.items():
            if code.lower() in normalized or any(
                cls._normalise_currency_text(alias) in normalized
                for alias in aliases
            ):
                return code
        return None

    @classmethod
    def _query_currency_code(cls, query: str) -> Optional[str]:
        normalized = cls._normalise_currency_text(query)
        for code, aliases in _CURRENCY_ALIASES.items():
            if code.lower() in normalized or any(
                cls._normalise_currency_text(alias) in normalized
                for alias in aliases
            ):
                return code
        return None

    @staticmethod
    def _result_lineage_columns(tree: ast.AST, available: set[str]) -> set[str]:
        """Return source columns that flow into the final result_df assignment."""
        lineage: dict[str, tuple[set[str], set[str]]] = {}

        def expression_lineage(node: ast.AST) -> tuple[set[str], set[str]]:
            visitor = _ExpressionLineageVisitor(available)
            visitor.visit(node)
            return visitor.columns, visitor.dependencies

        for node in getattr(tree, "body", []):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = getattr(node, "value", None)
                if value is None:
                    continue
                columns, dependencies = expression_lineage(value)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    variable = _assignment_variable(target)
                    if variable is None:
                        continue
                    if isinstance(target, ast.Name):
                        next_columns = set(columns)
                        next_dependencies = set(dependencies)
                        if variable in next_dependencies and variable in lineage:
                            prior_columns, prior_dependencies = lineage[variable]
                            next_columns.update(prior_columns)
                            next_dependencies.remove(variable)
                            next_dependencies.update(prior_dependencies)
                        lineage[variable] = (next_columns, next_dependencies)
                    else:
                        prior_columns, prior_dependencies = lineage.get(variable, (set(), set()))
                        lineage[variable] = (
                            prior_columns | columns,
                            prior_dependencies | dependencies,
                        )
            elif isinstance(node, ast.AugAssign):
                variable = _assignment_variable(node.target)
                if variable is not None:
                    columns, dependencies = expression_lineage(node.value)
                    prior_columns, prior_dependencies = lineage.get(variable, (set(), set()))
                    lineage[variable] = (
                        prior_columns | columns,
                        prior_dependencies | dependencies,
                    )
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                variable = _mutated_variable(node.value)
                if variable is not None and variable in lineage:
                    columns, dependencies = expression_lineage(node.value)
                    prior_columns, prior_dependencies = lineage[variable]
                    lineage[variable] = (
                        prior_columns | columns,
                        prior_dependencies | dependencies,
                    )

        def expand(variable: str, seen: set[str]) -> set[str]:
            if variable in seen or variable not in lineage:
                return set()
            columns, dependencies = lineage[variable]
            resolved = set(columns)
            next_seen = seen | {variable}
            for dependency in dependencies:
                resolved.update(expand(dependency, next_seen))
            return resolved

        return expand("result_df", set())


_COLUMN_ARGUMENT_METHODS = {
    "agg", "aggregate", "drop", "drop_duplicates", "dropna", "fillna", "filter",
    "groupby", "join", "merge", "rename",
    "melt", "nlargest", "nsmallest", "pivot", "pivot_table", "set_index",
    "sort_values", "value_counts",
}


class _ExpressionLineageVisitor(ast.NodeVisitor):
    """Collect dataframe dependencies and source-column selectors from one expression."""

    def __init__(self, available: set[str]) -> None:
        self.available = available
        self.columns: set[str] = set()
        self.dependencies: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.dependencies.add(node.id)

    def visit_Dict(self, node: ast.Dict) -> None:
        # Dictionary keys are commonly output labels, not source-column reads.
        for value in node.values:
            self.visit(value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.columns.update(_literal_columns(node.slice, self.available))
        self.visit(node.value)
        self.visit(node.slice)

    def visit_Call(self, node: ast.Call) -> None:
        method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if method in _COLUMN_ARGUMENT_METHODS:
            for argument in node.args:
                self.columns.update(_literal_columns(argument, self.available))
            for keyword in node.keywords:
                self.columns.update(_literal_columns(keyword.value, self.available))
        if method in {"query", "eval"} and node.args:
            expression = node.args[0]
            if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
                self.columns.update(
                    column for column in self.available if column in expression.value
                )
        self.generic_visit(node)


def _literal_columns(node: ast.AST, available: set[str]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if node.value in available else set()
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        columns: set[str] = set()
        for element in node.elts:
            columns.update(_literal_columns(element, available))
        return columns
    if isinstance(node, ast.Dict):
        columns: set[str] = set()
        for key in node.keys:
            if key is not None:
                columns.update(_literal_columns(key, available))
        return columns
    return set()


def _assignment_variable(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        value = node.value
        while isinstance(value, (ast.Subscript, ast.Attribute)):
            value = value.value
        if isinstance(value, ast.Name):
            return value.id
    return None


def _mutated_variable(node: ast.Call) -> Optional[str]:
    if not isinstance(node.func, ast.Attribute):
        return None
    value = node.func.value
    while isinstance(value, (ast.Subscript, ast.Attribute, ast.Call)):
        if isinstance(value, ast.Call):
            value = value.func
        else:
            value = value.value
    return value.id if isinstance(value, ast.Name) else None
