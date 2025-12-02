from __future__ import annotations

import errno
import hashlib
import html
import inspect
import json
import logging
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    ThreadPoolExecutor,
    TimeoutError,
    wait,
)
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from threading import BoundedSemaphore
from time import perf_counter
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

try:  # pragma: no cover - allow running as script or module
    from feed import config as feed_config
    from feed.logging import configure_logging
    from feed.providers import (
        iter_providers,
        load_provider_plugins,
        provider_statuses,
        register_provider,
        resolve_provider_name,
    )
    from feed.reporting import (
        DuplicateSummary,
        FeedHealthMetrics,
        RunReport,
        clean_message,
        write_feed_health_report,
        write_feed_health_json,
    )
except ModuleNotFoundError:  # pragma: no cover
    from .feed import config as feed_config
    from .feed.logging import configure_logging
    from .feed.providers import (
        iter_providers,
        load_provider_plugins,
        provider_statuses,
        register_provider,
        resolve_provider_name,
    )
    from .feed.reporting import (
        DuplicateSummary,
        FeedHealthMetrics,
        RunReport,
        clean_message,
        write_feed_health_report,
        write_feed_health_json,
    )

try:  # pragma: no cover - allow running as script or package
    from utils.cache import (
        cache_modified_at,
        read_cache as _core_read_cache,
        register_cache_alert_hook,
    )  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    from .utils.cache import (
        cache_modified_at,
        read_cache as _core_read_cache,
        register_cache_alert_hook,
    )

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None  # type: ignore

try:  # pragma: no cover - platform dependent
    import msvcrt  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    msvcrt = None  # type: ignore

log = logging.getLogger("build_feed")


resolve_env_path = feed_config.resolve_env_path
validate_path = feed_config.validate_path
get_bool_env = feed_config.get_bool_env
LOG_TIMEZONE = feed_config.LOG_TIMEZONE


def refresh_from_env() -> None:
    """Refresh configuration values and reload provider plugins."""

    feed_config.refresh_from_env()
    load_provider_plugins(force=True)


refresh_from_env()

ABSOLUTE_MAX_AGE_DAYS = feed_config.ABSOLUTE_MAX_AGE_DAYS
DESCRIPTION_CHAR_LIMIT = feed_config.DESCRIPTION_CHAR_LIMIT
ENDS_AT_GRACE_MINUTES = feed_config.ENDS_AT_GRACE_MINUTES
FEED_DESC = feed_config.FEED_DESC
FEED_LINK = feed_config.FEED_LINK
FEED_TITLE = feed_config.FEED_TITLE
FEED_TTL = feed_config.FEED_TTL
FRESH_PUBDATE_WINDOW_MIN = feed_config.FRESH_PUBDATE_WINDOW_MIN
FEED_HEALTH_PATH = feed_config.FEED_HEALTH_PATH
FEED_HEALTH_JSON_PATH = feed_config.FEED_HEALTH_JSON_PATH
LOG_DIR_PATH = feed_config.LOG_DIR_PATH
LOG_DIR = LOG_DIR_PATH.as_posix()
LOG_MAX_BYTES = feed_config.LOG_MAX_BYTES
LOG_BACKUP_COUNT = feed_config.LOG_BACKUP_COUNT
MAX_ITEM_AGE_DAYS = feed_config.MAX_ITEM_AGE_DAYS
MAX_ITEMS = feed_config.MAX_ITEMS
OUT_PATH = feed_config.OUT_PATH
PROVIDER_MAX_WORKERS = feed_config.PROVIDER_MAX_WORKERS
PROVIDER_TIMEOUT = feed_config.PROVIDER_TIMEOUT
RFC = feed_config.RFC
STATE_FILE = feed_config.STATE_FILE
STATE_RETENTION_DAYS = feed_config.STATE_RETENTION_DAYS
CACHE_MAX_AGE_HOURS = feed_config.CACHE_MAX_AGE_HOURS

os.makedirs(LOG_DIR, exist_ok=True)

read_cache = _core_read_cache


def read_cache_wl() -> List[Any]:
    return list(read_cache("wl"))


def read_cache_oebb() -> List[Any]:
    return list(read_cache("oebb"))


def read_cache_vor() -> List[Any]:
    return list(read_cache("vor"))


def read_cache_baustellen() -> List[Any]:
    return list(read_cache("baustellen"))


DEFAULT_PROVIDERS: Tuple[Tuple[str, Any], ...] = (
    ("WL_ENABLE", read_cache_wl),
    ("OEBB_ENABLE", read_cache_oebb),
    ("VOR_ENABLE", read_cache_vor),
    ("BAUSTELLEN_ENABLE", read_cache_baustellen),
)

PROVIDERS: List[Tuple[str, Any]] = list(DEFAULT_PROVIDERS)

for env_name, loader in PROVIDERS:
    register_provider(env_name, loader, cache_key=resolve_provider_name(loader, env_name))


def _provider_display_name(fetch: Any, env: Optional[str] = None) -> str:
    return resolve_provider_name(fetch, env)


def _detect_stale_caches(report: RunReport, now: datetime) -> List[str]:
    """Record warnings for provider caches older than the configured threshold."""

    if CACHE_MAX_AGE_HOURS <= 0:
        return []

    threshold = timedelta(hours=CACHE_MAX_AGE_HOURS)
    stale_messages: List[str] = []

    for _, loader in PROVIDERS:
        cache_name = getattr(loader, "_provider_cache_name", None)
        if not cache_name:
            continue

        modified_at = cache_modified_at(str(cache_name))
        if modified_at is None:
            continue

        age = now - modified_at
        if age <= threshold:
            continue

        hours = age.total_seconds() / 3600
        message = (
            f"Cache {cache_name}: zuletzt vor {hours:.1f}h aktualisiert "
            f"(Schwelle {CACHE_MAX_AGE_HOURS}h)"
        )
        report.add_warning(message)
        stale_messages.append(message)

    return stale_messages


def _provider_statuses() -> List[Tuple[str, bool]]:
    return provider_statuses()


def _log_startup_summary(statuses: List[Tuple[str, bool]]) -> None:
    enabled = sorted(name for name, is_enabled in statuses if is_enabled)
    disabled = sorted(name for name, is_enabled in statuses if not is_enabled)

    enabled_display = ", ".join(enabled) if enabled else "keine"
    log.info(
        "Starte Feed-Bau: %s aktiv (Timeout global=%ss, MaxItems=%d, Worker=%s)",
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

# ---------------- Provider tuning ----------------

def _provider_env_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").upper()).strip("_")
    return slug or "PROVIDER"


def _read_optional_non_negative_int(env_name: str) -> Optional[int]:
    raw = os.getenv(env_name)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except (TypeError, ValueError) as exc:
        log.warning(
            "Ungültiger Wert für %s=%r – ignoriere Override (%s: %s)",
            env_name,
            raw,
            type(exc).__name__,
            exc,
        )
        return None
    if value < 0:
        log.warning("Negativer Wert für %s=%r – ignoriere Override", env_name, raw)
        return None
    return value


def _provider_timeout_override(
    fetch: Any, env: Optional[str], provider_name: str
) -> Optional[int]:
    candidates: List[str] = []
    custom_env = getattr(fetch, "_provider_timeout_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(provider_name)
    candidates.append(f"PROVIDER_TIMEOUT_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_TIMEOUT")
        candidates.append(f"PROVIDER_TIMEOUT_{base}")

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        value = _read_optional_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _provider_concurrency_key(fetch: Any, provider_name: str) -> str:
    key = getattr(fetch, "_provider_concurrency_key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    return provider_name


def _provider_worker_limit(
    fetch: Any, env: Optional[str], provider_name: str, concurrency_key: str
) -> Optional[int]:
    candidates: List[str] = []
    custom_env = getattr(fetch, "_provider_max_workers_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(concurrency_key)
    candidates.append(f"PROVIDER_MAX_WORKERS_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_MAX_WORKERS")

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        value = _read_optional_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _fetch_supports_timeout(fetch: Any) -> bool:
    try:
        signature = inspect.signature(fetch)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "timeout":
            return True
    return False


def _call_fetch_with_timeout(
    fetch: Any, timeout: Optional[int], supports_timeout: bool
) -> Any:
    if supports_timeout:
        try:
            return fetch(timeout=None if timeout is None else timeout)
        except TypeError:
            return fetch()
    return fetch()

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
        for field_name in fields:
            _coerce_datetime_field(item, field_name)
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
        report = RunReport(provider_statuses())
    items: List[Dict[str, Any]] = []

    cache_alerts: defaultdict[str, List[str]] = defaultdict(list)
    seen_cache_alerts: set[Tuple[str, str]] = set()

    def _cache_alert_handler(provider_key: str, message: str) -> None:
        normalized_key = str(provider_key or "").strip()
        normalized_message = clean_message(message)
        if not normalized_key or not normalized_message:
            return
        cache_alerts[normalized_key].append(normalized_message)
        if report is not None:
            key = (normalized_key, normalized_message)
            if key not in seen_cache_alerts:
                seen_cache_alerts.add(key)
                report.add_warning(f"Cache {normalized_key}: {normalized_message}")

    unregister_cache_alert = register_cache_alert_hook(_cache_alert_handler)
    try:
        cache_fetchers: List[Any] = []
        network_fetchers: List[Any] = []
        provider_names: Dict[Any, str] = {}
        provider_envs: Dict[Any, Optional[str]] = {}

        provider_entries = list(PROVIDERS)
        providers_overridden = tuple(PROVIDERS) != DEFAULT_PROVIDERS
        if provider_entries:
            if not providers_overridden:
                known_envs = {env for env, _ in provider_entries}
                for spec in iter_providers():
                    if spec.env_var not in known_envs:
                        provider_entries.append((spec.env_var, spec.loader))
        else:
            provider_entries = [(spec.env_var, spec.loader) for spec in iter_providers()]

        for env, fetch in provider_entries:
            provider_name = _provider_display_name(fetch, env)
            enabled = bool(get_bool_env(env, True))
            fetch_type = "cache" if getattr(fetch, "_provider_cache_name", None) else "network"
            report.register_provider(provider_name, enabled, fetch_type)
            if not enabled:
                continue
            provider_names[fetch] = provider_name
            provider_envs[fetch] = env
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
                detail = "Keine aktuellen Daten"
                cache_name = getattr(fetch, "_provider_cache_name", None)
                if cache_name is not None:
                    alerts = cache_alerts.get(str(cache_name), [])
                    if alerts:
                        unique_alerts = list(dict.fromkeys(alerts))
                        detail = "; ".join(unique_alerts)
                report.provider_success(
                    provider_name,
                    items=count,
                    status="empty",
                    detail=detail,
                )
                if detail:
                    report.add_warning(f"Provider {provider_name}: {detail}")
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

        desired_workers = len(network_fetchers)
        if PROVIDER_MAX_WORKERS > 0:
            if desired_workers > PROVIDER_MAX_WORKERS:
                log.debug(
                    "Begrenze Provider-Threads von %s auf %s",
                    desired_workers,
                    PROVIDER_MAX_WORKERS,
                )
            desired_workers = min(desired_workers, PROVIDER_MAX_WORKERS)
        executor = ThreadPoolExecutor(max_workers=max(1, desired_workers))

        futures: Dict[Any, Tuple[Any, str, int]] = {}
        deadlines: Dict[Any, Optional[float]] = {}
        pending: set[Any] = set()
        semaphores: Dict[str, BoundedSemaphore] = {}
        timed_out = False

        try:
            for fetch in network_fetchers:
                provider_name = provider_names.get(fetch, _provider_display_name(fetch))
                env_name = provider_envs.get(fetch)
                timeout_override = _provider_timeout_override(fetch, env_name, provider_name)
                effective_timeout = (
                    timeout_override if timeout_override is not None else PROVIDER_TIMEOUT
                )
                concurrency_key = _provider_concurrency_key(fetch, provider_name)
                worker_limit = _provider_worker_limit(
                    fetch, env_name, provider_name, concurrency_key
                )
                semaphore: Optional[BoundedSemaphore] = None
                if worker_limit is not None and worker_limit > 0:
                    semaphore = semaphores.get(concurrency_key)
                    if semaphore is None:
                        semaphore = BoundedSemaphore(worker_limit)
                        semaphores[concurrency_key] = semaphore
                if timeout_override is not None:
                    log.debug(
                        "Provider %s nutzt Timeout-Override von %ss",
                        provider_name,
                        timeout_override,
                    )
                if worker_limit is not None and worker_limit > 0:
                    log.debug(
                        "Provider %s begrenzt Worker auf %s (Schlüssel %s)",
                        provider_name,
                        worker_limit,
                        concurrency_key,
                    )
                supports_timeout = _fetch_supports_timeout(fetch)

                def _run_fetch(
                    fetch: Any = fetch,
                    timeout_value: int = effective_timeout,
                    supports: bool = supports_timeout,
                    semaphore: Optional[BoundedSemaphore] = semaphore,
                ) -> Any:
                    timeout_arg = timeout_value if timeout_value > 0 else None
                    if semaphore is None:
                        return _call_fetch_with_timeout(fetch, timeout_arg, supports)
                    with semaphore:
                        return _call_fetch_with_timeout(fetch, timeout_arg, supports)

                report.provider_started(provider_name)
                future = executor.submit(_run_fetch)
                futures[future] = (fetch, provider_name, effective_timeout)
                pending.add(future)
                start_time = perf_counter()
                if effective_timeout > 0:
                    deadlines[future] = start_time + effective_timeout
                elif effective_timeout == 0:
                    deadlines[future] = start_time
                else:
                    deadlines[future] = None

            while pending:
                now = perf_counter()
                expired = []
                for future in list(pending):
                    deadline = deadlines.get(future)
                    if deadline is not None and now >= deadline:
                        expired.append(future)
                for future in expired:
                    pending.discard(future)
                    fetch, provider_name, timeout_value = futures[future]
                    name = getattr(fetch, "__name__", str(fetch))
                    log.error("%s fetch Timeout nach %ss", name, timeout_value)
                    report.provider_error(
                        provider_name,
                        f"Timeout nach {timeout_value}s",
                    )
                    future.cancel()
                    timed_out = True

                if not pending:
                    break

                wait_timeout: Optional[float] = None
                remaining = []
                for fut in pending:
                    deadline = deadlines.get(fut)
                    if deadline is not None:
                        remaining.append(deadline - now)
                if remaining:
                    wait_timeout = max(min(remaining), 0.0)

                done, _ = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    pending.discard(future)
                    fetch, provider_name, timeout_value = futures[future]
                    name = getattr(fetch, "__name__", str(fetch))
                    try:
                        result = future.result()
                    except TimeoutError as exc:
                        log.error("%s fetch Timeout: %s", name, exc)
                        report.provider_error(provider_name, f"Timeout: {exc}")
                        timed_out = True
                    except CancelledError:
                        report.provider_error(provider_name, "Fetch abgebrochen")
                        timed_out = True
                    except Exception as exc:
                        log.exception("%s fetch fehlgeschlagen: %s", name, exc)
                        report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
                    else:
                        _merge_result(fetch, result, provider_name)
        finally:
            if timed_out:
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        return items
    finally:
        unregister_cache_alert()


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


def _dedupe_key_for_item(
    it: Dict[str, Any], *, warn_on_missing: bool = True
) -> Tuple[str, bool]:
    """Return the deduplication key used for ``it`` and indicate fallback usage."""

    if it.get("_identity"):
        return str(it.get("_identity")), False
    if it.get("guid"):
        return str(it.get("guid")), False
    raw = (
        f"{it.get('source') or ''}|{it.get('title') or ''}|{it.get('description') or ''}"
    )
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    if warn_on_missing:
        log.warning(
            "Item ohne guid/_identity – Fallback-Schlüssel (source|title|description) %s",
            key,
        )
    return key, True


def _summarize_duplicates(items: Sequence[Dict[str, Any]]) -> List[DuplicateSummary]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        key, _ = _dedupe_key_for_item(it, warn_on_missing=False)
        groups.setdefault(key, []).append(it)

    summaries: List[DuplicateSummary] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        titles = tuple(str(entry.get("title") or "") for entry in group[:3])
        summaries.append(
            DuplicateSummary(dedupe_key=key, count=len(group), titles=titles)
        )
    summaries.sort(key=lambda summary: summary.count, reverse=True)
    return summaries


def _count_new_items(
    items: Sequence[Dict[str, Any]],
    state: Dict[str, Dict[str, Any]],
) -> int:
    existing = set(state.keys()) if isinstance(state, dict) else set()
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        ident = _identity_for_item(it)
        if ident not in existing:
            count += 1
    return count


def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate items by identity/guid and prefer later ends or longer descriptions."""

    def _recency_value(it: Dict[str, Any]) -> datetime:
        """Return a comparable timestamp describing how recent ``it`` is."""

        candidates: List[datetime] = []
        for field_name in ("pubDate", "first_seen", "starts_at"):
            value = it.get(field_name)
            if isinstance(value, datetime):
                candidates.append(_to_utc(value))
            else:
                parsed = _parse_datetime(value)
                if isinstance(parsed, datetime):
                    candidates.append(_to_utc(parsed))

        if candidates:
            return max(candidates)

        return datetime.min.replace(tzinfo=timezone.utc)

    def _end_value(it: Dict[str, Any]) -> datetime:
        ends = it.get("ends_at")
        if isinstance(ends, datetime):
            return _to_utc(ends)
        return datetime.min.replace(tzinfo=timezone.utc)

    def _better(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Return True if ``a`` is better than ``b`` according to recency and content."""

        a_end = _end_value(a)
        b_end = _end_value(b)
        if a_end > b_end:
            return True
        if a_end < b_end:
            if _recency_value(a) > _recency_value(b):
                return True
            return False

        a_len = len(a.get("description") or "")
        b_len = len(b.get("description") or "")
        if a_len > b_len:
            return True
        if a_len < b_len:
            return _recency_value(a) > _recency_value(b)

        return _recency_value(a) > _recency_value(b)

    seen: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for it in items:
        key, _ = _dedupe_key_for_item(it)
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

def _build_canonical_link(candidate: Any, ident: str) -> str:
    """Return a canonical link for ``ident`` with a stable fallback anchor."""

    if isinstance(candidate, str):
        normalized = candidate.strip()
        if normalized:
            return normalized

    slug_source = ident or ""
    slug = quote(slug_source, safe="")
    base = (FEED_LINK or "").strip()
    if not base:
        return f"#meldung-{slug}" if slug else ""

    anchor_prefix = "meldung"
    base = base.rstrip("/")
    if slug:
        return f"{base}/#{anchor_prefix}-{slug}"
    return base


def _guid_attributes(guid: str, link: str) -> str:
    """Return attributes for the GUID tag based on permalink heuristics."""

    parsed = urlparse(guid)
    if parsed.scheme and parsed.netloc and guid == link:
        return ""
    return ' isPermaLink="false"'


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
    link = _build_canonical_link(it.get("link"), ident)
    if not link:
        link = FEED_LINK

    raw_guid = it.get("guid") or ident
    guid = str(raw_guid).strip() if raw_guid is not None else ident
    if not guid:
        guid = ident
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
    guid_attrs = _guid_attributes(guid, link)
    parts.append(f"<guid{guid_attrs}>{html.escape(guid)}</guid>")
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


def lint() -> int:
    """Run structural checks on the aggregated feed items without writing RSS."""

    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    now = datetime.now(timezone.utc)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    exit_code = 0

    try:
        items = _invoke_collect_items(report)
        raw_count = len(items)
        _normalize_item_datetimes(items)

        filtered_items = _drop_old_items(items, now, state)
        filtered_count = len(filtered_items)
        duplicate_summaries = _summarize_duplicates(filtered_items)
        duplicates_removed = sum(summary.count - 1 for summary in duplicate_summaries)

        deduped_items = _dedupe_items(list(filtered_items))
        deduped_count = len(deduped_items)
        new_items_count = _count_new_items(deduped_items, state)
        missing_guid_items = [it for it in filtered_items if not it.get("guid")]

        metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

        print("Feed-Lint Bericht")
        print("==================")
        print(f"Rohdaten: {metrics.raw_items}")
        print(f"Nach Altersfilter: {metrics.filtered_items}")
        print(
            f"Nach Deduplizierung: {metrics.deduped_items} "
            f"(entfernte Duplikate: {metrics.duplicate_count})"
        )
        print(f"Neue Items (vs. State): {metrics.new_items}")

        if duplicate_summaries:
            print("\nErkannte Duplikat-Gruppen:")
            for summary in duplicate_summaries:
                titles = ", ".join(
                    title or "<ohne Titel>" for title in summary.titles if title is not None
                )
                titles = titles or "<keine Beispiele>"
                print(
                    f"- {summary.count}× Schlüssel {summary.dedupe_key}: {titles}"
                )

        if stale_cache_messages:
            print("\nVeraltete Cache-Dateien:")
            for message in stale_cache_messages:
                print(f"- {message}")

        if missing_guid_items:
            print("\nEinträge ohne GUID:")
            for item in missing_guid_items:
                source = item.get("source") or "unbekannt"
                title = item.get("title") or "<ohne Titel>"
                print(f"- {source}: {title}")

        provider_failures = report.has_errors()
        if provider_failures:
            print("\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.")

        if not duplicate_summaries and not missing_guid_items and not provider_failures:
            print("\nKeine strukturellen Probleme gefunden.")

        if provider_failures:
            exit_code = 2
        elif duplicate_summaries or missing_guid_items or stale_cache_messages:
            exit_code = 1
        else:
            exit_code = 0

        report.finish(
            build_successful=exit_code == 0,
            raw_items=metrics.raw_items,
            final_items=metrics.deduped_items,
        )
        return exit_code
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Feed-Lint fehlgeschlagen: %s", exc)
        report.record_exception(exc)
        report.finish(build_successful=False)
        return 2
    finally:
        report.log_results()


def main() -> int:
    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    job_start = perf_counter()
    now = datetime.now(timezone.utc)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    health_metrics: Optional[FeedHealthMetrics] = None
    duplicate_summaries: List[DuplicateSummary] = []
    raw_count = 0
    filtered_count = 0
    deduped_count = 0
    duplicates_removed = 0
    new_items_count = 0
    items: List[Dict[str, Any]] = []
    health_path = _validate_path(Path(FEED_HEALTH_PATH), "FEED_HEALTH_PATH")
    health_json_path = _validate_path(
        Path(FEED_HEALTH_JSON_PATH), "FEED_HEALTH_JSON_PATH"
    )

    def _write_health_outputs(active_metrics: FeedHealthMetrics) -> None:
        try:
            write_feed_health_report(
                report, active_metrics, output_path=health_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "Feed-Health-Markdown konnte nicht geschrieben werden: %s",
                exc,
            )
        try:
            write_feed_health_json(
                report, active_metrics, output_path=health_json_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "Feed-Health-JSON konnte nicht geschrieben werden: %s",
                exc,
            )

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
        filtered_count = len(items)
        log.info(
            "Altersfilter angewendet: %d Items nach %.2fs (vorher: %d)",
            len(items),
            filter_duration,
            raw_count,
        )

        pre_dedupe_items = list(items)
        duplicate_summaries = _summarize_duplicates(pre_dedupe_items)

        dedupe_start = perf_counter()
        deduped = _dedupe_items(items)
        dedupe_duration = perf_counter() - dedupe_start
        pre_dedupe_count = len(pre_dedupe_items)
        log.info(
            "Duplikate entfernt: %d eindeutige Items nach %.2fs (vorher: %d)",
            len(deduped),
            dedupe_duration,
            pre_dedupe_count,
        )
        items = deduped
        deduped_count = len(items)
        duplicates_removed = sum(summary.count - 1 for summary in duplicate_summaries)
        if not items:
            log.warning("Keine Items gesammelt.")
            items = []
        else:
            log.debug("Sortiere %d Items nach Priorität.", len(items))
        items.sort(key=_sort_key)

        new_items_count = _count_new_items(items, state)

        health_metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

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
        if health_metrics is None:
            fallback_deduped = deduped_count or filtered_count or raw_count
            health_metrics = FeedHealthMetrics(
                raw_items=raw_count,
                filtered_items=filtered_count or raw_count,
                deduped_items=fallback_deduped,
                new_items=new_items_count,
                duplicate_count=duplicates_removed,
                duplicates=tuple(duplicate_summaries),
            )
        _write_health_outputs(health_metrics)
        report.log_results()
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Feed-Bau fehlgeschlagen: %s", exc)
        report.record_exception(exc)
        if health_metrics is None:
            fallback_deduped = deduped_count or filtered_count or raw_count
            health_metrics = FeedHealthMetrics(
                raw_items=raw_count,
                filtered_items=filtered_count or raw_count,
                deduped_items=fallback_deduped,
                new_items=new_items_count,
                duplicate_count=duplicates_removed,
                duplicates=tuple(duplicate_summaries),
            )
        report.finish(build_successful=False)
        _write_health_outputs(health_metrics)
        report.log_results()
        raise

_resolve_env_path = resolve_env_path
# Backwards compatibility for tests and external imports
_validate_path = validate_path

if __name__ == "__main__":
    sys.exit(main())
