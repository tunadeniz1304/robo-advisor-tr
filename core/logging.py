"""Structured logging configuration built on top of ``structlog``.

Every module in the application should obtain its logger through
:func:`get_logger` so that log records are consistent, greppable and carry a
rich set of contextual metadata (timestamp, level, module, request id, event).

Configuration:
    * Development (default): pretty colored console output.
    * Production (``LOG_JSON=true``): newline-delimited JSON — the standard
      shape for collection by Docker/promtail/ELK and other log pipelines.

The configuration reads the same environment variables as
:class:`core.config.Settings`; it does not import the settings object to avoid
a circular dependency between the logging layer and the settings layer.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

import structlog

_DEFAULT_LEVEL: str = "INFO"


def _configure_stdlib(level: str) -> None:
    """Mirror structured configuration to the standard library logger.

    structlog's ``structlog.stdlib`` processor chain relies on ``logging``,
    therefore the root ``logging`` logger must forward records with a
    compatible level. Without this step, ``structlog`` records emitted through
    the stdlib bridge would be swallowed.
    """
    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric,
    )


def configure_logging(level: Optional[str] = None, log_json: Optional[bool] = None) -> None:
    """Configure structlog once per process.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR). Defaults to
            ``LOG_LEVEL`` env var, then ``INFO``.
        log_json: When ``True`` use JSON rendering. Defaults to ``LOG_JSON``
            env var, then ``False`` (pretty console).
    """
    if level is None:
        level = os.getenv("LOG_LEVEL", _DEFAULT_LEVEL).upper()
    if log_json is None:
        log_json = os.getenv("LOG_JSON", "false").lower() in {"1", "true", "yes"}

    _configure_stdlib(level)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # When we want pretty console output, add the event dictionary renderer
    # for the console; keep ``KeyValueRenderer`` out of JSON path.
    if log_json:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.ConsoleRenderer(colors=sys.stdout.isatty()),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level, logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **context: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Logger name, conventionally the module's ``__name__``.
        **context: Static key-value pairs bound to every log record emitted
            by the returned logger (e.g. ``service="api"``).

    Returns:
        A bound structured logger.
    """
    return structlog.get_logger(name, **context)


def configure_logging_from_env() -> None:
    """Configure logging reading ``LOG_LEVEL`` / ``LOG_JSON`` from the env.

    Convenience helper used at application startup.
    """
    configure_logging(
        level=os.getenv("LOG_LEVEL", _DEFAULT_LEVEL).upper(),
        log_json=os.getenv("LOG_JSON", "false").lower() in {"1", "true", "yes"},
    )


# Import at the bottom to allow direct ``from core.logging import LOGGER``.
LOGGER: structlog.stdlib.BoundLogger = get_logger("otonom.finansal.danisman")
