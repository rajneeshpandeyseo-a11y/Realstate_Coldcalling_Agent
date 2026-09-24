"""Structured logging configuration.

Produces JSON-structured logs for observability. Secrets (API keys, tokens,
passwords) are never logged. Provides a `get_logger(name)` helper that can
attach a correlation id (e.g. call_id / lead_id) per log record.
"""

import json
import logging
import sys
from typing import Any

from app.config import settings

_RESERVED = {"name", "msg", "args", "levelname", "levelno", "pathname",
              "filename", "module", "exc_info", "exc_text", "stack_info",
              "lineno", "funcName", "created", "msecs", "relativeCreated",
              "thread", "threadName", "processName", "process", "taskName",
              "message", "asctime", "level"}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Fold a correlation context dict into the payload if present.
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        else:
            # Include any extra attributes that were attached to the record.
            for key, value in record.__dict__.items():
                if key not in _RESERVED and not key.startswith("_"):
                    payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Configure root logging once at application startup."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Silence noisy third-party loggers unless debugging.
    if not settings.DEBUG:
        for noisy in ("uvicorn.access", "httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that supports attaching a `ctx` dict per call.

    Usage:
        log = get_logger("app.calls")
        log.info("call start", extra={"ctx": {"call_id": call_id}})
    """
    return logging.getLogger(name)
