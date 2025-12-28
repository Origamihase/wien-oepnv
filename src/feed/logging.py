"""Logging utilities for the feed builder."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from .config import (
    LOG_BACKUP_COUNT,
    LOG_DIR_PATH,
    LOG_FORMAT,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOG_TIMEZONE,
)
try:  # pragma: no cover - support package and script execution
    from utils.files import atomic_write
except ModuleNotFoundError:  # pragma: no cover
    from ..utils.files import atomic_write

LOG_DIR = LOG_DIR_PATH.as_posix()
error_log_path = Path(LOG_DIR) / "errors.log"
diagnostics_log_path = Path(LOG_DIR) / "diagnostics.log"

_LOGGING_CONFIGURED = False


class MaxLevelFilter(logging.Filter):
    """Filter that only lets records up to ``max_level`` through."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - simple predicate
        return record.levelno <= self._max_level


class JSONFormatter(logging.Formatter):
    """Minimal JSON logging formatter."""

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

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - exercised via logging
        message = record.getMessage()
        timestamp = datetime.fromtimestamp(record.created, LOG_TIMEZONE)
        payload: Dict[str, Any] = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        extras: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._DEFAULT_FIELDS:
                continue
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
        return JSONFormatter()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    formatter.converter = _vienna_time_converter
    return formatter


def configure_logging() -> None:
    """Configure the default logging handlers for the feed builder."""

    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setFormatter(_make_formatter())
        if isinstance(handler, logging.StreamHandler):
            handler.setLevel(level)

    error_log_path.touch(exist_ok=True)
    error_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(_make_formatter())
    root_logger.addHandler(error_handler)

    diagnostics_log_path.touch(exist_ok=True)
    diagnostics_handler = RotatingFileHandler(
        diagnostics_log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    diagnostics_handler.setLevel(logging.INFO)
    diagnostics_handler.addFilter(MaxLevelFilter(logging.ERROR - 1))
    diagnostics_handler.setFormatter(_make_formatter())
    root_logger.addHandler(diagnostics_handler)

    _LOGGING_CONFIGURED = True


_LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3})")


def prune_log_file(path: Path, *, now: datetime, keep_days: int = 7) -> None:
    """Remove log records older than ``keep_days`` from ``path``."""

    if keep_days <= 0:
        return
    if not path.exists():
        return

    if now.tzinfo is None:
        now = now.replace(tzinfo=LOG_TIMEZONE)
    else:
        now = now.astimezone(LOG_TIMEZONE)
    cutoff = now - timedelta(days=keep_days)
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return

    grouped: list[list[str]] = []
    current: list[str] = []
    for line in raw_lines:
        if _LOG_TIMESTAMP_RE.match(line):
            if current:
                grouped.append(current)
            current = [line]
        else:
            if not current:
                current = [line]
            else:
                current.append(line)
    if current:
        grouped.append(current)

    filtered: list[str] = []
    for record_lines in grouped:
        first = record_lines[0]
        match = _LOG_TIMESTAMP_RE.match(first)
        if not match:
            filtered.extend(record_lines)
            continue
        ts_raw = f"{match.group(1)} {match.group(2)},{match.group(3)}"
        try:
            ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f")
        except ValueError:
            filtered.extend(record_lines)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=LOG_TIMEZONE)
        else:
            ts = ts.astimezone(LOG_TIMEZONE)
        if ts >= cutoff:
            filtered.extend(record_lines)

    try:
        # Security: use atomic writes to avoid partial log truncation on interruption.
        with atomic_write(path, encoding="utf-8") as handle:
            handle.write("".join(filtered))
    except OSError:
        return


__all__ = [
    "MaxLevelFilter",
    "JSONFormatter",
    "configure_logging",
    "diagnostics_log_path",
    "error_log_path",
    "prune_log_file",
]
