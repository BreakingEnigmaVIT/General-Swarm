"""Swarm structlog setup; lives in logutil.py so Python never imports this file as the stdlib `logging` module."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import structlog
from structlog.types import EventDict, WrappedLogger

from observability.tracing import get_span_id, get_trace_id

# ── Custom processors ─────────────────────────────────────────────────────────


def _add_trace_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    tid = get_trace_id()
    sid = get_span_id()
    if tid:
        event_dict["trace_id"] = tid
    if sid:
        event_dict["span_id"] = sid
    return event_dict


# ── Public setup call ─────────────────────────────────────────────────────────


def configure_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Call once at startup, before any log statements."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_context,
    ]

    # Handlers
    handlers: list[logging.Handler] = []

    # Console — pretty with colours
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    handlers.append(console_handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Pretty console renderer
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=shared_processors,
    )
    console_handler.setFormatter(console_formatter)

    if log_file:
        json_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors,
        )
        # re-assign to last handler (file handler)
        handlers[-1].setFormatter(json_formatter)


def get_logger(name: str = "swarm") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
