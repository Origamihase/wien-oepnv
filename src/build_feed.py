#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import inspect
import json, os, sys, html, logging, re, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

# Provider-Imports (import lazily/defensively to support testing)
try:  # pragma: no cover
    from providers.wiener_linien import fetch_events as wl_fetch
except ModuleNotFoundError:  # pragma: no cover
    wl_fetch = lambda: []  # type: ignore

try:  # pragma: no cover
    from providers.oebb import fetch_events as oebb_fetch
except ModuleNotFoundError:  # pragma: no cover
    oebb_fetch = lambda: []  # type: ignore

try:  # pragma: no cover
    from providers.vor import fetch_events as vor_fetch
except ModuleNotFoundError:  # pragma: no cover
    vor_fetch = lambda: []  # type: ignore

# Mapping of environment variables to provider fetch functions
PROVIDERS: List[Tuple[str, Any]] = [
    ("WL_ENABLE", wl_fetch),
    ("OEBB_ENABLE", oebb_fetch),
    ("VOR_ENABLE", vor_fetch),
]

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_level = getattr(logging, LOG_LEVEL, logging.INFO)
if not isinstance(_level, int):
    _level = logging.INFO

logging.basicConfig(
    level=_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("build_feed")

# ---------------- Helpers: ENV ----------------

def _get_int_env(name: str, default: int) -> int:
    """Read integer environment variables safely.

    Returns the provided default if the variable is unset or cannot be
    converted to ``int``. On invalid values, a warning is logged.
    """

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError) as e:
        log.warning(
            "Ungültiger Wert für %s=%r – verwende Default %d (%s: %s)",
            name,
            raw,
            default,
            type(e).__name__,
            e,
        )
        return default

# ---------------- ENV ----------------
OUT_PATH = os.getenv("OUT_PATH", "docs/feed.xml")
FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK = os.getenv("FEED_LINK", "https://github.com/Origamihase/wien-oepnv")
FEED_DESC = os.getenv("FEED_DESC", "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")
FEED_TTL = max(_get_int_env("FEED_TTL", 30), 0)

DESCRIPTION_CHAR_LIMIT = max(_get_int_env("DESCRIPTION_CHAR_LIMIT", 170), 0)
FRESH_PUBDATE_WINDOW_MIN = _get_int_env("FRESH_PUBDATE_WINDOW_MIN", 5)
MAX_ITEMS = max(_get_int_env("MAX_ITEMS", 60), 0)
MAX_ITEM_AGE_DAYS = max(_get_int_env("MAX_ITEM_AGE_DAYS", 45), 0)
ABSOLUTE_MAX_AGE_DAYS = max(_get_int_env("ABSOLUTE_MAX_AGE_DAYS", 365), 0)
ENDS_AT_GRACE_MINUTES = max(_get_int_env("ENDS_AT_GRACE_MINUTES", 10), 0)
PROVIDER_TIMEOUT = max(_get_int_env("PROVIDER_TIMEOUT", 25), 0)

STATE_FILE = Path(os.getenv("STATE_PATH", "data/first_seen.json"))  # nur Einträge aus *aktuellem* Feed
STATE_RETENTION_DAYS = max(_get_int_env("STATE_RETENTION_DAYS", 60), 0)

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
        return _to_utc(dt).strftime(RFC)

# Entfernt XML-unerlaubte Kontrollzeichen (außer \t, \n, \r)
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)

def _sanitize_text(s: str) -> str:
    return _CONTROL_RE.sub("", s or "")

def _cdata(s: str) -> str:
    # CDATA sicher splitten, falls ']]>' im Text vorkommt
    s = s.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{s}]]>"

def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s or "")

def _clip_text_html(text: str, limit: int) -> str:
    """Für TV knapper machen. Wenn HTML enthalten ist, clippen wir auf Plaintext und escapen."""
    if limit <= 0:
        return text or ""
    plain = _strip_html(text)
    if len(plain) <= limit:
        return text or ""
    clipped = plain[:limit].rstrip() + " …"
    return html.escape(clipped)

def _parse_lines_from_title(title: str) -> List[str]:
    m = re.match(r"^\s*([A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20})\s*:\s*", title or "")
    if not m:
        return []
    return m.group(1).split("/")

def _ymd_or_none(dt: Optional[datetime]) -> str:
    if isinstance(dt, datetime):
        return _to_utc(dt).date().isoformat()
    return "None"

# ---------------- State (first_seen) ----------------

def _load_state() -> Dict[str, Dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("State laden fehlgeschlagen (%s) – starte leer.", e)
        return {}

    threshold = datetime.now(timezone.utc) - timedelta(days=STATE_RETENTION_DAYS)
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        try:
            fs_dt = datetime.fromisoformat(v.get("first_seen", ""))
            fs_dt = _to_utc(fs_dt)
            if fs_dt >= threshold:
                out[k] = v
        except Exception:
            continue
    return out

def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    threshold = datetime.now(timezone.utc) - timedelta(days=STATE_RETENTION_DAYS)
    pruned: Dict[str, Dict[str, Any]] = {}
    for k, v in state.items():
        try:
            fs_dt = datetime.fromisoformat(v.get("first_seen", ""))
            fs_dt = _to_utc(fs_dt)
            if fs_dt >= threshold:
                pruned[k] = v
        except Exception:
            continue
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(STATE_FILE)

def _identity_for_item(item: Dict[str, Any]) -> str:
    """
    Stabile Identität unabhängig von Titel-Kosmetik.
      - Wenn Provider _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link (vom RSS stabil).
      - WL/sonstige: Quelle|Kategorie|Linienpräfix + Start-YYYY-MM-DD.
    """
    if item.get("_identity"):
        return str(item["_identity"])

    source = (item.get("source") or "").lower()
    category = (item.get("category") or "").lower()
    if "öbb" in source or "oebb" in source:
        base = item.get("guid") or item.get("link") or item.get("title") or ""
        return f"oebb|{base}"

    lines = _parse_lines_from_title(item.get("title") or "")
    lines_part = "L=" + "/".join([l.upper() for l in lines]) if lines else "L="
    start_day = _ymd_or_none(item.get("starts_at"))
    return f"{source}|{category}|{lines_part}|D={start_day}"

# ---------------- Pipeline ----------------

def _collect_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    futures: Dict[Any, Any] = {}

    active = [
        f
        for env, f in PROVIDERS
        if os.getenv(env, "1").strip().lower() not in {"0", "false"}
    ]
    if not active:
        return []

    # ThreadPoolExecutor erlaubt max_workers nicht als 0; daher mindestens 1
    executor = ThreadPoolExecutor(max_workers=max(1, len(active)))
    try:
        for fetch in active:
            if "timeout" in inspect.signature(fetch).parameters:
                futures[executor.submit(fetch, timeout=PROVIDER_TIMEOUT)] = fetch
            else:
                futures[executor.submit(fetch)] = fetch
        try:
            for future in as_completed(futures, timeout=PROVIDER_TIMEOUT):
                fetch = futures[future]
                name = getattr(fetch, "__name__", str(fetch))
                try:
                    result = future.result()
                    if not isinstance(result, list):
                        log.error("%s fetch gab keine Liste zurück: %r", name, result)
                        continue
                    items += result
                except TimeoutError:
                    log.warning("%s fetch Timeout nach %ss", name, PROVIDER_TIMEOUT)
                except Exception as e:
                    log.exception("%s fetch fehlgeschlagen: %s", name, e)
        except TimeoutError:
            log.warning("Provider-Timeout nach %ss", PROVIDER_TIMEOUT)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return items


def _drop_old_items(items: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Entferne Items, die zu alt sind oder bereits beendet wurden."""
    out: List[Dict[str, Any]] = []
    for it in items:
        ends_at = it.get("ends_at")
        if isinstance(ends_at, datetime):
            if _to_utc(ends_at) < _to_utc(now) - timedelta(minutes=ENDS_AT_GRACE_MINUTES):
                continue

        dt = it.get("pubDate") or it.get("starts_at")
        if isinstance(dt, datetime):
            age_days = (_to_utc(now) - _to_utc(dt)).total_seconds() / 86400.0
            if age_days > ABSOLUTE_MAX_AGE_DAYS:
                continue
            if age_days > MAX_ITEM_AGE_DAYS:
                continue
        out.append(it)
    return out


def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Behalte nur das erste Item je Identität (oder guid)."""
    seen = set()
    out = []
    for it in items:
        key: Optional[str]
        if it.get("_identity"):
            key = it.get("_identity")
        elif it.get("guid"):
            key = it.get("guid")
        else:
            raw = f"{it.get('source') or ''}|{it.get('title') or ''}|{it.get('description') or ''}"
            key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
            log.warning(
                "Item ohne guid/_identity – Fallback-Schlüssel (source|title|description) %s",
                key,
            )
        if key in seen:
            continue
        seen.add(key)
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
    h.append('<rss version="2.0" xmlns:ext="https://wien-oepnv.example/schema">')
    h.append("<channel>")
    h.append(f"<title>{html.escape(FEED_TITLE)}</title>")
    h.append(f"<link>{html.escape(FEED_LINK)}</link>")
    h.append(f"<description>{html.escape(FEED_DESC)}</description>")
    h.append(f"<lastBuildDate>{_fmt_rfc2822(now)}</lastBuildDate>")
    h.append(f"<ttl>{FEED_TTL}</ttl>")
    return h

def _emit_item(it: Dict[str, Any], now: datetime, state: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
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
    pubDate   = it.get("pubDate")
    starts_at = it.get("starts_at")
    ends_at   = it.get("ends_at")

    if not isinstance(pubDate, datetime) and FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # TV-freundliche Kürzung (Beschreibung darf HTML enthalten)
    desc_out = _clip_text_html(raw_desc, DESCRIPTION_CHAR_LIMIT)
    # Für XML robust aufbereiten
    title_out = _sanitize_text(html.unescape(raw_title))
    desc_out  = _sanitize_text(html.unescape(desc_out))

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

    parts.append(f"<description>{_cdata(desc_out)}</description>")
    parts.append("</item>")
    return ident, "\n".join(parts)

def _make_rss(items: List[Dict[str, Any]], now: datetime, state: Dict[str, Dict[str, Any]]) -> str:
    out: List[str] = _emit_channel_header(now)

    body_parts: List[str] = []
    identities_in_feed: List[str] = []
    count = 0
    for it in items:
        ident, xml_item = _emit_item(it, now, state)
        body_parts.append(xml_item)
        identities_in_feed.append(ident)
        count += 1
        if count >= MAX_ITEMS:
            break

    out.extend(body_parts)
    out.append("</channel>")
    out.append("</rss>")

    # State nur für *aktuelle* Items speichern (kein Anwachsen)
    pruned = {k: state[k] for k in identities_in_feed if k in state}
    try:
        _save_state(pruned)
    except Exception as e:
        log.warning("State speichern fehlgeschlagen (%s) – Feed wird trotzdem zurückgegeben.", e)

    return "\n".join(out)

def main() -> int:
    now = datetime.now(timezone.utc)
    state = _load_state()
    items = _collect_items()
    items = _drop_old_items(items, now)
    items = _dedupe_items(items)
    if not items:
        log.warning("Keine Items gesammelt.")
        items = []
    items.sort(key=_sort_key)
    rss = _make_rss(items, now, state)

    out_path = Path(OUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rss, encoding="utf-8")
    log.info("Feed geschrieben: %s (%d Items)", out_path, min(len(items), MAX_ITEMS))
    return 0

if __name__ == "__main__":
    sys.exit(main())
