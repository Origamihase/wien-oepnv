from __future__ import annotations

import logging
import json
from datetime import datetime
from typing import Any, Dict

from .config import LOG_FORMAT, LOG_TIMEZONE
try:
    from ..utils.logging import sanitize_log_message
except ImportError:
    try:
        from utils.logging import sanitize_log_message
    except ImportError:
        # Fallback to simple replacement if utils not available (e.g. running script directly)
        def sanitize_log_message(s: str, secrets: list[str] | None = None) -> str:
            return s.replace("\n", "\\n").replace("\r", "\\r")

class SafeFormatter(logging.Formatter):
    """
    A logging formatter that sanitizes messages before formatting.

    This ensures that:
    1. Secrets are masked.
    2. Control characters (newlines, etc.) are escaped to prevent log injection.
    3. ANSI codes are stripped.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Sanitize the raw message
        # We modify the record.msg temporarily or work on a copy to avoid side effects?
        # Ideally, we format the message first (args substitution) then sanitize.

        # Standard logging does: msg % args
        # But if args contains secrets, we want to sanitize them too.
        # sanitize_log_message handles string sanitization.

        # If we use record.getMessage(), it does the substitution.
        original_msg = record.getMessage()
        sanitized_msg = sanitize_log_message(original_msg)

        # We replace the message in the record temporarily for formatting
        # Be careful not to mutate record permanently if other formatters need raw data (unlikely here)
        # But for safety, we can clone? No, logging records are passed around.
        # Let's just update it.

        # Wait, if we use getMessage(), it merges args.
        # If we then set record.msg = sanitized_msg and record.args = (), we are safe.
        record.msg = sanitized_msg
        record.args = ()

        return super().format(record)


class SafeJSONFormatter(logging.Formatter):
    """JSON logging formatter that sanitizes values."""

    _DEFAULT_FIELDS = {
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
    }

    def format(self, record: logging.LogRecord) -> str:
        # Sanitize message content
        original_msg = record.getMessage()
        sanitized_msg = sanitize_log_message(original_msg)

        timestamp = datetime.fromtimestamp(record.created, LOG_TIMEZONE)
        payload: Dict[str, Any] = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitized_msg,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        extras: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._DEFAULT_FIELDS:
                continue
            # Basic sanitization for extras if they are strings
            if isinstance(value, str):
                extras[key] = sanitize_log_message(value)
            else:
                extras[key] = value
        if extras:
            payload["extra"] = extras

        return json.dumps(payload, ensure_ascii=False)


def _vienna_time_converter(timestamp: float | None) -> tuple:
    effective_timestamp = (
        timestamp
        if timestamp is not None
        else datetime.now(tz=LOG_TIMEZONE).timestamp()
    )
    return datetime.fromtimestamp(effective_timestamp, LOG_TIMEZONE).timetuple()


def _make_formatter() -> logging.Formatter:
    if LOG_FORMAT == "json":
        return SafeJSONFormatter()

    # Standard format string
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = SafeFormatter(fmt)
    formatter.converter = _vienna_time_converter
    return formatter
