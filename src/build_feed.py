#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, os, sys, html, logging, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from email.utils import format_datetime

# Provider-Imports
from providers.wiener_linien import fetch_events as wl_fetch
from providers.oebb import fetch_events as oebb_fetch

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("build_feed")

# ---------------- ENV ----------------
OUT_PATH = os.getenv("OUT_PATH", "docs/feed.xml")
FEED_TITLE = os.getenv("FEED_TITLE", "ÖPNV Störungen Wien & Umgebung")
FEED_LINK = os.getenv("FEED_LINK", "https://github.com/Origamihase/wien-oepnv")
FEED_DESC = os.getenv("FEED_DESC", "Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen")

DESCRIPTION_CHAR_LIMIT = max(int(os.getenv("DESCRIPTION_CHAR_LIMIT", "170")), 0)
FRESH_PUBDATE_WINDOW_MIN = int(os.getenv("FRESH_PUBDATE_WINDOW_MIN", "5"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "60"))
ACTIVE_GRACE_MIN = int(os.getenv("ACTIVE_GRACE_MIN", "10"))

STATE_FILE = Path("data/first_seen.json")  # nur Einträge aus *aktuellem* Feed

RFC = "%a, %d %b %Y %H:%M:%S %z"

# ---------------- Helpers ----------------

def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

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
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("State laden fehlgeschlagen (%s) – starte leer.", e)
        return {}

def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
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
    try:
        items += wl_fetch()
    except Exception as e:
        log.exception("WL fetch fehlgeschlagen: %s", e)
    try:
        items += oebb_fetch()
    except Exception as e:
        log.exception("ÖBB fetch fehlgeschlagen: %s", e)
    return items

def _sort_key(item: Dict[str, Any]) -> Tuple[int, float, str]:
    pd = item.get("pubDate")
    if isinstance(pd, datetime):
        return (0, -_to_utc(pd).timestamp(), item.get("guid", ""))
    return (1, 0.0, item.get("guid", ""))

def _emit_channel_header(now: datetime) -> List[str]:
    h = []
    h.append('<?xml version="1.0" encoding="UTF-8"?>')
    h.append('<rss version="2.0">')
    h.append("<channel>")
    h.append(f"<title>{html.escape(FEED_TITLE)}</title>")
    h.append(f"<link>{html.escape(FEED_LINK)}</link>")
    h.append(f"<description>{html.escape(FEED_DESC)}</description>")
    h.append(f"<lastBuildDate>{_fmt_rfc2822(now)}</lastBuildDate>")
    h.append("<ttl>15</ttl>")
    return h

def _emit_item(it: Dict[str, Any], now: datetime, state: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    ident = _identity_for_item(it)
    st = state.get(ident)
    if not st:
        st = {"first_seen": _to_utc(now).isoformat()}
        state[ident] = st

    # Felder holen
    raw_title = it.get("title") or "Mitteilung"
    raw_desc  = it.get("description") or ""
    link      = it.get("link") or FEED_LINK
    guid      = it.get("guid") or ident
    pubDate   = it.get("pubDate")
    starts_at = it.get("starts_at")
    ends_at   = it.get("ends_at")

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

    try:
        fs_dt = datetime.fromisoformat(st["first_seen"])
    except Exception:
        log.warning("first_seen Parsefehler: %r – fallback to now", st.get("first_seen"))
        fs_dt = _to_utc(now)
        st["first_seen"] = fs_dt.isoformat()
    parts.append(f"<first_seen>{_fmt_rfc2822(fs_dt)}</first_seen>")
    if isinstance(starts_at, datetime):
        parts.append(f"<starts_at>{_fmt_rfc2822(starts_at)}</starts_at>")
    if isinstance(ends_at, datetime):
        parts.append(f"<ends_at>{_fmt_rfc2822(ends_at)}</ends_at>")

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
    _save_state(pruned)

    return "\n".join(out)

def main() -> int:
    now = datetime.now(timezone.utc)
    state = _load_state()
    items = _collect_items()
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
