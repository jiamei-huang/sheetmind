"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("SHEETMIND_HOST", "127.0.0.1")
    port: int = int(os.getenv("SHEETMIND_PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_dir: str = os.getenv("LOG_DIR", "logs")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


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
