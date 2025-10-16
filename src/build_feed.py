#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import inspect
import json
import os
import sys
import html
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
import re
import hashlib
import errno
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # pragma: no cover - make mypy prefer package imports
    from .utils.cache import read_cache
    from .utils.env import get_bool_env, get_int_env
else:  # pragma: no cover - allow running as package and as script
    try:
        from utils.cache import read_cache
        from utils.env import get_int_env, get_bool_env
    except ModuleNotFoundError:
        from .utils.cache import read_cache  # type: ignore
        from .utils.env import get_int_env, get_bool_env  # type: ignore

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None  # type: ignore

try:  # pragma: no cover - platform dependent
    import msvcrt  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    msvcrt = None  # type: ignore

# ---------------- Paths ----------------
_ALLOWED_ROOTS = {"docs", "data", "log"}
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_env_path(env_name: str, default: str | Path, *, allow_fallback: bool = False) -> Path:
    """Return a repository-internal path for ``env_name``.

    Empty or whitespace-only values fall back to ``default``.  Invalid
    non-empty values propagate the :class:`ValueError` raised by
    :func:`_validate_path`.
    """

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
        default_parts = Path(default_path).parts
        candidate_parts = candidate_path.parts
        if default_parts and len(candidate_parts) >= len(default_parts):
            if candidate_parts[-len(default_parts):] == default_parts:
                _validate_path(default_path, env_name)
                fallback = Path(default_path)
                os.environ[env_name] = fallback.as_posix()
                return fallback
        if not allow_fallback:
            raise
        _validate_path(default_path, env_name)
        fallback_path = Path(default_path)
        os.environ[env_name] = fallback_path.as_posix()
        return fallback_path
    os.environ[env_name] = resolved.as_posix()
    return resolved


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

# ---------------- Logging ----------------
LOG_TIMEZONE = ZoneInfo("Europe/Vienna")


def _vienna_time_converter(timestamp: float):
    return datetime.fromtimestamp(timestamp, LOG_TIMEZONE).timetuple()


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self._max_level


def _make_formatter() -> logging.Formatter:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    formatter.converter = _vienna_time_converter
    return formatter


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_level = getattr(logging, LOG_LEVEL, logging.INFO)
if not isinstance(_level, int):
    _level = logging.INFO

_DEFAULT_LOG_DIR = Path("log")
LOG_DIR_PATH = _resolve_env_path("LOG_DIR", _DEFAULT_LOG_DIR, allow_fallback=True)
LOG_DIR = LOG_DIR_PATH.as_posix()
LOG_MAX_BYTES = max(get_int_env("LOG_MAX_BYTES", 1_000_000), 0)
LOG_BACKUP_COUNT = max(get_int_env("LOG_BACKUP_COUNT", 5), 0)

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

root_logger = logging.getLogger()
root_logger.setLevel(min(_level, logging.WARNING))
for handler in root_logger.handlers:
    handler.setFormatter(_make_formatter())
    if isinstance(handler, logging.StreamHandler):
        handler.setLevel(_level)

error_log_path = Path(LOG_DIR) / "errors.log"
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

diagnostics_log_path = Path(LOG_DIR) / "diagnostics.log"
diagnostics_log_path.touch(exist_ok=True)
diagnostics_handler = RotatingFileHandler(
    diagnostics_log_path,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
diagnostics_handler.setLevel(logging.INFO)
diagnostics_handler.addFilter(_MaxLevelFilter(logging.ERROR - 1))
diagnostics_handler.setFormatter(_make_formatter())
root_logger.addHandler(diagnostics_handler)

log = logging.getLogger("build_feed")

_LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3})")


def _prune_log_file(path: Path, *, now: datetime, keep_days: int = 7) -> None:
    """Remove log records older than ``keep_days`` from ``path``.

    The function keeps log output grouped by records, so multi-line stack traces
    stay intact.  Lines without the expected timestamp prefix are preserved.
    """

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

    grouped: List[List[str]] = []
    current: List[str] = []
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

    filtered: List[str] = []
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
        path.write_text("".join(filtered), encoding="utf-8")
    except OSError:
        return


def _provider_display_name(fetch: Any, env: Optional[str] = None) -> str:
    provider_name = getattr(fetch, "_provider_cache_name", None)
    if provider_name:
        return str(provider_name)
    if env:
        env_name = PROVIDER_CACHE_KEYS.get(env)
        if env_name:
            return env_name
    name = getattr(fetch, "__name__", None)
    if name:
        return name
    return str(fetch)


def _clean_message(message: Optional[str]) -> str:
    if not message:
        return ""
    return re.sub(r"\s+", " ", message).strip()


@dataclass
class ProviderReport:
    name: str
    enabled: bool
    fetch_type: str = "unknown"
    status: str = "pending"  # ok, empty, error, disabled, skipped
    detail: Optional[str] = None
    items: Optional[int] = None
    duration: Optional[float] = None
    _started_at: Optional[float] = None

    def mark_disabled(self) -> None:
        self.enabled = False
        self.status = "disabled"

    def start(self) -> None:
        self._started_at = perf_counter()
        if self.status == "disabled":
            return
        self.status = "running"

    def finish(
        self,
        status: str,
        *,
        items: Optional[int] = None,
        detail: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> None:
        if duration is None and self._started_at is not None:
            duration = perf_counter() - self._started_at
        self.duration = duration
        self.items = items
        self.detail = detail
        self.status = status


class _RunErrorCollector(logging.Handler):
    def __init__(self, report: "RunReport") -> None:
        super().__init__(level=logging.ERROR)
        self.report = report
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - defensive
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.exc_info:
            try:
                exc_text = self._formatter.formatException(record.exc_info)
            except Exception:
                exc_text = ""
            if exc_text:
                msg = f"{msg}\n{exc_text}"
        source = record.name or "root"
        composed = f"{source}: {msg}" if msg else source
        self.report.add_error_message(composed)


@dataclass
class RunReport:
    statuses: List[Tuple[str, bool]]
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    providers: Dict[str, ProviderReport] = field(default_factory=dict)
    raw_item_count: Optional[int] = None
    final_item_count: Optional[int] = None
    durations: Dict[str, float] = field(default_factory=dict)
    feed_path: Optional[str] = None
    build_successful: bool = False
    exception_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    _error_messages: List[str] = field(default_factory=list)
    _seen_errors: set[str] = field(default_factory=set)
    finished_at: Optional[datetime] = None
    _error_collector: Optional[_RunErrorCollector] = None

    def __post_init__(self) -> None:
        for name, enabled in self.statuses:
            normalized = str(name)
            entry = ProviderReport(name=normalized, enabled=enabled)
            if not enabled:
                entry.mark_disabled()
            self.providers[normalized] = entry

    @property
    def run_id(self) -> str:
        return self.started_at.strftime("%Y%m%dT%H%M%SZ")

    def register_provider(self, name: str, enabled: bool, fetch_type: str) -> None:
        normalized = str(name)
        entry = self.providers.get(normalized)
        if entry is None:
            entry = ProviderReport(name=normalized, enabled=enabled, fetch_type=fetch_type)
            self.providers[normalized] = entry
        else:
            entry.enabled = enabled
            entry.fetch_type = fetch_type
        if not enabled:
            entry.mark_disabled()
        elif entry.status == "disabled":
            entry.status = "pending"

    def provider_started(self, name: str) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        entry.start()

    def provider_success(
        self,
        name: str,
        *,
        items: int,
        status: str = "ok",
        detail: Optional[str] = None,
    ) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        entry.finish(status, items=items, detail=_clean_message(detail))
        if status != "ok" and detail:
            self.warnings.append(f"Provider {name}: {detail}")

    def provider_error(self, name: str, message: str) -> None:
        entry = self.providers.get(name)
        if entry is None:
            entry = ProviderReport(name=name, enabled=True)
            self.providers[name] = entry
        entry.finish("error", detail=_clean_message(message))
        self.add_error_message(f"Provider {name}: {message}")

    def add_error_message(self, message: str) -> None:
        cleaned = _clean_message(message)
        if not cleaned:
            return
        if cleaned in self._seen_errors:
            return
        self._seen_errors.add(cleaned)
        self._error_messages.append(cleaned)

    @property
    def error_messages(self) -> List[str]:
        return list(self._error_messages)

    def has_errors(self) -> bool:
        if self.exception_message:
            return True
        if any(entry.status == "error" for entry in self.providers.values()):
            return True
        return bool(self._error_messages)

    def attach_error_collector(self) -> None:
        if self._error_collector is not None:
            return
        collector = _RunErrorCollector(self)
        logging.getLogger().addHandler(collector)
        self._error_collector = collector

    def detach_error_collector(self) -> None:
        if self._error_collector is None:
            return
        logging.getLogger().removeHandler(self._error_collector)
        self._error_collector = None

    def finish(
        self,
        *,
        build_successful: bool,
        raw_items: Optional[int] = None,
        final_items: Optional[int] = None,
        durations: Optional[Dict[str, float]] = None,
        feed_path: Optional[Path] = None,
    ) -> None:
        self.build_successful = build_successful
        if raw_items is not None:
            self.raw_item_count = raw_items
        if final_items is not None:
            self.final_item_count = final_items
        if durations:
            self.durations.update(durations)
        if feed_path is not None:
            self.feed_path = feed_path.as_posix()
        self.finished_at = datetime.now(timezone.utc)

    def record_exception(self, exc: Exception) -> None:
        message = f"{exc.__class__.__name__}: {exc}"
        self.exception_message = _clean_message(message)
        self.add_error_message(f"Ausnahme: {message}")

    def prune_logs(self) -> None:
        now = self.started_at
        _prune_log_file(diagnostics_log_path, now=now)
        _prune_log_file(error_log_path, now=now)

    def _provider_summary(self) -> str:
        summaries: List[str] = []
        for name in sorted(self.providers):
            entry = self.providers[name]
            details: List[str] = []
            if entry.items is not None and entry.status in {"ok", "empty"}:
                details.append(f"{entry.items} Items")
            if entry.detail:
                details.append(entry.detail)
            if entry.duration is not None:
                details.append(f"{entry.duration:.2f}s")
            details_str = ", ".join(details)
            if entry.status == "disabled":
                summaries.append(f"{name}:disabled")
                continue
            if entry.status == "pending":
                summaries.append(f"{name}:pending")
                continue
            if entry.status == "error":
                if details_str:
                    summaries.append(f"{name}:error({details_str})")
                else:
                    summaries.append(f"{name}:error")
                continue
            if entry.status == "empty":
                if details_str:
                    summaries.append(f"{name}:empty({details_str})")
                else:
                    summaries.append(f"{name}:empty")
                continue
            if entry.status == "ok":
                if details_str:
                    summaries.append(f"{name}:ok({details_str})")
                else:
                    summaries.append(f"{name}:ok")
                continue
            if entry.status == "running":
                summaries.append(f"{name}:running")
                continue
            summaries.append(f"{name}:{entry.status or 'unknown'}")
        return "; ".join(summaries)

    def _duration_summary(self) -> str:
        if not self.durations:
            return ""
        parts = [f"{key}={value:.2f}s" for key, value in sorted(self.durations.items())]
        return ", ".join(parts)

    def _items_summary(self) -> str:
        raw = self.raw_item_count if self.raw_item_count is not None else "?"
        final = self.final_item_count if self.final_item_count is not None else "?"
        return f"Items raw={raw}, final={final}"

    def diagnostics_message(self) -> str:
        status = "FAILED"
        if self.build_successful:
            status = "ERROR" if self.has_errors() else "OK"
        provider_summary = self._provider_summary()
        duration_summary = self._duration_summary()
        items_summary = self._items_summary()
        components = [
            f"Feed-Lauf {self.run_id}",
            f"Status={status}",
            items_summary,
        ]
        if duration_summary:
            components.append(f"Dauer: {duration_summary}")
        if provider_summary:
            components.append(f"Provider: {provider_summary}")
        if self.feed_path:
            components.append(f"Feed={self.feed_path}")
        if self.exception_message and not self.build_successful:
            components.append(f"Fehler={self.exception_message}")
        if self.warnings:
            components.append(f"Warnungen: {'; '.join(self.warnings)}")
        return " | ".join(components)

    def log_results(self) -> None:
        try:
            diagnostics = self.diagnostics_message()
            log.info(diagnostics)
            if self.has_errors():
                log.info(
                    "Hinweis: Fehler während des Feed-Laufs – Details siehe %s",
                    error_log_path,
                )
        finally:
            self.detach_error_collector()

# Mapping of environment variables to provider cache loaders
PROVIDER_CACHE_KEYS: Dict[str, str] = {
    "WL_ENABLE": "wl",
    "OEBB_ENABLE": "oebb",
    "VOR_ENABLE": "vor",
}

def read_cache_wl() -> List[Any]:
    return read_cache("wl")


def read_cache_oebb() -> List[Any]:
    return read_cache("oebb")


def read_cache_vor() -> List[Any]:
    return read_cache("vor")


PROVIDERS: List[Tuple[str, Any]] = [
    ("WL_ENABLE", read_cache_wl),
    ("OEBB_ENABLE", read_cache_oebb),
    ("VOR_ENABLE", read_cache_vor),
]

for env, loader in PROVIDERS:
    provider_name = PROVIDER_CACHE_KEYS.get(env)
    if provider_name is None:
        continue
    try:
        loader.__name__ = f"read_cache_{provider_name}"
    except (AttributeError, TypeError):  # pragma: no cover - defensive only
        pass
    setattr(loader, "_provider_cache_name", provider_name)


def _provider_statuses() -> List[Tuple[str, bool]]:
    statuses: List[Tuple[str, bool]] = []
    seen_envs: set[str] = set()
    for env, fetch in PROVIDERS:
        if env in seen_envs:
            continue
        seen_envs.add(env)
        provider_name = getattr(fetch, "_provider_cache_name", None)
        if not provider_name:
            provider_name = PROVIDER_CACHE_KEYS.get(env)
        if not provider_name:
            provider_name = getattr(fetch, "__name__", env.lower())
        provider_display = str(provider_name)
        statuses.append((provider_display, bool(get_bool_env(env, True))))
    return statuses


def _log_startup_summary(statuses: List[Tuple[str, bool]]) -> None:
    enabled = sorted(name for name, is_enabled in statuses if is_enabled)
    disabled = sorted(name for name, is_enabled in statuses if not is_enabled)

    enabled_display = ", ".join(enabled) if enabled else "keine"
    log.info(
        "Starte Feed-Bau: %s aktiv (Timeout=%ss, MaxItems=%d, Worker=%s)",
        enabled_display,
        PROVIDER_TIMEOUT,
        MAX_ITEMS,
        PROVIDER_MAX_WORKERS or "auto",
    )
    if disabled:
        log.info("Deaktivierte Provider: %s", ", ".join(disabled))


def _validate_configuration(statuses: List[Tuple[str, bool]]) -> None:
    enabled_count = sum(1 for _, is_enabled in statuses if is_enabled)
    if not statuses:
        log.warning("Keine Provider registriert – es werden keine Items gesammelt.")
    elif enabled_count == 0:
        log.error(
            "Alle Provider deaktiviert – Feed bleibt leer, bitte Konfiguration prüfen."
        )

    if MAX_ITEMS == 0:
        log.warning("MAX_ITEMS ist 0 – der Feed wird ohne Einträge erzeugt.")
    if FEED_TTL == 0:
        log.warning(
            "FEED_TTL ist 0 – Clients werten den Feed unmittelbar als abgelaufen."
        )
    if PROVIDER_TIMEOUT == 0 and enabled_count:
        log.warning(
            "PROVIDER_TIMEOUT ist 0 – Netzwerkprovider haben keine Zeit für Antworten."
        )
    if MAX_ITEM_AGE_DAYS > ABSOLUTE_MAX_AGE_DAYS:
        log.warning(
            "MAX_ITEM_AGE_DAYS (%s) übersteigt ABSOLUTE_MAX_AGE_DAYS (%s) – ältere Items "
            "werden dennoch durch den absoluten Grenzwert verworfen.",
            MAX_ITEM_AGE_DAYS,
            ABSOLUTE_MAX_AGE_DAYS,
        )

# ---------------- ENV ----------------
OUT_PATH = _resolve_env_path("OUT_PATH", Path("docs/feed.xml")).as_posix()
FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK = os.getenv("FEED_LINK", "https://github.com/Origamihase/wien-oepnv")
FEED_DESC = os.getenv("FEED_DESC", "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
# Defaultwerte für die Feed-Erzeugung
DEFAULT_FEED_TTL = 15
DEFAULT_MAX_ITEM_AGE_DAYS = 365
DEFAULT_ABSOLUTE_MAX_AGE_DAYS = 540

FEED_TTL = max(get_int_env("FEED_TTL", DEFAULT_FEED_TTL), 0)

DESCRIPTION_CHAR_LIMIT = max(get_int_env("DESCRIPTION_CHAR_LIMIT", 170), 0)
FRESH_PUBDATE_WINDOW_MIN = get_int_env("FRESH_PUBDATE_WINDOW_MIN", 5)
MAX_ITEMS = max(get_int_env("MAX_ITEMS", 10), 0)
MAX_ITEM_AGE_DAYS = max(
    get_int_env("MAX_ITEM_AGE_DAYS", DEFAULT_MAX_ITEM_AGE_DAYS), 0
)
ABSOLUTE_MAX_AGE_DAYS = max(
    get_int_env("ABSOLUTE_MAX_AGE_DAYS", DEFAULT_ABSOLUTE_MAX_AGE_DAYS), 0
)
ENDS_AT_GRACE_MINUTES = max(get_int_env("ENDS_AT_GRACE_MINUTES", 10), 0)
PROVIDER_TIMEOUT = max(get_int_env("PROVIDER_TIMEOUT", 25), 0)
PROVIDER_MAX_WORKERS = max(get_int_env("PROVIDER_MAX_WORKERS", 0), 0)

STATE_FILE = _resolve_env_path("STATE_PATH", Path("data/first_seen.json"))  # nur Einträge aus *aktuellem* Feed
STATE_RETENTION_DAYS = max(get_int_env("STATE_RETENTION_DAYS", 60), 0)

RFC = "%a, %d %b %Y %H:%M:%S %z"

# ---------------- Helpers ----------------

def _to_utc(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC.

    If ``dt`` already contains timezone information, it is converted to UTC
    using ``astimezone``.  Previously, aware datetimes were returned as-is,
    which meant that values in non-UTC zones stayed in their original
    timezone.  With this change all consumers receive a true UTC value.
    Naive datetimes are assumed to already represent UTC and will simply be
    tagged accordingly.
    """

    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _fmt_rfc2822(dt: datetime) -> str:
    try:
        return format_datetime(_to_utc(dt))
    except Exception:
        log.exception(
            "Konnte Datum %r nicht per format_datetime formatieren – nutze strftime-Fallback.",
            dt,
        )
        return _to_utc(dt).strftime(RFC)


_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def format_local_times(
    start: Optional[datetime], end: Optional[datetime]
) -> str:
    start_local: Optional[datetime] = None
    end_local: Optional[datetime] = None

    if isinstance(start, datetime):
        start_local = _to_utc(start).astimezone(_VIENNA_TZ)
    if isinstance(end, datetime):
        end_local = _to_utc(end).astimezone(_VIENNA_TZ)

    if start_local and end_local and (end_local - start_local).days > 180:
        end_local = None

    today = datetime.now(_VIENNA_TZ)

    if start_local:
        if end_local:
            if (
                start_local.date() == end_local.date()
                and start_local.date() > today.date()
            ):
                return f"Am {start_local:%d.%m.%Y}"
            if end_local <= start_local:
                if start_local.date() > today.date():
                    return f"Ab {start_local:%d.%m.%Y}"
                return f"Seit {start_local:%d.%m.%Y}"
            if start_local.date() == end_local.date():
                return f"Seit {start_local:%d.%m.%Y}"
            return f"{start_local:%d.%m.%Y} – {end_local:%d.%m.%Y}"
        if start_local.date() > today.date():
            return f"Ab {start_local:%d.%m.%Y}"
        return f"Seit {start_local:%d.%m.%Y}"
    if end_local:
        return f"bis {end_local:%d.%m.%Y}"
    return ""

# Entfernt XML-unerlaubte Kontrollzeichen (außer \t, \n, \r)
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)

# Prefix pattern for line identifiers like "U1/U2: "
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3})$")

_LINE_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20})\s*:\s*"
)

DATE_RANGE_RE = re.compile(
    r"^\s*(\d{2}\.\d{2}\.\d{4})\s*(?:-|–|bis)\s*(\d{2}\.\d{2}\.\d{4})\s*$",
    re.IGNORECASE,
)

_ELLIPSIS = " …"
_SENTENCE_END_RE = re.compile(r"[.!?…](?=\s|$)")
_WHITESPACE_RE = re.compile(r"\s+")
_ISO_TZ_FIX_RE = re.compile(r"([+-]\d{2})(\d{2})$")

def _sanitize_text(s: str) -> str:
    return _CONTROL_RE.sub("", s or "")

def _cdata(s: str) -> str:
    # CDATA sicher splitten, falls ']]>' im Text vorkommt
    s = s.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{s}]]>"

def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s or "")

def _clip_text_html(text: str, limit: int) -> str:
    """Für TV knapper machen. Gibt immer Plaintext zurück und kürzt falls nötig."""
    plain = html.unescape(_strip_html(text or ""))
    if limit <= 0 or len(plain) <= limit:
        return plain
    prefix = plain[:limit]
    candidates = []

    # Satzende bevorzugt, z. B. "...!" oder "...?"
    for match in _SENTENCE_END_RE.finditer(prefix):
        end = match.end()
        if end:
            candidates.append(end)

    # Wortgrenzen (Whitespace) als Fallback
    for match in _WHITESPACE_RE.finditer(prefix):
        start = match.start()
        if start:
            candidates.append(start)

    # Wenn das nächste Zeichen bereits eine Grenze ist, darf der aktuelle Block stehen bleiben
    next_char = plain[limit] if limit < len(plain) else ""
    if next_char and (next_char.isspace() or next_char in ".,;:!?…"):
        candidates.append(limit)

    clip_pos = max((pos for pos in candidates if 0 < pos <= limit), default=None)

    if clip_pos is None:
        truncated = prefix.rstrip()
    else:
        truncated = prefix[:clip_pos].rstrip()
        if not truncated:
            truncated = prefix.rstrip()

    return truncated + _ELLIPSIS

def _parse_lines_from_title(title: str) -> List[str]:
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return []

    tokens: List[str] = []
    for raw in m.group(1).split("/"):
        token = raw.strip()
        if not token:
            continue
        normalized = token.upper()
        if _LINE_TOKEN_RE.match(normalized):
            tokens.append(normalized)
    return tokens

def _ymd_or_none(dt: Optional[datetime]) -> str:
    if isinstance(dt, datetime):
        return _to_utc(dt).date().isoformat()
    return "None"


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse ISO8601 timestamps (incl. ``Z`` suffix and compact offsets)."""

    if isinstance(value, datetime):
        if value.tzinfo:
            return value
        return value.replace(tzinfo=timezone.utc)

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None

        attempts = [candidate]
        if candidate.endswith("Z"):
            attempts.append(candidate[:-1] + "+00:00")
        match = _ISO_TZ_FIX_RE.search(candidate)
        if match:
            attempts.append(candidate[: match.start()] + f"{match.group(1)}:{match.group(2)}")

        last_error: Optional[Exception] = None
        for attempt in attempts:
            try:
                parsed = datetime.fromisoformat(attempt)
            except ValueError as exc:
                last_error = exc
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

        if last_error is not None:
            log.debug("Datetime-Parsing fehlgeschlagen für %r (%s)", value, last_error)

    return None


def _coerce_datetime_field(it: Dict[str, Any], field: str) -> Optional[datetime]:
    value = it.get(field)
    if value is None:
        return None

    parsed = _parse_datetime(value)
    if parsed is None:
        if isinstance(value, str):
            log.warning("%s Parsefehler: %r", field, value)
        it[field] = None
        return None

    it[field] = parsed
    return parsed


def _normalize_item_datetimes(
    items: List[Dict[str, Any]],
    fields: Tuple[str, ...] = ("pubDate", "starts_at", "ends_at"),
) -> List[Dict[str, Any]]:
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in fields:
            _coerce_datetime_field(item, field)
    return items

# ---------------- State (first_seen) ----------------

def _lock_length(fileobj: Any) -> int:
    try:
        fileno = fileobj.fileno()
    except (AttributeError, OSError):
        return 1

    try:
        size = os.fstat(fileno).st_size
    except OSError:
        try:
            current = fileobj.tell()
            fileobj.seek(0, os.SEEK_END)
            size = fileobj.tell()
            fileobj.seek(current, os.SEEK_SET)
        except Exception:
            return 1
    return max(int(size), 1)


def _acquire_file_lock(fileobj: Any, exclusive: bool) -> None:
    if fcntl is not None:  # pragma: no branch - simple POSIX case
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        while True:
            try:
                fcntl.flock(fileobj.fileno(), flag)
                return
            except OSError as exc:  # pragma: no cover - rare EINTR handling
                if exc.errno != errno.EINTR:
                    raise
    elif msvcrt is not None:  # pragma: no cover - Windows fallback
        length = _lock_length(fileobj)
        shared_flag = getattr(msvcrt, "LK_RLCK", getattr(msvcrt, "LK_LOCK"))
        mode = msvcrt.LK_LOCK if exclusive else shared_flag
        current = None
        try:
            current = fileobj.tell()
        except Exception:
            current = None
        fileobj.seek(0)
        try:
            msvcrt.locking(fileobj.fileno(), mode, length)
        finally:
            if current is not None:
                fileobj.seek(current)


def _release_file_lock(fileobj: Any) -> None:
    if fcntl is not None:  # pragma: no branch - simple POSIX case
        while True:
            try:
                fcntl.flock(fileobj.fileno(), fcntl.LOCK_UN)
                return
            except OSError as exc:  # pragma: no cover - rare EINTR handling
                if exc.errno != errno.EINTR:
                    raise
    elif msvcrt is not None:  # pragma: no cover - Windows fallback
        length = _lock_length(fileobj)
        unlock_flag = getattr(msvcrt, "LK_UNLCK", getattr(msvcrt, "LK_UNLOCK", None))
        if unlock_flag is None:  # pragma: no cover - extremely unlikely
            return
        current = None
        try:
            current = fileobj.tell()
        except Exception:
            current = None
        fileobj.seek(0)
        try:
            msvcrt.locking(fileobj.fileno(), unlock_flag, length)
        finally:
            if current is not None:
                fileobj.seek(current)


@contextmanager
def _file_lock(fileobj: Any, *, exclusive: bool) -> Iterator[None]:
    locked = False
    try:
        _acquire_file_lock(fileobj, exclusive)
        locked = True
    except Exception as exc:  # pragma: no cover - lock failures are rare
        log.debug("Dateisperre fehlgeschlagen (%s) – fahre ohne Lock fort.", exc)
    try:
        yield
    finally:
        if locked:
            try:
                _release_file_lock(fileobj)
            except Exception as exc:  # pragma: no cover - release failures are rare
                log.debug("Dateisperre konnte nicht gelöst werden: %s", exc)


def _load_state() -> Dict[str, Dict[str, Any]]:
    path = _validate_path(STATE_FILE, "STATE_PATH")
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            with _file_lock(f, exclusive=False):
                data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("State laden fehlgeschlagen (%s) – starte leer.", e)
        return {}

    retention_cutoff: Optional[datetime] = None
    if STATE_RETENTION_DAYS > 0:
        now_utc = _to_utc(datetime.now(timezone.utc))
        retention_cutoff = now_utc - timedelta(days=STATE_RETENTION_DAYS)

    out: Dict[str, Dict[str, Any]] = {}
    for ident, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            raw_first_seen = entry.get("first_seen", "")
            fs_dt = datetime.fromisoformat(str(raw_first_seen))
            fs_utc = _to_utc(fs_dt)
        except Exception:
            log.warning(
                "State-Eintrag %s hat unparsebares first_seen: %r", ident, entry.get("first_seen")
            )
            continue

        if retention_cutoff and fs_utc < retention_cutoff:
            log.debug(
                "State-Eintrag %s älter als %s Tage – entferne Eintrag.",
                ident,
                STATE_RETENTION_DAYS,
            )
            continue

        entry["first_seen"] = fs_utc.isoformat()
        out[ident] = entry
    return out

def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    path = _validate_path(STATE_FILE, "STATE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with path.open("a+", encoding="utf-8") as lock_file:
        with _file_lock(lock_file, exclusive=True):
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)

def _identity_for_item(item: Dict[str, Any]) -> str:
    """
    Stabile Identität unabhängig von Titel-Kosmetik.
      - Wenn Provider _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link (vom RSS stabil).
      - WL/sonstige: Quelle|Kategorie|Linienpräfix + Start-YYYY-MM-DD.
    """
    if item.get("_identity"):
        return str(item["_identity"])

    title = item.get("title") or ""
    sa = item.get("starts_at")
    ea = item.get("ends_at")
    sa_str = _to_utc(sa).isoformat() if isinstance(sa, datetime) else "None"
    ea_str = _to_utc(ea).isoformat() if isinstance(ea, datetime) else "None"
    fuzzy_raw = f"{title}|{sa_str}|{ea_str}"
    fuzzy_hash = hashlib.sha1(fuzzy_raw.encode("utf-8")).hexdigest()

    source = (item.get("source") or "").lower()
    category = (item.get("category") or "").lower()
    if "öbb" in source or "oebb" in source:
        return f"oebb|F={fuzzy_hash}"

    lines = _parse_lines_from_title(title)
    lines_part = "L=" + "/".join(lines) if lines else "L="
    start_day = _ymd_or_none(sa)
    base = f"{source}|{category}|{lines_part}|D={start_day}"
    if source and category:
        if not lines:
            if item.get("title"):
                return f"{base}|T={item['title']}|F={fuzzy_hash}"

            raw = json.dumps(item, sort_keys=True, default=str)
            hashed = hashlib.sha1(raw.encode("utf-8")).hexdigest()
            return f"{base}|H={hashed}|F={fuzzy_hash}"
        return f"{base}|F={fuzzy_hash}"
    # Fallback: Ohne Quelle/Kategorie Titel oder vollständigen Hash anhängen
    if item.get("title"):
        return f"{base}|T={item['title']}|F={fuzzy_hash}"
    raw = json.dumps(item, sort_keys=True, default=str)
    hashed = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"{base}|H={hashed}|F={fuzzy_hash}"

# ---------------- Pipeline ----------------

def _collect_items(report: Optional[RunReport] = None) -> List[Dict[str, Any]]:
    if report is None:
        report = RunReport(_provider_statuses())
    items: List[Dict[str, Any]] = []

    cache_fetchers: List[Any] = []
    network_fetchers: List[Any] = []
    provider_names: Dict[Any, str] = {}
    for env, fetch in PROVIDERS:
        provider_name = _provider_display_name(fetch, env)
        enabled = bool(get_bool_env(env, True))
        fetch_type = "cache" if getattr(fetch, "_provider_cache_name", None) else "network"
        report.register_provider(provider_name, enabled, fetch_type)
        if not enabled:
            continue
        provider_names[fetch] = provider_name
        if getattr(fetch, "_provider_cache_name", None):
            cache_fetchers.append(fetch)
        else:
            network_fetchers.append(fetch)

    if not cache_fetchers and not network_fetchers:
        return []

    def _merge_result(fetch: Any, result: Any, provider_name: str) -> None:
        name = getattr(fetch, "__name__", str(fetch))
        if not isinstance(result, list):
            log.error("%s fetch gab keine Liste zurück: %r", name, result)
            report.provider_error(provider_name, "Ungültige Antwort (keine Liste)")
            return
        _normalize_item_datetimes(result)
        items.extend(result)
        count = len(result)
        if count == 0:
            log.warning(
                "Cache für Provider '%s' leer – generiere Feed ohne aktuelle Daten.",
                provider_name,
            )
            report.provider_success(
                provider_name,
                items=count,
                status="empty",
                detail="Keine aktuellen Daten",
            )
        else:
            report.provider_success(provider_name, items=count)

    for fetch in cache_fetchers:
        name = getattr(fetch, "__name__", str(fetch))
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        report.provider_started(provider_name)
        try:
            result = fetch()
        except Exception as exc:
            log.exception("%s fetch fehlgeschlagen: %s", name, exc)
            report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
            continue
        _merge_result(fetch, result, provider_name)

    if not network_fetchers:
        return items

    futures: Dict[Any, Tuple[Any, str]] = {}
    desired_workers = len(network_fetchers)
    if PROVIDER_MAX_WORKERS > 0:
        if desired_workers > PROVIDER_MAX_WORKERS:
            log.debug(
                "Begrenze Provider-Threads von %s auf %s", desired_workers, PROVIDER_MAX_WORKERS
            )
        desired_workers = min(desired_workers, PROVIDER_MAX_WORKERS)
    # ThreadPoolExecutor erlaubt max_workers nicht als 0; daher mindestens 1
    executor = ThreadPoolExecutor(max_workers=max(1, desired_workers))
    timed_out = False
    try:
        for fetch in network_fetchers:
            provider_name = provider_names.get(fetch, _provider_display_name(fetch))
            report.provider_started(provider_name)
            futures[executor.submit(fetch)] = (fetch, provider_name)
        try:
            for future in as_completed(futures, timeout=PROVIDER_TIMEOUT):
                fetch, provider_name = futures[future]
                name = getattr(fetch, "__name__", str(fetch))
                try:
                    result = future.result()
                except TimeoutError:
                    log.error("%s fetch Timeout nach %ss", name, PROVIDER_TIMEOUT)
                    report.provider_error(
                        provider_name,
                        f"Timeout nach {PROVIDER_TIMEOUT}s",
                    )
                except Exception as exc:
                    log.exception("%s fetch fehlgeschlagen: %s", name, exc)
                    report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
                else:
                    _merge_result(fetch, result, provider_name)
        except TimeoutError:
            timed_out = True
            log.error("Provider-Timeout nach %ss", PROVIDER_TIMEOUT)
            executor.shutdown(wait=False, cancel_futures=True)
            for future, (fetch, provider_name) in futures.items():
                if future.done():
                    continue
                report.provider_error(
                    provider_name,
                    f"Timeout nach {PROVIDER_TIMEOUT}s (globaler Abbruch)",
                )
    finally:
        if not timed_out:
            executor.shutdown(wait=True)

    return items


def _invoke_collect_items(report: RunReport) -> List[Dict[str, Any]]:
    collect_fn = _collect_items
    try:
        signature = inspect.signature(collect_fn)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        signature = None
    if signature is not None:
        params = signature.parameters
        if not params:
            return collect_fn()
        if "report" in params:
            return collect_fn(report=report)
        if all(
            param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
            for param in params.values()
        ):
            return collect_fn()
    return collect_fn(report=report)


def _drop_old_items(
    items: List[Dict[str, Any]],
    now: datetime,
    state: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Entferne Items, die zu alt sind oder bereits beendet wurden.

    Neben ``pubDate``/``starts_at`` wird – falls vorhanden – ``first_seen`` aus dem
    geladenen State als Altersreferenz verwendet. Das betrifft Items ohne
    Datumsangaben, die andernfalls ewig im Feed verbleiben würden.
    """

    out: List[Dict[str, Any]] = []
    now_utc = _to_utc(now)
    for it in items:
        if not isinstance(it, dict):
            continue

        ident = _identity_for_item(it)
        state_entry = state.get(ident) if isinstance(state, dict) else None

        ends_at = it.get("ends_at")
        if isinstance(ends_at, datetime):
            if _to_utc(ends_at) < now_utc - timedelta(minutes=ENDS_AT_GRACE_MINUTES):
                continue

        dt = it.get("pubDate") or it.get("starts_at")
        age_days: Optional[float] = None
        if isinstance(dt, datetime):
            age_days = (now_utc - _to_utc(dt)).total_seconds() / 86400.0
        elif isinstance(state_entry, dict):
            raw_first_seen = state_entry.get("first_seen")
            if raw_first_seen is not None:
                try:
                    first_seen_dt = datetime.fromisoformat(str(raw_first_seen))
                except Exception:
                    log.warning(
                        "first_seen Parsefehler: %r – ignoriere für %s",
                        raw_first_seen,
                        ident,
                    )
                else:
                    if first_seen_dt.tzinfo is None:
                        first_seen_dt = first_seen_dt.replace(tzinfo=timezone.utc)
                    age_days = (now_utc - _to_utc(first_seen_dt)).total_seconds() / 86400.0

        if age_days is not None:
            if age_days > ABSOLUTE_MAX_AGE_DAYS:
                continue
            if age_days > MAX_ITEM_AGE_DAYS:
                if not (
                    isinstance(ends_at, datetime) and _to_utc(ends_at) > now_utc
                ):
                    continue

        out.append(it)
    return out


def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate items by identity/guid and prefer later ends or longer descriptions."""

    def _key_for_item(it: Dict[str, Any]) -> str:
        if it.get("_identity"):
            return str(it.get("_identity"))
        if it.get("guid"):
            return str(it.get("guid"))
        raw = f"{it.get('source') or ''}|{it.get('title') or ''}|{it.get('description') or ''}"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        log.warning(
            "Item ohne guid/_identity – Fallback-Schlüssel (source|title|description) %s",
            key,
        )
        return key

    def _better(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Return True if ``a`` is better than ``b`` according to ends_at and description length."""

        def _end_value(it: Dict[str, Any]) -> datetime:
            ends = it.get("ends_at")
            if isinstance(ends, datetime):
                return _to_utc(ends)
            return datetime.min.replace(tzinfo=timezone.utc)

        a_end = _end_value(a)
        b_end = _end_value(b)
        if a_end > b_end:
            return True
        if a_end < b_end:
            return False
        a_len = len(a.get("description") or "")
        b_len = len(b.get("description") or "")
        return a_len > b_len

    seen: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for it in items:
        key = _key_for_item(it)
        if key in seen:
            idx = seen[key]
            if _better(it, out[idx]):
                out[idx] = it
        else:
            seen[key] = len(out)
            out.append(it)
    return out

def _sort_key(item: Dict[str, Any]) -> Tuple[int, float, str]:
    pd = item.get("pubDate")
    if isinstance(pd, datetime):
        return (0, -_to_utc(pd).timestamp(), item.get("guid", ""))
    return (1, 0.0, item.get("guid", ""))

def _emit_channel_header(now: datetime) -> List[str]:
    h = []
    h.append('<?xml version="1.0" encoding="UTF-8"?>')
    h.append(
        '<rss version="2.0" xmlns:ext="https://wien-oepnv.example/schema" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
    )
    h.append("<channel>")
    h.append(f"<title>{html.escape(FEED_TITLE)}</title>")
    h.append(f"<link>{html.escape(FEED_LINK)}</link>")
    h.append(f"<description>{html.escape(FEED_DESC)}</description>")
    h.append(f"<lastBuildDate>{_fmt_rfc2822(now)}</lastBuildDate>")
    h.append(f"<ttl>{FEED_TTL}</ttl>")
    return h

def _emit_item(it: Dict[str, Any], now: datetime, state: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    pubDate = _coerce_datetime_field(it, "pubDate")
    starts_at = _coerce_datetime_field(it, "starts_at")
    ends_at = _coerce_datetime_field(it, "ends_at")

    ident = _identity_for_item(it)
    st = state.get(ident)
    if not st:
        st = {"first_seen": _to_utc(now).isoformat()}
        state[ident] = st

    try:
        fs_dt = datetime.fromisoformat(st["first_seen"])
    except Exception:
        log.warning("first_seen Parsefehler: %r – fallback to now", st.get("first_seen"))
        fs_dt = _to_utc(now)
        st["first_seen"] = fs_dt.isoformat()

    # Felder holen
    raw_title = it.get("title") or "Mitteilung"
    raw_desc  = it.get("description") or ""
    link      = it.get("link") or FEED_LINK
    guid      = it.get("guid") or ident
    if not isinstance(pubDate, datetime) and FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # TV-freundliche Kürzung (Beschreibung darf HTML enthalten)
    desc_clipped = _clip_text_html(raw_desc, DESCRIPTION_CHAR_LIMIT)
    # Für XML robust aufbereiten (CDATA schützt Sonderzeichen)
    title_out = _sanitize_text(html.unescape(raw_title))
    desc_lines_raw = desc_clipped.split("\n")

    date_range_line: Optional[str] = None
    for line in desc_lines_raw:
        match = DATE_RANGE_RE.match(line)
        if match:
            date_range_line = f"{match.group(1)} – {match.group(2)}"
            break

    removed_title_line: Optional[str] = None
    if desc_lines_raw and desc_lines_raw[0].lower() in title_out.lower():
        removed_title_line = desc_lines_raw[0]
        desc_lines_raw = desc_lines_raw[1:]
    filtered_lines = [
        line.strip()
        for line in desc_lines_raw
        if line.strip() and line.strip().lower() != "zeitraum:"
    ]

    extra_prefixes = ("linien:", "betroffene haltestellen:")
    extra_lines_raw: List[str] = []
    desc_lines: List[str] = []
    for line in filtered_lines:
        lower_line = line.lower()
        if any(lower_line.startswith(prefix) for prefix in extra_prefixes):
            extra_lines_raw.append(line)
        else:
            desc_lines.append(line)

    first_alpha_idx: Optional[int] = None
    fallback_candidates = desc_lines if desc_lines else extra_lines_raw
    if fallback_candidates:
        fallback_line = fallback_candidates[0]
    else:
        sanitized_removed = (
            _sanitize_text(removed_title_line) if removed_title_line else ""
        )
        fallback_line = sanitized_removed or title_out
    for idx, line in enumerate(desc_lines):
        if any(ch.isalpha() for ch in line):
            first_alpha_idx = idx
            fallback_line = line
            break

    desc_sentence = fallback_line
    if first_alpha_idx is not None:
        sentence_text = ""
        sentence_found = False
        for line in desc_lines[first_alpha_idx:]:
            part = line.strip()
            if not part:
                continue
            sentence_text = f"{sentence_text} {part}".strip() if sentence_text else part
            if _SENTENCE_END_RE.search(sentence_text):
                sentence_found = True
                break

        if sentence_found and sentence_text:
            desc_sentence = sentence_text

    desc_line = _sanitize_text(desc_sentence)
    title_out = re.sub(r"\s+", " ", title_out).strip()
    desc_line = re.sub(r"[ \t\r\f\v]+", " ", desc_line).strip()

    sanitized_extras: List[str] = []
    for extra_line in extra_lines_raw:
        sanitized = _sanitize_text(extra_line)
        sanitized = re.sub(r"[ \t\r\f\v]+", " ", sanitized).strip()
        if sanitized and sanitized != desc_line:
            sanitized_extras.append(sanitized)

    time_line = format_local_times(
        starts_at if isinstance(starts_at, datetime) else None,
        ends_at if isinstance(ends_at, datetime) else None,
    )
    normalized_time_line = (time_line or "").strip()
    normalized_time_line_first = (
        normalized_time_line.split(" ", 1)[0] if normalized_time_line else ""
    )
    if date_range_line and (
        not normalized_time_line
        or normalized_time_line_first in {"Seit", "Ab"}
    ):
        time_line = date_range_line
    time_line = _sanitize_text(time_line)
    time_line = re.sub(r"[ \t\r\f\v]+", " ", time_line).strip()
    if time_line:
        if not time_line.startswith("["):
            time_line = f"[{time_line}"
        if not time_line.endswith("]"):
            time_line = f"{time_line}]"

    desc_parts: List[str] = []
    if desc_line:
        desc_parts.append(desc_line)
    desc_parts.extend(sanitized_extras)
    if time_line:
        desc_parts.append(time_line)
    desc_out = "\n".join(desc_parts)
    desc_html = desc_out.replace("\n", "<br/>")
    desc_cdata = desc_out.replace("\n", "<br>")

    parts: List[str] = []
    parts.append("<item>")
    parts.append(f"<title>{_cdata(title_out)}</title>")
    parts.append(f"<link>{html.escape(link)}</link>")
    parts.append(f"<guid>{html.escape(guid)}</guid>")
    if isinstance(pubDate, datetime):
        parts.append(f"<pubDate>{_fmt_rfc2822(pubDate)}</pubDate>")

    parts.append(f"<ext:first_seen>{_fmt_rfc2822(fs_dt)}</ext:first_seen>")
    if isinstance(starts_at, datetime):
        parts.append(f"<ext:starts_at>{_fmt_rfc2822(starts_at)}</ext:starts_at>")
    if isinstance(ends_at, datetime):
        parts.append(f"<ext:ends_at>{_fmt_rfc2822(ends_at)}</ext:ends_at>")

    parts.append(f"<description>{_cdata(desc_cdata)}</description>")
    parts.append(f"<content:encoded>{_cdata(desc_html)}</content:encoded>")
    parts.append("</item>")
    return ident, "\n".join(parts)

def _make_rss(items: List[Dict[str, Any]], now: datetime, state: Dict[str, Dict[str, Any]]) -> str:
    out: List[str] = _emit_channel_header(now)

    body_parts: List[str] = []
    identities_in_feed: List[str] = []
    emitted = 0
    for it in items:
        if emitted >= MAX_ITEMS:
            break
        ident, xml_item = _emit_item(it, now, state)
        body_parts.append(xml_item)
        identities_in_feed.append(ident)
        emitted += 1

    out.extend(body_parts)
    out.append("</channel>")
    out.append("</rss>")

    # State nur für *aktuelle* Items speichern (kein Anwachsen). Ist der Feed
    # leer, speichern wir einen leeren State, um veraltete GUIDs zu entfernen.
    pruned = {k: state[k] for k in identities_in_feed if k in state} if identities_in_feed else {}
    try:
        _save_state(pruned)
    except Exception as e:
        log.warning(
            "State speichern fehlgeschlagen (%s) – Feed wird trotzdem zurückgegeben.",
            e,
        )

    return "\n".join(out)

def main() -> int:
    statuses = _provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    job_start = perf_counter()
    now = datetime.now(timezone.utc)
    state = _load_state()

    try:
        collect_start = perf_counter()
        items = _invoke_collect_items(report)
        collect_duration = perf_counter() - collect_start
        raw_count = len(items)
        log.info(
            "Provider-Abfrage abgeschlossen: %d Items in %.2fs",
            raw_count,
            collect_duration,
        )

        normalize_start = perf_counter()
        _normalize_item_datetimes(items)
        normalize_duration = perf_counter() - normalize_start
        log.debug("Zeitstempel normalisiert in %.2fs", normalize_duration)

        filter_start = perf_counter()
        items = _drop_old_items(items, now, state)
        filter_duration = perf_counter() - filter_start
        log.info(
            "Altersfilter angewendet: %d Items nach %.2fs (vorher: %d)",
            len(items),
            filter_duration,
            raw_count,
        )

        dedupe_start = perf_counter()
        deduped = _dedupe_items(items)
        dedupe_duration = perf_counter() - dedupe_start
        log.info(
            "Duplikate entfernt: %d eindeutige Items nach %.2fs (vorher: %d)",
            len(deduped),
            dedupe_duration,
            len(items),
        )
        items = deduped
        if not items:
            log.warning("Keine Items gesammelt.")
            items = []
        else:
            log.debug("Sortiere %d Items nach Priorität.", len(items))
        items.sort(key=_sort_key)

        rss_start = perf_counter()
        rss = _make_rss(items, now, state)
        rss_duration = perf_counter() - rss_start

        out_path = _validate_path(Path(OUT_PATH), "OUT_PATH")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            f.write(rss)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(out_path)

        total_duration = perf_counter() - job_start
        log.info(
            "Feed geschrieben: %s (%d Items) in %.2fs (RSS-Erzeugung: %.2fs)",
            out_path,
            min(len(items), MAX_ITEMS),
            total_duration,
            rss_duration,
        )
        report.finish(
            build_successful=True,
            raw_items=raw_count,
            final_items=len(items),
            durations={
                "collect": collect_duration,
                "normalize": normalize_duration,
                "filter": filter_duration,
                "dedupe": dedupe_duration,
                "rss": rss_duration,
                "total": total_duration,
            },
            feed_path=out_path,
        )
        report.log_results()
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Feed-Bau fehlgeschlagen: %s", exc)
        report.record_exception(exc)
        report.finish(build_successful=False)
        report.log_results()
        raise

if __name__ == "__main__":
    sys.exit(main())
