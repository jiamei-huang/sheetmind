"""
SheetMind Runtime — Model Configurations
=============================================
ModelRole   — enum of use-case roles; each maps to a model tier.
ModelConfig — pydantic model for one role's configuration.
DEFAULT_CONFIGS — default mapping.  Override via env vars or ModelRouter.

Environment override syntax:
    SHEETMIND_MODEL_<ROLE_UPPER>_ID       override model_id
    SHEETMIND_MODEL_<ROLE_UPPER>_PROVIDER override provider
e.g.
    SHEETMIND_MODEL_CODE_GENERATION_ID=claude-sonnet-5
    SHEETMIND_MODEL_ROUTING_ID=gpt-4o-mini
    SHEETMIND_MODEL_QUERY_PLANNING_ID=gpt-4o-mini
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel


class ModelRole(str, Enum):
    """
    Maps a use-case to a model tier.

    Cheap fast models handle routing, typing, and sheet selection.
    Strong code/reasoning models handle code generation and repair.
    Stable Chinese writing models handle insight text generation.
    """
    ROUTING          = "routing"           # 3-way RoutingHint, low stakes
    QUERY_PLANNING   = "query_planning"    # multi-step decomposition + dependencies
    SEMANTIC_TYPING  = "semantic_typing"   # column type inference
    SHEET_SELECTION  = "sheet_selection"   # pick relevant sheets from query
    CODE_GENERATION  = "code_generation"   # generate pandas code
    CODE_REPAIR      = "code_repair"       # fix broken code after executor error
    INSIGHT_WRITING  = "insight_writing"   # write Chinese summary text
    LONG_CONTEXT     = "long_context"      # large sheets / extended context


class ModelConfig(BaseModel):
    provider: str          # "openai" | "anthropic" | "zhipu" | "qwen" | "local"
    model_id: str
    max_tokens: int = 2500
    temperature: float = 0.1
    timeout_seconds: int = 60
    extra: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Default config table.
# All defaults use OpenAI-compatible endpoints.  Change provider + model_id
# to route specific roles to domestic models (ZhipuAI, Qwen, etc.)
# ---------------------------------------------------------------------------

DEFAULT_CONFIGS: Dict[ModelRole, ModelConfig] = {
    ModelRole.ROUTING: ModelConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        max_tokens=320,
        temperature=0.0,
        timeout_seconds=15,
    ),
    ModelRole.QUERY_PLANNING: ModelConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        max_tokens=1600,
        temperature=0.0,
        timeout_seconds=20,
    ),
    ModelRole.SEMANTIC_TYPING: ModelConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        max_tokens=2000,
        temperature=0.0,
        timeout_seconds=20,
    ),
    ModelRole.SHEET_SELECTION: ModelConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        max_tokens=512,
        temperature=0.0,
        timeout_seconds=20,
    ),
    ModelRole.CODE_GENERATION: ModelConfig(
        provider="openai",
        model_id="gpt-4o",
        max_tokens=2500,
        temperature=0.1,
        timeout_seconds=60,
    ),
    ModelRole.CODE_REPAIR: ModelConfig(
        provider="openai",
        model_id="gpt-4o",
        max_tokens=2500,
        temperature=0.1,
        timeout_seconds=60,
    ),
    ModelRole.INSIGHT_WRITING: ModelConfig(
        provider="openai",
        model_id="gpt-4o-mini",
        max_tokens=1024,
        temperature=0.3,
        timeout_seconds=30,
    ),
    ModelRole.LONG_CONTEXT: ModelConfig(
        provider="openai",
        model_id="gpt-4o",
        max_tokens=2500,
        temperature=0.1,
        timeout_seconds=120,
    ),
}
