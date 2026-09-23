"""Structured JSON logging.

One line per event, machine-parsable, with a request id that follows a call
from the HTTP edge through the tool, the database and the classifier.

Never logged: OAuth tokens, API keys, raw file bytes, and (unless
PC_LOG_LEVEL=DEBUG in a non-prod environment) message or memory content.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger.json import JsonFormatter

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
principal_var: ContextVar[str] = ContextVar("principal", default="-")
provider_var: ContextVar[str] = ContextVar("provider", default="-")
tool_var: ContextVar[str] = ContextVar("tool", default="-")

#: Substrings that force a field to be redacted wherever it appears in `extra`.
_SECRET_HINTS = (
    "token",
    "secret",
    "api_key",
    "apikey",
    "password",
    "authorization",
    "signing_key",
    "service_key",
)


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


class ContextFilter(logging.Filter):
    """Attach the ambient request context and redact anything secret-looking."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.principal = principal_var.get()
        record.provider = provider_var.get()
        record.tool = tool_var.get()
        for key in list(record.__dict__):
            if _is_secret(key):
                record.__dict__[key] = "[redacted]"
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
            timestamp=True,
        )
    )
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # These are chatty at INFO and say nothing we do not already log.
    for noisy in ("httpx", "httpx2", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, /, **fields: Any) -> None:
    """Emit a structured event, dropping None values for a tidier line."""
    logger.info(message, extra={k: v for k, v in fields.items() if v is not None})
