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
from time import perf_counter
from typing import Any, Dict, List, Optional

from sheetmind.config import settings
from sheetmind.exceptions import AIQuotaExhaustedError, ModelOutputTruncatedError

from .configs import ModelConfig

logger = logging.getLogger(__name__)


class ModelProvider:
    """
    Async model completion interface.

    One instance per ModelConfig.  Clients are created lazily on first use
    and cached for the life of the provider instance.
    """

    def __init__(self, config: ModelConfig, role: Optional[str] = None) -> None:
        self.config = config
        self.role = role
        self.max_output_tokens = settings.max_model_output_tokens
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
        requested_max_tokens = (
            max_tokens if max_tokens is not None else self.config.max_tokens
        )
        effective_max_tokens = min(
            max(1, int(requested_max_tokens)),
            self.max_output_tokens,
        )
        started = perf_counter()
        try:
            if provider == "openai":
                try:
                    result = await self._complete_openai(
                        messages,
                        max_tokens=effective_max_tokens,
                        temperature=temperature,
                        json_mode=json_mode,
                    )
                except ModelOutputTruncatedError:
                    if effective_max_tokens >= self.max_output_tokens:
                        raise
                    logger.warning(
                        "Model output truncated for role=%s at %d tokens; retrying once at %d",
                        self.role,
                        effective_max_tokens,
                        self.max_output_tokens,
                    )
                    result = await self._complete_openai(
                        messages,
                        max_tokens=self.max_output_tokens,
                        temperature=temperature,
                        json_mode=json_mode,
                    )
                self._record_trace(messages, result, perf_counter() - started)
                return result
        except Exception as exc:
            self._record_trace(messages, "", perf_counter() - started, error=str(exc))
            if self._status_code(exc) == 402:
                raise AIQuotaExhaustedError(
                    "The model API quota has been exhausted.",
                    error_code="AI_QUOTA_EXHAUSTED",
                    internal_detail=str(exc),
                ) from exc
            raise

        raise ValueError(
            f"ModelProvider: unsupported provider {provider!r}. "
            "Add a new _complete_<provider>() branch to extend."
        )

    @staticmethod
    def _status_code(exc: Exception) -> Optional[int]:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            return None

    def _record_trace(
        self,
        messages: List[Dict[str, str]],
        result: str,
        duration: float,
        error: Optional[str] = None,
    ) -> None:
        from ..tracing.current import current_trace
        from ..tracing.trace import EVT_MODEL_CALL

        trace = current_trace()
        if trace is None:
            return
        trace.add_event(
            EVT_MODEL_CALL,
            model_role=self.role,
            model_id=self.config.model_id,
            input_summary=f"messages={len(messages)} chars={sum(len(item.get('content', '')) for item in messages)}",
            output_summary=f"chars={len(result)}",
            duration_ms=duration * 1000,
            error=error,
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
            "max_tokens": max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content
        if str(getattr(choice, "finish_reason", "")).lower() == "length":
            raise ModelOutputTruncatedError(
                "The model response reached its output token limit.",
                error_code="MODEL_OUTPUT_TRUNCATED",
            )
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
