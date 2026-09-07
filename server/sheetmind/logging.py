"""Application logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: str = "INFO", log_dir: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path / "sheetmind.log", encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name or "sheetmind")
