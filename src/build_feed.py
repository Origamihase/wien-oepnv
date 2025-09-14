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

# **State nur für aktuelle Items** (wächst nicht an)
STATE_FILE = Path("data/first_seen.json")

# ---------------- Utils ----------------

RFC = "%a, %d %b %Y %H:%M:%S %z"

def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _fmt_rfc2822(dt: datetime) -> str:
    try:
        return format_datetime(_to_utc(dt))
    except Exception:
        # Fallback
        return dt.strftime(RFC)

def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s or "")

def _clip_text_html(text: str, limit: int) -> str:
    if limit <= 0:
        return text or ""
    plain = _strip_html(text)
    if len(plain) <= limit:
        return text
    clipped = plain[:limit].rstrip() + " …"
    return html.escape(clipped)

def _parse_lines_from_title(title: str) -> List[str]:
    """Erkennt ein führendes L1/L2/...-Präfix und gibt die Linien in Displayform zurück."""
    m = re.match(r"^\s*([A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20})\s*:\s*", title or "")
    if not m:
        return []
    return m.group(1).split("/")

def _ymd_or_none(dt: Optional[datetime]) -> str:
    if isinstance(dt, datetime):
        dt = _to_utc(dt)
        return dt.date().isoformat()
    return "None"

# ---------------- First-seen State ----------------

def _load_state() -> Dict[str, Dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
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
      - Wenn Provider bereits _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link nutzen (vom RSS stabil).
      - WL/sonstige: aus Quelle|Kategorie|Linien (aus Titelpräfix) + Start-YYYY-MM-DD.
    """
    if "_identity" in item and item["_identity"]:
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

# ---------------- Feed assembly ----------------

def _collect_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    # Reihenfolge der Quellen ist egal – wir sortieren später
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
    # 0 = hat pubDate (neuere zuerst), 1 = ohne pubDate (stabil via guid)
    pd = item.get("pubDate")
    if isinstance(pd, datetime):
        return (0, -_to_utc(pd).timestamp(), item.get("guid", ""))
    return (1, 0.0, item.get("guid", ""))

def _make_rss(items: List[Dict[str, Any]], now: datetime, state: Dict[str, Dict[str, Any]]) -> str:
    # Header
    out: List[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0">')
    out.append("<channel>")
    out.append(f"<title>{html.escape(FEED_TITLE)}</title>")
    out.append(f"<link>{html.escape(FEED_LINK)}</link>")
    out.append(f"<description>{html.escape(FEED_DESC)}</description>")
    out.append(f"<lastBuildDate>{_fmt_rfc2822(now)}</lastBuildDate>")
    out.append("<ttl>15</ttl>")

    # Body
    count = 0
    for it in items:
        # Identity (stabil) & first_seen aus State pflegen
        ident = _identity_for_item(it)
        st = state.get(ident)
        if not st:
            st = {"first_seen": _to_utc(now).isoformat()}
            state[ident] = st

        # Felder
        title = it.get("title") or "Mitteilung"
        link = it.get("link") or FEED_LINK
        guid = it.get("guid") or ident
        pubDate = it.get("pubDate")
        starts_at = it.get("starts_at")
        ends_at = it.get("ends_at")
        description = it.get("description") or ""

        # Beschreibung einkürzen für TV (optional)
        if DESCRIPTION_CHAR_LIMIT:
            description = _clip_text_html(description, DESCRIPTION_CHAR_LIMIT)

        out.append("<item>")
        out.append(f"<title>{title}</title>")
        out.append(f"<link>{html.escape(link)}</link>")
        out.append(f"<guid>{html.escape(guid)}</guid>")

        # pubDate nur, wenn aus Quelle vorhanden
        if isinstance(pubDate, datetime):
            out.append(f"<pubDate>{_fmt_rfc2822(pubDate)}</pubDate>")

        # Metadaten (TV unsichtbar)
        fs_dt = datetime.fromisoformat(st["first_seen"])
        out.append(f"<first_seen>{_fmt_rfc2822(fs_dt)}</first_seen>")
        if isinstance(starts_at, datetime):
            out.append(f"<starts_at>{_fmt_rfc2822(starts_at)}</starts_at>")
        if isinstance(ends_at, datetime):
            out.append(f"<ends_at>{_fmt_rfc2822(ends_at)}</ends_at>")

        out.append(f"<description>{description}</description>")
        out.append("</item>")

        count += 1
        if count >= MAX_ITEMS:
            break

    out.append("</channel>")
    out.append("</rss>")
    return "\n".join(out)

def main() -> int:
    now = datetime.now(timezone.utc)

    # 1) laden
    state = _load_state()

    # 2) Items holen
    items = _collect_items()
    if not items:
        log.warning("Keine Items gesammelt.")
        items = []

    # 3) sortieren & begrenzen
    items.sort(key=_sort_key)

    # 4) Feed bauen
    rss = _make_rss(items, now, state)

    # 5) State nur für *aktuell im Feed* enthaltene Identities speichern
    current_identities = set(_identity_for_item(it) for it in items[:MAX_ITEMS])
    pruned_state = {k: v for k, v in state.items() if k in current_identities}
    _save_state(pruned_state)

    # 6) Schreiben
    out_path = Path(OUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rss, encoding="utf-8")
    log.info("Feed geschrieben: %s (%d Items)", out_path, min(len(items), MAX_ITEMS))
    return 0

if __name__ == "__main__":
    sys.exit(main())
