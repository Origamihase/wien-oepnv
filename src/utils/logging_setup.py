"""Shared logging helpers with rotating file handlers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

try:  # pragma: no cover - support running as script
    from .env import get_int_env
except ModuleNotFoundError:  # pragma: no cover
    from utils.env import get_int_env  # type: ignore


_ALLOWED_ROOTS = {"docs", "data", "log"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG_DIR = Path("log")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass(frozen=True)
class RotatingLoggingConfig:
    """Summary of the applied logging configuration."""

    level: int
    log_dir: Path
    diagnostics_log: Path
    error_log: Path
    max_bytes: int
    backup_count: int


def _validate_path(path: Path, name: str) -> Path:
    """Ensure ``path`` stays within whitelisted directories."""

    resolved = path.resolve()
    bases = {Path.cwd().resolve(), _REPO_ROOT}
    for base in bases:
        try:
            rel = resolved.relative_to(base)
        except Exception:
            continue
        if rel.parts and rel.parts[0] in _ALLOWED_ROOTS:
            return resolved
    raise ValueError(f"{name} outside allowed directories")


def _resolve_env_path(env_name: str, default: str | Path, *, allow_fallback: bool = False) -> Path:
    """Return a repository-internal path for ``env_name``."""

    default_path = Path(default)
    raw = os.getenv(env_name)
    candidate_str = (raw or "").strip()

    if not candidate_str:
        _validate_path(default_path, env_name)
        resolved_default = Path(default_path)
        os.environ[env_name] = resolved_default.as_posix()
        return resolved_default

    candidate_path = Path(candidate_str)
    try:
        resolved = _validate_path(candidate_path, env_name)
    except ValueError:
        if not allow_fallback:
            raise
        _validate_path(default_path, env_name)
        fallback_path = Path(default_path)
        os.environ[env_name] = fallback_path.as_posix()
        return fallback_path
    os.environ[env_name] = resolved.as_posix()
    return resolved


def _logging_level_from_env(default_level: int = logging.INFO) -> int:
    raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, raw, default_level)
    return level if isinstance(level, int) else default_level


def _rotating_handler_exists(handlers: Iterable[logging.Handler], target: Path, min_level: int) -> bool:
    target = target.resolve()
    for handler in handlers:
        if isinstance(handler, RotatingFileHandler):
            handler_path = Path(getattr(handler, "baseFilename", "")).resolve()
            if handler_path == target and handler.level <= min_level:
                return True
    return False


def ensure_rotating_file_logging() -> RotatingLoggingConfig:
    """Attach rotating log handlers for diagnostics and errors.

    The diagnostics handler records INFO+ messages in ``log/diagnostics.log`` while
    the error handler keeps ``log/errors.log`` clean unless an ERROR entry is
    emitted.  Existing compatible handlers are re-used, so the function is safe to
    call multiple times.
    """

    level = _logging_level_from_env()
    log_dir = _resolve_env_path("LOG_DIR", _DEFAULT_LOG_DIR, allow_fallback=True)
    os.makedirs(log_dir, exist_ok=True)

    max_bytes = max(get_int_env("LOG_MAX_BYTES", 1_000_000), 0)
    backup_count = max(get_int_env("LOG_BACKUP_COUNT", 5), 0)

    root = logging.getLogger()
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    root.setLevel(level)

    fmt = logging.Formatter(_LOG_FORMAT)
    diagnostics_path = log_dir / "diagnostics.log"
    error_path = log_dir / "errors.log"

    if not _rotating_handler_exists(root.handlers, diagnostics_path, logging.INFO):
        diagnostics_handler = RotatingFileHandler(
            diagnostics_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        diagnostics_handler.setLevel(logging.INFO)
        diagnostics_handler.setFormatter(fmt)
        root.addHandler(diagnostics_handler)

    if not _rotating_handler_exists(root.handlers, error_path, logging.ERROR):
        error_handler = RotatingFileHandler(
            error_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(fmt)
        root.addHandler(error_handler)

    return RotatingLoggingConfig(
        level=level,
        log_dir=log_dir,
        diagnostics_log=diagnostics_path,
        error_log=error_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


__all__ = [
    "RotatingLoggingConfig",
    "ensure_rotating_file_logging",
    "_resolve_env_path",
    "_validate_path",
]
