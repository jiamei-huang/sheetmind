"""
SheetMind Runtime — Model Router
=====================================
ModelRouter maps ModelRole → ModelProvider.
Skills call router.get_provider(ModelRole.CODE_GENERATION).complete(...).

Override model IDs via environment variables (see configs.py docstring)
or by passing an `overrides` dict at construction.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from .configs import DEFAULT_CONFIGS, ModelConfig, ModelRole
from .provider import ModelProvider


class ModelRouter:
    """
    Central routing table: ModelRole → ModelProvider.

    Usage:
        router = ModelRouter()
        provider = router.get_provider(ModelRole.CODE_GENERATION)
        text = await provider.complete(messages, system=system_prompt)

    Providers are cached after first access so the same client is reused
    across multiple calls for the same role.
    """

    def __init__(
        self,
        overrides: Optional[Dict[ModelRole, ModelConfig]] = None,
    ) -> None:
        # Start from defaults, apply overrides
        self._configs: Dict[ModelRole, ModelConfig] = {**DEFAULT_CONFIGS}
        self._explicit_overrides = set(overrides or {})
        if overrides:
            self._configs.update(overrides)
        self._apply_env_overrides()
        self._providers: Dict[ModelRole, ModelProvider] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_provider(self, role: ModelRole) -> ModelProvider:
        """Return a (cached) ModelProvider for the given role."""
        if role not in self._providers:
            config = self._configs.get(role)
            if config is None:
                raise ValueError(
                    f"ModelRouter: no config for role {role!r}. "
                    "Add it to DEFAULT_CONFIGS or pass it as an override."
                )
            self._providers[role] = ModelProvider(config, role=role.value)
        return self._providers[role]

    def config_for(self, role: ModelRole) -> ModelConfig:
        """Return the ModelConfig for a role (for inspection / logging)."""
        config = self._configs.get(role)
        if config is None:
            raise ValueError(f"ModelRouter: no config for role {role!r}")
        return config

    def summary(self) -> Dict[str, str]:
        """Return a {role: model_id} dict for logging / health checks."""
        return {role.value: cfg.model_id for role, cfg in self._configs.items()}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_env_overrides(self) -> None:
        """
        Check environment variables and update configs.
        Pattern: SHEETMIND_MODEL_<ROLE_UPPER>_ID=<model_id>
        e.g. SHEETMIND_MODEL_CODE_GENERATION_ID=gpt-4o
        """
        for role in ModelRole:
            env_key = f"SHEETMIND_MODEL_{role.value.upper().replace('-', '_')}_ID"
            model_id = os.environ.get(env_key)
            if (
                not model_id
                and role == ModelRole.QUERY_PLANNING
                and role not in self._explicit_overrides
            ):
                # QUERY_PLANNING was introduced after the original role set.
                # Existing deployments already configure ROUTING for the same
                # low-latency reasoning tier, so inherit it instead of sending
                # the OpenAI default model to a custom endpoint.
                model_id = os.environ.get("SHEETMIND_MODEL_ROUTING_ID")
            if model_id and role in self._configs:
                self._configs[role] = self._configs[role].model_copy(
                    update={"model_id": model_id}
                )

            provider_key = f"SHEETMIND_MODEL_{role.value.upper().replace('-', '_')}_PROVIDER"
            provider = os.environ.get(provider_key)
            if (
                not provider
                and role == ModelRole.QUERY_PLANNING
                and role not in self._explicit_overrides
            ):
                provider = os.environ.get("SHEETMIND_MODEL_ROUTING_PROVIDER")
            if provider and role in self._configs:
                self._configs[role] = self._configs[role].model_copy(
                    update={"provider": provider}
                )
