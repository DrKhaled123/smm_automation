"""
utils/logger.py — Structured, async-safe logging.
Uses structlog for machine-readable JSON in production,
rich-formatted output in development.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from rich.console import Console
from rich.logging import RichHandler


_console = Console(stderr=True)


def configure_logging(log_level: str = "INFO", log_dir: Path | None = None) -> None:
    """Call once at startup before any other module uses the logger."""

    # stdlib root logger
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_console, rich_tracebacks=True)],
    )

    # File handler (JSON lines for ingestion by Grafana/Loki etc.)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "automation.jsonl")
        file_handler.setLevel(log_level)
        logging.getLogger().addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
