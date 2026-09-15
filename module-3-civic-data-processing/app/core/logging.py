"""Structured logging with request correlation.

Logs are emitted as single-line JSON in production (``LOG_JSON=true``) and as a
readable format for local development. A context variable carries the
``request_id`` so that every stage of the pipeline can be correlated without
threading the identifier through every function signature.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any, Optional

from app.core.config import settings

request_id_var: ContextVar[Optional[str]] = ContextVar("civic_request_id", default=None)
stage_var: ContextVar[Optional[str]] = ContextVar("civic_stage", default=None)

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Render log records as compact JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        stage = stage_var.get()
        if stage:
            payload["stage"] = stage

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _safe_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """Readable single-line format used when ``LOG_JSON=false``."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get() or "-"
        stage = stage_var.get() or "-"
        base = f"%(asctime)s %(levelname)s [%(name)s] [req={request_id}] [stage={stage}] %(message)s"
        self._style._fmt = base
        return super().format(record)


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set, dict)):
        try:
            json.dumps(value, default=str)
            return value
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def configure_logging() -> None:
    """Configure the root logger exactly once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_json else PlainFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Uvicorn installs its own handlers; route them through ours.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def bind_request_context(request_id: Optional[str], stage: Optional[str] = None) -> None:
    request_id_var.set(request_id)
    stage_var.set(stage)


def set_stage(stage: str) -> None:
    stage_var.set(stage)
