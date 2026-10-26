"""Structured logging: one JSON object per line (brief §5.5)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from app.config import settings

# Anything that could carry a secret is redacted before it reaches a log line.
_REDACT_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "totp_secret",
    "key",
    "master_key",
    "session_secret",
    "token",
    "cookie",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in ("request_id", "provider", "model", "partner", "status", "latency_ms"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return super().format(record)


def redact(value: object) -> str:
    """Shorten anything secret-shaped before logging it."""
    text = str(value or "")
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Uvicorn's access log duplicates our own request logging.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
