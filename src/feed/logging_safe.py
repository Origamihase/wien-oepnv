from __future__ import annotations

import logging
import json
from datetime import datetime
from typing import Any

from .config import LOG_FORMAT, LOG_TIMEZONE
from ..utils.logging import sanitize_log_message

class SafeFormatter(logging.Formatter):
    """
    A logging formatter that sanitizes messages before formatting.

    This ensures that:
    1. Secrets are masked.
    2. Control characters (newlines, etc.) are escaped to prevent log injection.
    3. ANSI codes are stripped.
    """

    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord(record.__dict__)
        original_msg = record.getMessage()
        sanitized_msg = sanitize_log_message(original_msg)
        record.msg = sanitized_msg
        record.args = ()
        formatted = super().format(record)
        return formatted.replace("\n", "\\n").replace("\r", "\\r")

    def formatException(self, ei: Any) -> str:
        s = super().formatException(ei)
        # Redact secrets but preserve newlines for readability in tracebacks
        return sanitize_log_message(s, strip_control_chars=False)


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
        payload: dict[str, Any] = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitized_msg,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._DEFAULT_FIELDS:
                continue
            extras[key] = value

        if extras:
            payload["extra"] = extras

        # We sanitize the full JSON string to catch secrets nested in dictionaries or lists
        # (e.g. extra={"context": {"api_key": "..."}}) which the previous per-field logic missed.
        dumped = json.dumps(payload, ensure_ascii=False)
        sanitized = sanitize_log_message(dumped, strip_control_chars=False)
        return sanitized.replace("\n", "\\n").replace("\r", "\\r")

    def formatException(self, ei: Any) -> str:
        s = super().formatException(ei)
        # Redact secrets but preserve newlines (JSON handles them)
        return sanitize_log_message(s, strip_control_chars=False)


def _vienna_time_converter(timestamp: float | None) -> Any:
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


def setup_script_logging(level: int = logging.INFO) -> None:
    """Configure root logger with :class:`SafeFormatter` for scripts.

    Replaces the ``logging.basicConfig(...)`` call that scripts in
    ``scripts/`` historically used. ``basicConfig`` installs a default
    :class:`logging.Formatter` which does NOT sanitise the formatted
    message — meaning a hostile exception text or upstream-controlled
    URL fragment passed via ``%s`` in a log call leaks unmodified into
    the script's stderr / log file.

    This helper installs a single :class:`StreamHandler` whose formatter
    is the project's standard :class:`SafeFormatter` (or
    :class:`SafeJSONFormatter` when ``LOG_FORMAT=json``), giving every
    script the same clear-text-logging-drift defence that
    ``src.build_feed.configure_logging`` provides for the production
    feed builder.

    Idempotency: a SafeFormatter handler is added exactly once per
    process. Subsequent calls only update the root level. Foreign
    handlers (most importantly pytest's caplog capture handler) are
    preserved — they predate our installation and clearing them would
    invalidate test fixtures that capture log records via the standard
    pytest mechanism. This matches the original
    ``logging.basicConfig(level=…, format=…)`` no-op-if-handlers
    behaviour while still installing the sanitising handler when it's
    truly absent.

    Args:
        level: Root-logger log level. Pass ``logging.DEBUG`` for
            ``--verbose`` script invocations; defaults to
            ``logging.INFO`` to match the historical script defaults.
    """
    logger = logging.getLogger()
    has_safe_formatter = any(
        isinstance(h.formatter, (SafeFormatter, SafeJSONFormatter))
        for h in logger.handlers
    )
    if not has_safe_formatter:
        handler = logging.StreamHandler()
        handler.setFormatter(_make_formatter())
        logger.addHandler(handler)
    logger.setLevel(level)
