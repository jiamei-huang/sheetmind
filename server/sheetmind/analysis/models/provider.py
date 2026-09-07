"""
SheetMind Runtime — Model Provider
=======================================
Thin async wrapper around model API clients.
Skills call ModelProvider.complete() and never touch client SDKs directly.

Supported providers:
  "openai"   — OpenAI or any OpenAI-compatible endpoint
               (set OPENAI_BASE_URL to route to domestic proxies / local models)

Future providers to add here (not in Skills):
  "anthropic", "zhipu", "qwen", "local"
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .configs import ModelConfig

logger = logging.getLogger(__name__)


class ModelProvider:
    """
    Async model completion interface.

    One instance per ModelConfig.  Clients are created lazily on first use
    and cached for the life of the provider instance.
    """

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._client: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
    ) -> str:
        """
        Send a chat completion request and return the response text.

        Args:
            messages    — list of {role, content} dicts (user/assistant turns)
            system      — optional system prompt prepended automatically
            max_tokens  — override config max_tokens for this call
            temperature — override config temperature for this call
            json_mode   — if True, request JSON output (provider must support it)

        Returns:
            The model's response as a plain string.
        """
        if system:
            messages = [{"role": "system", "content": system}] + list(messages)

        provider = self.config.provider
        if provider == "openai":
            return await self._complete_openai(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
            )

        raise ValueError(
            f"ModelProvider: unsupported provider {provider!r}. "
            "Add a new _complete_<provider>() branch to extend."
        )

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _complete_openai(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        json_mode: bool,
    ) -> str:
        client = self._get_openai_client()

        kwargs: Dict[str, Any] = {
            "model": self.config.model_id,
            "messages": messages,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content or ""

    def _get_openai_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            ) from exc

        self._client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            timeout=float(self.config.timeout_seconds),
        )
        return self._client
