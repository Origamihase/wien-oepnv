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
import json
import re
import time
import html
from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from email.utils import parsedate_to_datetime

import requests

if TYPE_CHECKING:  # pragma: no cover - prefer package imports during type checks
    from ..utils.env import get_bool_env
    from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe
    from ..utils.ids import make_guid
    from ..utils.logging import sanitize_log_arg
    from ..utils.stations import canonical_name, station_by_oebb_id, is_in_vienna
    from ..utils.text import html_to_text
else:  # pragma: no cover - support both package layouts at runtime
    try:
        from utils.env import get_bool_env
    except ModuleNotFoundError:
        from ..utils.env import get_bool_env  # type: ignore

    try:
        from utils.ids import make_guid
        from utils.text import html_to_text
        from utils.stations import canonical_name, station_by_oebb_id, is_in_vienna
    except ModuleNotFoundError:
        from ..utils.ids import make_guid  # type: ignore
        from ..utils.text import html_to_text  # type: ignore
        from ..utils.stations import canonical_name, station_by_oebb_id, is_in_vienna  # type: ignore

    try:
        from utils.http import session_with_retries, validate_http_url, fetch_content_safe
    except ModuleNotFoundError:
        from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe  # type: ignore

    try:
        from utils.logging import sanitize_log_arg
    except ModuleNotFoundError:
        from ..utils.logging import sanitize_log_arg  # type: ignore

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

# Max wait time for Retry-After headers to prevent DoS
RETRY_AFTER_MAX_SEC = 120.0

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
# Also swallow surrounding "decorations" like < > if they wrap the arrow
ARROW_ANY_RE    = re.compile(r"\s*(?:<+\s*)?(?:<=>|<->|<>|→|↔|=>|->|<-|=|–|—|\s-\s)(?:\s*>+)?\s*")
DESC_CLEANUP_RE = re.compile(r"(?:<+\s*)(?:<=>|<->|<>|→|↔|=>|->|<-)(?:\s*>+)|(?:<->|<=>)")

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

def _clean_description(text: str) -> str:
    if not text:
        return ""
    # Normalize arrows wrapped in angle brackets or specific ASCII arrows to ↔
    text = DESC_CLEANUP_RE.sub(" ↔ ", text)
    # Collapse spaces
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()

    # Redundanz-Check: Wenn Titel „Text: Station“ ist und Station im Text vorkommt,
    # dann nur Text nehmen (z.B. "Aufzug in X defekt: X").
    match = re.search(r"^(.*):\s+(.+)$", t)
    if match:
        text_part, suffix_part = match.group(1), match.group(2)
        # Check ob suffix im Text enthalten ist (case-sensitive)
        if suffix_part.strip() in text_part:
            t = text_part

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
        # Check ordering: if part[1] is Vienna and part[0] is not, swap
        if is_in_vienna(parts[1]) and not is_in_vienna(parts[0]):
             parts[0], parts[1] = parts[1], parts[0]

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

# ---------------- Region / Filter Logic ----------------

# Global sets for caching loaded station data
_VIENNA_STATIONS: Optional[set] = None
_OUTER_STATIONS: Optional[set] = None

# Compiled regexes for fast scanning
_VIENNA_STATIONS_RE: Optional[re.Pattern] = None
_OUTER_STATIONS_RE: Optional[re.Pattern] = None

def _load_station_sets():
    """
    Loads station data from data/stations.json and populates the global
    sets/regexes for Vienna vs. Outer stations.
    """
    global _VIENNA_STATIONS, _OUTER_STATIONS
    global _VIENNA_STATIONS_RE, _OUTER_STATIONS_RE

    if _VIENNA_STATIONS is not None:
        return

    _VIENNA_STATIONS = set()
    _OUTER_STATIONS = set()

    try:
        # Resolve path relative to this file: src/providers/oebb.py -> ../../data/stations.json
        base_dir = Path(__file__).resolve().parent.parent.parent
        data_path = base_dir / "data" / "stations.json"

        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = data.get("stations", [])

        for entry in data:
            is_vienna = entry.get("in_vienna", False)
            names = set()
            # Add main name
            if entry.get("name"):
                names.add(entry["name"])
            # Add aliases
            if entry.get("aliases"):
                names.update(entry["aliases"])

            # Normalize: lowercase, stripped. Filter out very short unsafe aliases (<3 chars) unless numeric
            # to avoid false positives (e.g. "Au" in "Aufzug", "Sg" matching "SG" for St. Gallen).
            normalized = set()
            for n in names:
                if not n:
                    continue
                n_clean = n.strip().lower()
                if len(n_clean) < 3 and not n_clean.isdigit():
                    continue
                # Filter generic aliases that would match any station (e.g. "Innsbruck Hbf" matching "Hbf")
                if n_clean in {"hbf", "bf", "bahnhof", "hauptbahnhof", "station"}:
                    continue
                normalized.add(n_clean)

            if is_vienna:
                _VIENNA_STATIONS.update(normalized)
            else:
                _OUTER_STATIONS.update(normalized)

        # Remove overlaps (if a name is in both, prefer Vienna or handle as such?
        # Actually logic is: Check A (Vienna items) then Check B (Outer items).
        # We don't need to remove overlaps for the sets, but for regex generation it helps.
        # But here we just build regexes.

        def _make_re(terms):
            if not terms:
                return re.compile(r"(?!x)x") # impossible match
            # Sort by length desc to match "Bad Vöslau" before "Bad"
            sorted_terms = sorted(terms, key=len, reverse=True)
            # Escape and join
            pattern = r"\b(?:" + "|".join(re.escape(t) for t in sorted_terms) + r")\b"
            return re.compile(pattern, re.IGNORECASE)

        _VIENNA_STATIONS_RE = _make_re(_VIENNA_STATIONS)
        _OUTER_STATIONS_RE = _make_re(_OUTER_STATIONS)

    except Exception as e:
        log.error("Failed to load station data for filtering: %s", e)
        # Fallback: empty sets
        _VIENNA_STATIONS = set()
        _OUTER_STATIONS = set()
        _VIENNA_STATIONS_RE = re.compile(r"(?!x)x")
        _OUTER_STATIONS_RE = re.compile(r"(?!x)x")


def _is_relevant(title: str, description: str) -> bool:
    """
    Entscheidet über Relevanz für Wien-Pendler.
    Logik:
    1. Check A (Explizit Wien): Text enthält "Wien" oder "Vienna". -> JA.
    2. Check B (Ort in Wien): Text enthält Bahnhof aus `vienna_stations`. -> JA.
    3. Check C (Ausschluss Umland): Text enthält *nur* Bahnhöfe aus `outer_stations` (und keine Wien-Referenz). -> NEIN.
    """
    _load_station_sets()

    text = f"{title} {description}" # Regex is case-insensitive, no need to lower() here for regex

    # Check A: Explizit Wien (Word boundaries)
    if re.search(r"\b(wien|vienna)\b", text, re.IGNORECASE):
        return True

    # Check B: Ort in Wien
    assert _VIENNA_STATIONS_RE is not None
    if _VIENNA_STATIONS_RE.search(text):
        return True

    # Check C: Ausschluss Umland
    # Wir sind hier nur, wenn WEDER Wien-Keyword NOCH Wien-Bahnhof gefunden wurde.
    # Wenn jetzt EIN Outer-Bahnhof gefunden wird, ist es eine "reine Umland-Meldung" -> Weg damit.
    assert _OUTER_STATIONS_RE is not None
    if _OUTER_STATIONS_RE.search(text):
        return False

    # Check D: Heuristik für unbekannte Routen
    # Wenn der Titel nach einer Strecke aussieht (Pfeil), aber kein bekannter
    # Bahnhof (Wien oder Umland) gefunden wurde, ist es vermutlich eine
    # Strecke weit weg (z.B. "Innsbruck ↔ Feldkirch") oder eine irrelevante
    # Formatierung (z.B. "Bauarbeiten ↔ Umleitung").
    # Note: Regex matches < ↔ > if cleanup failed, or just ↔.
    if "↔" in title or ARROW_ANY_RE.search(title):
        return False

    # Fallback: Keine bekannten Bahnhöfe gefunden.
    # Strict Policy: "Wenn ein Bahnhof unbekannt ist, gehört die Meldung nicht in den Feed."
    # Das bedeutet auch: "Allgemeine Störungen" ohne expliziten Wien-Bezug (Check A/B) werden gefiltert.
    return False

# ---------------- Region helpers ----------------
_MAX_STATION_WINDOW = 4

# ---------------- Fallback Helpers ----------------
def _extract_id_from_url(url: str) -> Optional[int]:
    """
    Extracts a numeric ID (e.g., station ID) from the end of a URL/GUID.
    Matches ...&123456 or ...?123456.
    """
    if not url:
        return None
    # Looking for &<digits> or ?<digits> at string end or before hash/other param
    # User example: ...&752992
    match = re.search(r"[?&](\d{6,})(?:$|[#&])", url)
    if match:
        return int(match.group(1))
    return None

def _find_stations_in_text(blob: str) -> List[str]:
    """
    Scans text for known station names using a sliding window.
    Returns a list of unique canonical station names found.
    """
    # Use whitespace splitting to preserve punctuation like '.' in 'St. Pölten'
    tokens = [t for t in blob.split() if t]
    if not tokens:
        return []

    found = set()
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            chunk = " ".join(tokens[idx : idx + size])
            canon = canonical_name(chunk)
            if canon:
                found.add(canon)

    return sorted(list(found))

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> Optional[ET.Element]:
    with session_with_retries(USER_AGENT) as s:
        for attempt in range(2):
            try:
                content = fetch_content_safe(s, url, timeout=timeout)
                return ET.fromstring(content)
            except ValueError as e:
                log.warning("ÖBB RSS: Content-Limit/Format-Fehler: %s", sanitize_log_arg(e))
                return None
            except requests.RequestException as e:
                log.warning("ÖBB RSS fetch fehlgeschlagen (Versuch %d): %s", attempt + 1, sanitize_log_arg(e))

                wait_seconds = 1.0
                if e.response is not None and e.response.status_code == 429:
                    header = e.response.headers.get("Retry-After")
                    if header:
                        try:
                            wait_seconds = float(header)
                        except (TypeError, ValueError):
                            try:
                                retry_dt = parsedate_to_datetime(header)
                                if retry_dt.tzinfo is None:
                                    retry_dt = retry_dt.replace(tzinfo=timezone.utc)
                                delta = (retry_dt - datetime.now(timezone.utc)).total_seconds()
                                wait_seconds = max(0.0, delta)
                            except Exception:
                                pass  # nosec B110
                    log.warning("ÖBB RSS Rate-Limit (Retry-After: %s)", header)

                if attempt == 0:
                     if wait_seconds > 0:
                        if wait_seconds > RETRY_AFTER_MAX_SEC:
                            log.warning("Retry-After %.1fs zu hoch – kappe auf %.1fs", wait_seconds, RETRY_AFTER_MAX_SEC)
                            wait_seconds = RETRY_AFTER_MAX_SEC
                        time.sleep(wait_seconds)
                     continue
                return None

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
        log.exception("ÖBB RSS abruf fehlgeschlagen: %s", sanitize_log_arg(e))
        return []

    if root is None:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: List[Dict[str, Any]] = []
    for item in channel.findall("item"):
        raw_title = _get_text(item, "title")
        # Decode HTML entities (e.g. "&lt;" -> "<") for cleanup regexes
        raw_title = html.unescape(raw_title)
        title = _clean_title_keep_places(raw_title)
        link  = _get_text(item, "link").strip() or OEBB_URL
        guid  = _get_text(item, "guid").strip() or make_guid(title, link)
        desc_html = _get_text(item, "description")
        desc = html_to_text(desc_html)
        desc = _clean_description(desc)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        # Title Fallback for "poor" titles
        def _is_poor_title(t: str) -> bool:
            return not t or not any(c.isalnum() for c in t) or t == "-"

        if _is_poor_title(title):
            # Attempt 1: ID from Link/GUID
            station_id = _extract_id_from_url(link) or _extract_id_from_url(guid)
            if station_id:
                found_name = station_by_oebb_id(station_id)
                if found_name:
                    title = found_name

            # Attempt 2: Text extraction (if still poor)
            if _is_poor_title(title):
                stations_found = _find_stations_in_text(desc)
                if len(stations_found) == 1:
                    title = stations_found[0]
                elif len(stations_found) >= 2:
                    title = f"{stations_found[0]} ↔ {stations_found[1]}"

            # Attempt 3: Truncation
            if _is_poor_title(title):
                snippet = desc.strip()
                if len(snippet) > 40:
                    snippet = snippet[:40] + "..."
                if snippet:
                    title = snippet

        # Region-Filter: Neue Logik (Wien-Bezug strikt)
        if not _is_relevant(title, desc):
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
