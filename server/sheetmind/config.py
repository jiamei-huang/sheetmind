"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("SHEETMIND_HOST", "127.0.0.1")
    port: int = int(os.getenv("SHEETMIND_PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_dir: str = os.getenv("LOG_DIR", "logs")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    session_cookie_secure: bool = _bool_env("SHEETMIND_SESSION_COOKIE_SECURE")
    anonymous_session_ttl_days: int = _positive_int_env(
        "ANONYMOUS_SESSION_TTL_DAYS", 30
    )
    claim_legacy_projects: bool = _bool_env("SHEETMIND_CLAIM_LEGACY_PROJECTS")


settings = Settings()


def get_openai_api_key() -> str | None:
    value = os.getenv("OPENAI_API_KEY", "").strip()
    return value or None


# Runtime model providers import these names directly.
OPENAI_BASE_URL = settings.openai_base_url
LOG_LEVEL = settings.log_level
LOG_DIR = settings.log_dir
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
