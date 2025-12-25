#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.

- Secret OEBB_RSS_URL (Fallback: offizielle ÖBB-RSS-URL)
- Titel-Kosmetik:
  • Kategorie-Vorspann (bis Doppelpunkt) entfernen
  • „Wien X und Wien Y“ → „Wien X ↔ Wien Y“
  • Pfeile/Bindestriche normalisieren (ein „↔“), Bahnhof/Hbf/Bf entfernen
  • Spitze Klammern etc. entfernen
- Plain-Text-Description (HTML/Word raus, Entities decodiert; Trenner „ • “)
- Strenger GEO-Filter: Behalte NUR Meldungen, deren Endpunkte in Wien
  oder definierter Pendler-Region (Whitelist) liegen
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from email.utils import parsedate_to_datetime

if TYPE_CHECKING:  # pragma: no cover - prefer package imports during type checks
    from ..utils.env import get_bool_env
    from ..utils.http import session_with_retries, validate_http_url
    from ..utils.ids import make_guid
    from ..utils.stations import canonical_name, is_in_vienna, is_pendler
    from ..utils.text import html_to_text
else:  # pragma: no cover - support both package layouts at runtime
    try:
        from utils.env import get_bool_env
    except ModuleNotFoundError:
        from ..utils.env import get_bool_env  # type: ignore

    try:
        from utils.ids import make_guid
        from utils.text import html_to_text
        from utils.stations import canonical_name, is_in_vienna, is_pendler
    except ModuleNotFoundError:
        from ..utils.ids import make_guid  # type: ignore
        from ..utils.text import html_to_text  # type: ignore
        from ..utils.stations import canonical_name, is_in_vienna, is_pendler  # type: ignore

    try:
        from utils.http import session_with_retries, validate_http_url
    except ModuleNotFoundError:
        from ..utils.http import session_with_retries, validate_http_url  # type: ignore
from defusedxml import ElementTree as ET

log = logging.getLogger(__name__)

_OEBB_URL_ENV = os.getenv("OEBB_RSS_URL", "").strip()
OEBB_URL = (
    validate_http_url(_OEBB_URL_ENV)
    or "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"
)

# Optional strenger Filter: Nur Meldungen mit Endpunkten in Wien behalten.
# Aktiviert durch Umgebungsvariable ``OEBB_ONLY_VIENNA`` ("1"/"true" vs "0"/"false", case-insens).
OEBB_ONLY_VIENNA = get_bool_env("OEBB_ONLY_VIENNA", False)

# ---------------- HTTP ----------------
USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"

# ---------------- Titel + Endpunkte ----------------
# remove generic suffixes like "Bahnhof" or "Hbf" when they appear as standalone
# tokens (optionally followed by "(U)", "(S)" or similar short indicators)
BAHNHOF_TRIM_RE = re.compile(
    r"\s*\b(?:Bahnhof|Bahnhst|Hbf|Bf)\b(?:\s*\(\s*[US]\d*\s*\))?",
    re.IGNORECASE,
)
# cover compound spellings that glue "Bahnhof"/"Hbf" directly to the
# station name but still end with whitespace, a hyphen or string end, e.g.
# "Ostbahnhof-Messe" → "Ost-Messe"
BAHNHOF_COMPOUND_RE = re.compile(
    r"(?<=\S)(?:Bahnhof|Bahnhst|Hbf|Bf)(?=(?:\s|-|$))",
    re.IGNORECASE,
)
# treat simple hyphen as separator only when surrounded by spaces
ARROW_ANY_RE    = re.compile(r"\s*(?:<=>|<->|<>|→|↔|=>|=|–|—|\s-\s)\s*")
COLON_PREFIX_RE = re.compile(
    r"""^\s*(?:Update\s*\d+\s*\([^)]*\)\s*)?
        (?:DB\s*↔\s*)?
        (?:[A-Za-zÄÖÜäöüß/ \-]+:\s*)+
    """, re.IGNORECASE | re.VERBOSE
)
MULTI_ARROW_RE  = re.compile(r"(?:\s*↔\s*){2,}")
_MULTI_SLASH_RE = re.compile(r"\s*/{2,}\s*")
_MULTI_COMMA_RE = re.compile(r"\s*,{2,}\s*")

def _clean_endpoint(p: str) -> str:
    p = BAHNHOF_TRIM_RE.sub("", p)
    p = _MULTI_SLASH_RE.sub("/", p)
    p = _MULTI_COMMA_RE.sub(", ", p)
    p = re.sub(r"\s{2,}", " ", p)
    return p.strip(" ,/")

def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    # Vorspann bis zum Doppelpunkt entfernen
    t = COLON_PREFIX_RE.sub("", t)
    # Sonderfall: „Wien X und Wien Y“ → „Wien X ↔ Wien Y“
    t = re.sub(r"\b(Wien [^,;|]+?)\s+und\s+(Wien [^,;|]+?)\b", r"\1 ↔ \2", t)
    # Pfeile/Bindestriche und Trennzeichen normalisieren
    raw_parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    canonical_parts: List[str] = []
    for part in raw_parts:
        segment = part.strip()
        if not segment:
            continue
        canon = canonical_name(segment)
        if not canon:
            cleaned = _clean_endpoint(segment)
            canon = canonical_name(cleaned) or cleaned
        if canon:
            canon = re.sub(r"\s+\(VOR\)$", "", canon)
        canonical_parts.append(canon)
    parts = canonical_parts
    if len(parts) >= 2:
        t = f"{parts[0]} ↔ {parts[1]}"
        if len(parts) > 2:
            rest = " ".join(parts[2:]).strip()
            if rest:
                t += f" {rest}"
    elif parts:
        t = parts[0]
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[<>«»‹›]+", "", t)
    return t.strip()

def _split_endpoints(title: str) -> Optional[List[str]]:
    """Extrahiert Endpunktnamen links/rechts (ohne Bahnhof/Hbf/Klammern)."""
    arrow_markers = (
        "↔", "<=>", "<->", "→", "=>", "->", "—", "–",
    )

    parts = []

    # 1. Standard: Pfeile oder " - "
    if any(a in title for a in arrow_markers) or re.search(r"\s-\s", title):
        parts = [
            p for p in re.split(r"\s*(?:↔|<=>|<->|→|=>|->|—|–|\s-\s)\s*", title) if p.strip()
        ]

    # 2. Fallback: Hyphen split if not a valid single station name
    elif "-" in title:
        # If the entire title matches a known station (e.g. "Deutsch-Wagram"),
        # do NOT split it.
        if canonical_name(title):
            return None

        parts = [p.strip() for p in title.split("-") if p.strip()]

    if len(parts) < 2:
        return None

    # Use only first and last part if > 2? Or just split first pivot?
    # Usually standard arrows split into 2 main blocks.
    # Simple hyphen split might produce "A-B-C".
    # For compatibility with explode logic, we take first vs remaining?
    # Or just treat everything as a list of endpoints.
    # The existing logic assumes strict left/right split.

    # We process all parts generically below.

    def explode(side: str) -> List[str]:
        tmp = re.split(r"\s*(?:/|,|bzw\.|oder|und)\s*", side, flags=re.IGNORECASE)
        names: List[str] = []
        for n in tmp:
            n = BAHNHOF_TRIM_RE.sub("", n)
            n = BAHNHOF_COMPOUND_RE.sub("", n)
            n = re.sub(r"\s*\([^)]*\)\s*", "", n)  # Klammern-Inhalte weg
            n = re.sub(r"\s{2,}", " ", n).strip(" .")
            if n:
                names.append(n)
        return names

    all_found = []
    for p in parts:
        all_found.extend(explode(p))

    return list(dict.fromkeys(all_found))

# ---------------- Region helpers ----------------
_MAX_STATION_WINDOW = 4
_FAR_AWAY_RE = re.compile(
    r"\b(salzburg|innsbruck|villach|bregenz|linz|graz|klagenfurt|bratislava|muenchen|passau|freilassing)\b",
    re.IGNORECASE,
)


def _is_allowed_station(name: str) -> bool:
    if is_in_vienna(name):
        return True
    if OEBB_ONLY_VIENNA:
        return False
    return is_pendler(name)


def _has_allowed_station(blob: str) -> bool:
    tokens = [t for t in re.split(r"\W+", blob) if t]
    if not tokens:
        return False
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            candidate = " ".join(tokens[idx : idx + size])
            if _is_allowed_station(candidate):
                return True
    return False


def _keep_by_region(title: str, desc: str) -> bool:
    endpoints = _split_endpoints(title)
    if endpoints:
        # Strecken: Alle Endpunkte müssen in whitelist sein (Pendler/Wien),
        # ABER mindestens einer MUSS explizit in Wien liegen.
        are_allowed = all(_is_allowed_station(x) for x in endpoints)
        has_vienna = any(is_in_vienna(x) for x in endpoints)
        return are_allowed and has_vienna

    blob = f"{title or ''} {desc or ''}"
    if not _has_allowed_station(blob):
        return False
    if _FAR_AWAY_RE.search(blob):
        return False
    return True

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> Optional[ET.Element]:
    with session_with_retries(USER_AGENT) as s:
        for attempt in range(2):
            r = s.get(url, timeout=timeout)
            status = getattr(r, "status_code", None)
            headers = getattr(r, "headers", {}) or {}
            if status == 429:
                retry_after = headers.get("Retry-After")
                log.warning("ÖBB RSS Rate-Limit (Retry-After: %s)", retry_after)
                wait_seconds: Optional[float] = None
                if retry_after:
                    try:
                        wait_seconds = float(retry_after)
                    except (TypeError, ValueError):
                        try:
                            retry_dt = parsedate_to_datetime(str(retry_after))
                        except (TypeError, ValueError, OverflowError):
                            wait_seconds = None
                        else:
                            if retry_dt is not None:
                                if retry_dt.tzinfo is None:
                                    retry_dt = retry_dt.replace(tzinfo=timezone.utc)
                                delta = (retry_dt - datetime.now(timezone.utc)).total_seconds()
                                wait_seconds = max(0.0, delta)
                if attempt == 0:
                    if wait_seconds and wait_seconds > 0:
                        try:
                            time.sleep(wait_seconds)
                        except Exception as sleep_err:
                            log.warning("ÖBB RSS Wartezeit fehlgeschlagen: %s", sleep_err)
                    continue
                return None
            if status is not None and status >= 400:
                log.warning("ÖBB RSS HTTP-Fehler: Status %s", status)
                return None
            return ET.fromstring(r.content)
    return None

def _get_text(elem: Optional[ET.Element], tag: str) -> str:
    e = elem.find(tag) if elem is not None else None
    return (e.text or "") if e is not None else ""

def _parse_dt_rfc2822(s: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

# ---------------- Public ----------------
def fetch_events(timeout: int = 25) -> List[Dict[str, Any]]:
    try:
        root = _fetch_xml(OEBB_URL, timeout=timeout)
    except Exception as e:
        msg = str(e).replace(OEBB_URL, "***")
        log.exception("ÖBB RSS abruf fehlgeschlagen: %s", msg)
        return []

    if root is None:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: List[Dict[str, Any]] = []
    for item in channel.findall("item"):
        raw_title = _get_text(item, "title")
        title = _clean_title_keep_places(raw_title)
        link  = _get_text(item, "link").strip() or OEBB_URL
        guid  = _get_text(item, "guid").strip() or make_guid(title, link)
        desc_html = _get_text(item, "description")
        desc = html_to_text(desc_html)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        # Region-Filter: nur Wien + definierter Pendelraum
        if not _keep_by_region(title, desc):
            continue

        out.append({
            "source": "ÖBB",
            "category": "Störung",
            "title": title,          # bereits kurz & ohne Bahnhof/Hbf
            "description": desc,     # plain
            "link": link,
            "guid": guid,
            "pubDate": pub,
            "starts_at": pub,
            "ends_at": None,
            "_identity": f"oebb|{guid}",
        })

    log.info("ÖBB: %d Items nach Region/Titel-Kosmetik", len(out))
    return out


__all__ = ["fetch_events"]
