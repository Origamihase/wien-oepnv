"""Logging utilities for the feed builder."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import (
    LOG_BACKUP_COUNT,
    LOG_DIR_PATH,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOG_TIMEZONE,
)

from .logging_safe import SafeFormatter, SafeJSONFormatter, _make_formatter

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


def configure_logging() -> None:
    """Configure the default logging handlers for the feed builder."""

    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    # Use SafeFormatter for console output as well
    logging.basicConfig(level=level)

    # We must replace the formatter on the root logger's handlers created by basicConfig
    # or any existing handlers.
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Ensure all handlers use the safe formatter
    safe_formatter = _make_formatter()

    for handler in root_logger.handlers:
        handler.setFormatter(safe_formatter)
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
    error_handler.setFormatter(safe_formatter)
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
    diagnostics_handler.setFormatter(safe_formatter)
    root_logger.addHandler(diagnostics_handler)

    _LOGGING_CONFIGURED = True


_LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3})")

# Security: ``MAX_LOG_PRUNE_KEEP_DAYS`` is the retention-window ceiling for the
# in-place log-pruning helper. ``prune_log_file`` consumes ``keep_days`` as
# ``cutoff = now - timedelta(days=keep_days)`` (direct ``datetime - timedelta``
# arithmetic). The default callers in ``src/feed/reporting.py`` use the
# hardcoded 7-day default, but the function is exported as a public API and a
# future caller passing an env-controlled or user-controlled value (e.g. a
# hypothetical ``LOG_RETENTION_DAYS`` env var) would otherwise inherit the
# unbounded shape — at very large values the subtraction underflows past
# Python's year-1 datetime boundary and raises ``OverflowError: date value out
# of range``, propagating out of ``prune_log_file`` past the surrounding
# ``OSError`` handlers and crashing the cron job that owns the call. Capping
# inside the function (defense-in-depth) means every caller — current and
# future — inherits the ceiling without having to remember to add it.
# 365 days is generous (~52x default) and bounds ``now - timedelta(days=N)``
# safely within Python's datetime range. TIGHTEN-only contract mirrors
# ``MAX_STATE_RETENTION_DAYS`` / ``MAX_ENDS_AT_GRACE_MINUTES`` /
# ``MAX_CACHE_MAX_AGE_HOURS`` / ``MAX_FRESH_PUBDATE_WINDOW_MIN`` in
# ``src/feed/config.py`` — same env-cap drift family (env-derived integer
# feeding ``timedelta(unit=N)`` into ``datetime - timedelta`` arithmetic).
MAX_LOG_PRUNE_KEEP_DAYS = 365


def prune_log_file(path: Path, *, now: datetime, keep_days: int = 7) -> None:
    """Remove log records older than ``keep_days`` from ``path``."""

    if keep_days <= 0:
        return
    # Security: clamp ``keep_days`` to ``MAX_LOG_PRUNE_KEEP_DAYS`` to defeat the
    # ``datetime - timedelta`` underflow vector documented at the constant
    # declaration above. Without the cap a caller passing
    # ``keep_days=99999999`` would crash the cron job via OverflowError.
    if keep_days > MAX_LOG_PRUNE_KEEP_DAYS:
        keep_days = MAX_LOG_PRUNE_KEEP_DAYS
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
        # Security: Modify in-place to preserve file handle for RotatingFileHandler.
        # atomic_write would replace the inode, causing the active logger to write to a stale handle.
        # Use r+ to read/write without replacing inode.
        with path.open("r+", encoding="utf-8") as handle:
            handle.seek(0)
            handle.write("".join(filtered))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return


__all__ = [
    "MAX_LOG_PRUNE_KEEP_DAYS",
    "MaxLevelFilter",
    "SafeFormatter",
    "SafeJSONFormatter",
    "configure_logging",
    "diagnostics_log_path",
    "error_log_path",
    "prune_log_file",
]
