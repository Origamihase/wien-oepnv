#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import html
import logging
from logging.handlers import RotatingFileHandler
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

try:  # pragma: no cover - allow running as package and as script
    from utils.cache import read_cache
    from utils.env import get_int_env, get_bool_env
except ModuleNotFoundError:  # pragma: no cover
    from .utils.cache import read_cache  # type: ignore
    from .utils.env import get_int_env, get_bool_env  # type: ignore

# ---------------- Paths ----------------
_ALLOWED_ROOTS = {"docs", "data", "log"}


def _validate_path(path: Path, name: str) -> Path:
    """Ensure ``path`` stays within whitelisted directories."""

    try:
        rel = path.resolve().relative_to(Path.cwd().resolve())
    except Exception:
        raise ValueError(f"{name} outside allowed directories")
    if rel.parts and rel.parts[0] in _ALLOWED_ROOTS:
        return path
    raise ValueError(f"{name} outside allowed directories")

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_level = getattr(logging, LOG_LEVEL, logging.INFO)
if not isinstance(_level, int):
    _level = logging.INFO

_DEFAULT_LOG_DIR = Path("log")
_LOG_DIR_ENV = os.getenv("LOG_DIR")
if _LOG_DIR_ENV is None:
    LOG_DIR_PATH = _validate_path(_DEFAULT_LOG_DIR, "LOG_DIR")
else:
    try:
        LOG_DIR_PATH = _validate_path(Path(_LOG_DIR_ENV), "LOG_DIR")
    except ValueError:
        LOG_DIR_PATH = _validate_path(_DEFAULT_LOG_DIR, "LOG_DIR")
LOG_DIR = str(LOG_DIR_PATH)
LOG_MAX_BYTES = max(get_int_env("LOG_MAX_BYTES", 1_000_000), 0)
LOG_BACKUP_COUNT = max(get_int_env("LOG_BACKUP_COUNT", 5), 0)

os.makedirs(LOG_DIR, exist_ok=True)
fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(
    level=_level,
    format=fmt,
)
error_handler = RotatingFileHandler(
    Path(LOG_DIR) / "errors.log",
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter(fmt))
logging.getLogger().addHandler(error_handler)
log = logging.getLogger("build_feed")
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

# ---------------- ENV ----------------
OUT_PATH = os.getenv("OUT_PATH", "docs/feed.xml")
_validate_path(Path(OUT_PATH), "OUT_PATH")
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

STATE_FILE = Path(os.getenv("STATE_PATH", "data/first_seen.json"))  # nur Einträge aus *aktuellem* Feed
STATE_FILE = _validate_path(STATE_FILE, "STATE_PATH")
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

        for attempt in attempts:
            try:
                parsed = datetime.fromisoformat(attempt)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

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

def _load_state() -> Dict[str, Dict[str, Any]]:
    path = _validate_path(STATE_FILE, "STATE_PATH")
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("State laden fehlgeschlagen (%s) – starte leer.", e)
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for ident, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            raw_first_seen = entry.get("first_seen", "")
            fs_dt = datetime.fromisoformat(str(raw_first_seen))
            _to_utc(fs_dt)
        except Exception:
            log.warning(
                "State-Eintrag %s hat unparsebares first_seen: %r", ident, entry.get("first_seen")
            )
            continue
        out[ident] = entry
    return out

def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    path = _validate_path(STATE_FILE, "STATE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
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

def _collect_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    cache_fetchers: List[Any] = []
    network_fetchers: List[Any] = []
    for env, fetch in PROVIDERS:
        if not get_bool_env(env, True):
            continue
        if getattr(fetch, "_provider_cache_name", None):
            cache_fetchers.append(fetch)
        else:
            network_fetchers.append(fetch)

    if not cache_fetchers and not network_fetchers:
        return []

    def _merge_result(fetch: Any, result: Any) -> None:
        name = getattr(fetch, "__name__", str(fetch))
        if not isinstance(result, list):
            log.error("%s fetch gab keine Liste zurück: %r", name, result)
            return
        provider_name = getattr(fetch, "_provider_cache_name", None)
        if provider_name and not result:
            log.warning(
                "Cache für Provider '%s' leer – generiere Feed ohne aktuelle Daten.",
                provider_name,
            )
        _normalize_item_datetimes(result)
        items.extend(result)

    for fetch in cache_fetchers:
        name = getattr(fetch, "__name__", str(fetch))
        try:
            result = fetch()
        except Exception as exc:
            log.exception("%s fetch fehlgeschlagen: %s", name, exc)
            continue
        _merge_result(fetch, result)

    if not network_fetchers:
        return items

    futures: Dict[Any, Any] = {}
    # ThreadPoolExecutor erlaubt max_workers nicht als 0; daher mindestens 1
    executor = ThreadPoolExecutor(max_workers=max(1, len(network_fetchers)))
    timed_out = False
    try:
        for fetch in network_fetchers:
            futures[executor.submit(fetch)] = fetch
        try:
            for future in as_completed(futures, timeout=PROVIDER_TIMEOUT):
                fetch = futures[future]
                name = getattr(fetch, "__name__", str(fetch))
                try:
                    result = future.result()
                except TimeoutError:
                    log.warning("%s fetch Timeout nach %ss", name, PROVIDER_TIMEOUT)
                except Exception as exc:
                    log.exception("%s fetch fehlgeschlagen: %s", name, exc)
                else:
                    _merge_result(fetch, result)
        except TimeoutError:
            timed_out = True
            log.warning("Provider-Timeout nach %ss", PROVIDER_TIMEOUT)
            executor.shutdown(wait=False, cancel_futures=True)
    finally:
        if not timed_out:
            executor.shutdown(wait=True)

    return items


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
    now = datetime.now(timezone.utc)
    state = _load_state()
    items = _collect_items()
    _normalize_item_datetimes(items)
    items = _drop_old_items(items, now, state)
    items = _dedupe_items(items)
    if not items:
        log.warning("Keine Items gesammelt.")
        items = []
    items.sort(key=_sort_key)
    rss = _make_rss(items, now, state)

    out_path = _validate_path(Path(OUT_PATH), "OUT_PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(rss)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(out_path)
    log.info("Feed geschrieben: %s (%d Items)", out_path, min(len(items), MAX_ITEMS))
    return 0

if __name__ == "__main__":
    sys.exit(main())
