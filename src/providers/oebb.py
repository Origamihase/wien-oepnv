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

import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from email.utils import parsedate_to_datetime

import requests

from ..feed_types import FeedItem
from ..utils.env import get_bool_env
from ..utils.ids import make_guid
from ..utils.stations import canonical_name, station_by_oebb_id, is_in_vienna, station_info, text_has_vienna_connection
from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe
from ..utils.logging import sanitize_log_arg

from defusedxml import ElementTree as ET # XXE Mitigation applied

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
RETRY_AFTER_MAX_SEC = 60.0

# ---------------- HTTP ----------------
USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"

# ---------------- Titel + Endpunkte ----------------
# remove generic suffixes like "Bahnhof" or "Hbf" when they appear as standalone
# tokens (optionally followed by "(U)", "(S)" or similar short indicators)
BAHNHOF_TRIM_RE = re.compile(
    r"\s*\b(?:Bahnhof|Bahnhst|Hbf|Bf)\b(?:\s*\(\s*[US]\d*\s*\))?",
    re.IGNORECASE,
)
# treat simple hyphen as separator only when surrounded by spaces
# Also swallow surrounding "decorations" like < > or &lt; &gt; if they wrap the arrow
# Also support double-escaped entities like &amp;lt; and &amp;gt; (seen in some feeds)
ARROW_ANY_RE    = re.compile(
    r"\s*(?:(?:<|&lt;|&amp;lt;|&#60;|&#x3C;)+\s*)?"
    r"(?:<=>|<->|<>|→|↔|=>|->|<-|=|–|—|\s-\s)"
    r"(?:\s*(?:>|&gt;|&amp;gt;|&#62;|&#x3E;)+)?\s*"
)
DESC_CLEANUP_RE = re.compile(
    r"(?:(?:<|&lt;|&amp;lt;|&#60;|&#x3C;)+\s*)"
    r"(?:<=>|<->|<>|→|↔|=>|->|<-)"
    r"(?:\s*(?:>|&gt;|&amp;gt;|&#62;|&#x3E;)+)|(?:<->|<=>)"
)

MULTI_ARROW_RE  = re.compile(r"(?:\s*↔\s*){2,}")
_MULTI_SLASH_RE = re.compile(r"\s*/{2,}\s*")
_MULTI_COMMA_RE = re.compile(r"\s*,{2,}\s*")

NON_LOCATION_PREFIXES = {
    "bauarbeiten", "störung", "störungen", "ausfall", "ausfälle", "verspätung", "verspätungen", "sperre",
    "einschränkung", "verkehrsunfall", "feuerwehreinsatz", "rettungseinsatz",
    "polizeieinsatz", "notarzteinsatz", "weichenstörung", "signalstörung",
    "oberleitungsstörung", "stellwerksstörung", "fahrzeugschaden", "personenschaden",
    "wetter", "unwetter", "schnee", "hochwasser", "murenabgang",
    "lawinengefahr", "streik", "demonstration", "veranstaltung", "wartungsarbeiten",
    "update", "info", "information", "hinweis", "achtung", "verkehrsmeldung",
    "umleitung", "haltausfall", "schienenersatzverkehr", "sev", "ersatzverkehr",
        "streckenunterbrechung", "unterbrechung", "teilausfall", "zugausfall",
        "verkehrseinschränkung"
}

def _is_category(text: str) -> bool:
    t = text.lower()

    t = re.sub(r"^(?:db|öbb|oebb|nj|rj|rjx|ic|ice|rex|s)[-\s]+", "", t)

    parts = re.split(r"[\s↔<>/\-–]+", t)
    for part in parts:
        if part in NON_LOCATION_PREFIXES:
            return True

    for k in NON_LOCATION_PREFIXES:
        if t == k or t.startswith(k + " "):
             return True

    return False

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
    t = html.unescape(t)

    # Redundanz-Check: Wenn Titel „Text: Station“ ist und Station im Text vorkommt,
    # dann nur Text nehmen (z.B. "Aufzug in X defekt: X").
    match = re.search(r"^([^:]+):\s+(.+)$", t)
    if match:
        text_part, suffix_part = match.group(1), match.group(2)
        # Check ob suffix im Text enthalten ist (case-sensitive)
        if suffix_part.strip() in text_part or text_part.strip() in suffix_part:
            t = text_part if len(text_part) > len(suffix_part) else suffix_part

    # Allgemeiner Fall: „X und Y“ → „X ↔ Y“ für Stationen
    t = re.sub(r"\b([^,;|]+?)\s+und\s+([^,;|]+?)\b", r"\1 ↔ \2", t)
    # Pfeile/Bindestriche und Trennzeichen normalisieren
    raw_parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    canonical_parts: List[str] = []
    for part in raw_parts:
        segment = part.strip()
        if not segment:
            continue

        # NEU: Präfix iterativ vom jeweiligen Segment abtrennen
        while True:
            match = re.match(r"^\s*([^:]+):\s*", segment)
            if not match:
                break

            prefix = match.group(1).strip()
            if _is_category(prefix):
                segment = segment[match.end():]
            else:
                break

        if not segment:
            continue

        canon = canonical_name(segment)
        if not canon:
            cleaned = _clean_endpoint(segment)
            canon = canonical_name(cleaned)

            # If full string lookup fails, try splitting composite endpoints (e.g. "Wien/ Flughafen Wien")
            # We require a space after the slash to avoid splitting names like "Linz/Donau" or "2/3".
            if not canon and re.search(r"/\s", segment):
                sub_segments = re.split(r"/\s+", segment)
                sub_segments = [s.strip() for s in sub_segments if s.strip()]

                if len(sub_segments) > 1:
                    processed_subs = []
                    for s in sub_segments:
                        # Resolve each part individually
                        c = canonical_name(s)
                        if not c:
                            cl = _clean_endpoint(s)
                            c = canonical_name(cl) or cl
                        if c:
                            c = re.sub(r"\s+\(VOR\)$", "", c)
                        processed_subs.append(c)
                    canon = "/ ".join(processed_subs)

            if not canon:
                canon = cleaned

        if canon:
            canon = re.sub(r"\s+\(VOR\)$", "", canon)
        canonical_parts.append(canon)
    parts = canonical_parts
    if len(parts) >= 2:
        # Check if first part is a category keyword -> use colon
        if _is_category(parts[0]):
             t = f"{parts[0]}: {parts[1]}"
             if len(parts) > 2:
                rest = " ".join(parts[2:]).strip()
                if rest:
                    t += f" {rest}"
        else:
            # Check ordering: if part[1] is Vienna and part[0] is not, swap
            if len(parts) == 2 and is_in_vienna(parts[1]) and not is_in_vienna(parts[0]):
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
    t = re.sub(r"&lt;|&gt;|&#60;|&#x3C;|&#62;|&#x3E;|[<>«»‹›]+", "", t)
    return t.strip()

# ---------------- Region / Filter Logic ----------------

def _strip_oebb_prefixes(text: str) -> str:
    """
    Entfernt iterativ typische ÖBB-Präfixe wie Liniencodes oder Störungsarten.

    Warum iterativ (while-Schleife) mit Regex anstatt naivem Split?
    ÖBB-Titel sind oft mehrfach mutiert, z.B. "REX 51: Störung: Wien". Ein naives
    Abschneiden am letzten oder ersten Doppelpunkt (`.split(':')`) würde echte
    Stationsnamen zerstören, die selbst Doppelpunkte enthalten (z.B. "Wien 10.: Favoriten").
    Daher iterieren wir und entfernen von vorne nur bekannte Präfixe, bis keines mehr matcht.
    """
    # Sucht nach Linien (z.B. REX 51, RJX 123) oder Wörtern gefolgt von Doppelpunkt
    base_pattern = (r"^(?:(?:REX|S|U|RJ|RJX|EC|IC|ICE|WB|NJ|D|R)\s*\d+|Störung|Verspätung|Zugausfall"
                    r"|Bauarbeiten|Info|Information|Einschränkung|Unterbrechung)\s*:\s*")
    while re.search(base_pattern, text, re.IGNORECASE):
        text = re.sub(base_pattern, "", text, flags=re.IGNORECASE)
    return text.strip()

def _is_relevant(title: str, description: str) -> bool:
    """
    Entscheidet über Relevanz für Wien-Pendler.

    Mehrstufiger Filter-Prozess:
    1. Guard für völlig unbekannte Fernverkehrsstationen: Sind beide Endpunkte `None`
       (und keine bekannten Kategorie-Schlüsselwörter), wird die Meldung verworfen
       (z.B. bei Verbindungen wie "Budapest ↔ Bratislava").
    2. Strikter Modus (OEBB_ONLY_VIENNA): Falls aktiviert, müssen alle bekannten
       Endpunkte explizit in Wien liegen.
    3. Asymmetrischer Pendler-Check: Wenn mindestens ein Endpunkt bekannt ist
       (`at_least_one_known`), muss zwingend auch mindestens ein Endpunkt in Wien liegen
       (`vienna_endpoint`), sonst wird die Strecke verworfen.
    4. Fallback auf `text_has_vienna_connection`: Für Meldungen ohne "↔" im Titel
       (z.B. allgemeine Meldungen ohne explizites Routing) fällt die Logik auf einen
       generischen Text-Check zurück.
    """
    text = f"{title} {description}"

    # Check 0: Strecken-Filter für explizite Routen A ↔ B
    if "↔" in title:
        parts = [p.strip() for p in title.split("↔")]
        if len(parts) >= 2:
            # Entferne eventuelle Präfixe wie "REX 51: " aus den Stationsnamen
            part0 = _strip_oebb_prefixes(parts[0])
            part1 = _strip_oebb_prefixes(parts[1])

            info0 = station_info(part0)
            info1 = station_info(part1)

            # Check if these are actually station names. If they are known category keywords
            # that were incorrectly joined with ↔ (like "Bauarbeiten ↔ Umleitung"), they might
            # evaluate to None. We only treat them as strict unknown stations if they don't look
            # like category keywords.
            if info0 is None and info1 is None and not _is_category(part0) and not _is_category(part1):
                # Wenn beide Stationen völlig unbekannt sind, ist es Fernverkehr -> verwerfen
                return False

            if OEBB_ONLY_VIENNA:
                if (info0 and not info0.in_vienna) or (info1 and not info1.in_vienna):
                    return False

            # Neuer, strikter Streckenabgleich:
            # Wenn mindestens ein Endpunkt bekannt ist, MUSS mindestens einer in Wien liegen.
            at_least_one_known = (info0 is not None) or (info1 is not None)
            vienna_endpoint = (info0 and info0.in_vienna) or (info1 and info1.in_vienna)

            if at_least_one_known:
                return bool(vienna_endpoint)

    return text_has_vienna_connection(text)

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

    # Filter out shorter overlapping matches
    sorted_found = sorted(list(found), key=len, reverse=True)
    filtered: List[str] = []
    for station in sorted_found:
        if not any(station in longer_station for longer_station in filtered):
            filtered.append(station)

    return sorted(filtered)

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> Optional[ET.Element]:
    with session_with_retries(USER_AGENT) as s:
        for attempt in range(2):
            try:
                content = fetch_content_safe(
                    s,
                    url,
                    timeout=timeout,
                    allowed_content_types=(
                        "application/xml",
                        "text/xml",
                        "application/rss+xml",
                    ),
                )
                return ET.fromstring(content)
            except (ValueError, ET.ParseError) as e:
                log.warning("ÖBB RSS: Content-Limit/Format-Fehler: %s", sanitize_log_arg(e))
                return None
            except requests.RequestException as e:
                log.warning("ÖBB RSS fetch fehlgeschlagen (Versuch %d): %s", attempt + 1, sanitize_log_arg(e))

                wait_seconds = 0.0
                if e.response is not None and e.response.status_code == 429:
                    wait_seconds = 1.0  # Default for 429 if no valid Retry-After is found
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
                            except Exception as parse_exc:
                                log.warning("Failed to parse Retry-After header", exc_info=parse_exc)
                    log.warning("ÖBB RSS Rate-Limit (Retry-After: %s)", header)

                if attempt == 0:
                     if wait_seconds > 0:
                         if wait_seconds > RETRY_AFTER_MAX_SEC:
                             log.warning("ÖBB RSS Rate-Limit überschreitet Maximum (%.1fs). Überspringe (Fail-Fast).", wait_seconds)
                             break
                         log.warning("ÖBB RSS Rate-Limit erreicht. Warte %.1fs (Retry-After).", wait_seconds)
                         time.sleep(wait_seconds)
                     continue
                raise

    return None

def _get_text(elem: Optional[ET.Element], tag: str) -> str:
    e = elem.find(tag) if elem is not None else None
    return (e.text or "") if e is not None else ""

def _parse_dt_rfc2822(s: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _is_poor_title(t: str) -> bool:
    return not t or not any(c.isalnum() for c in t) or t == "-"

# ---------------- Public ----------------
def fetch_events(timeout: int = 25) -> List[FeedItem]:
    root = _fetch_xml(OEBB_URL, timeout=timeout)

    if root is None:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: List[FeedItem] = []
    for item in channel.findall("item"):
        raw_title = _get_text(item, "title")
        title = _clean_title_keep_places(raw_title)
        link  = _get_text(item, "link").strip() or OEBB_URL
        raw_guid = _get_text(item, "guid").strip()
        if raw_guid and len(raw_guid) > 128:
            # Security: Prevent huge GUIDs from external feed
            guid = make_guid(raw_guid)
        else:
            guid = raw_guid or make_guid(title, link)
        desc_html = _get_text(item, "description")
        desc = _clean_description(desc_html)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        # Attempt to extract affected line from description (e.g. "REX 1", "S 50", "S-Bahn 1", "U1")
        # if not already present in the title.
        # Regex covers common Austrian train types + digit.
        line_match = re.search(r"\b((?:REX|S(?:-Bahn)?|U)\s*\d+)\b", desc)
        if line_match:
            line_str = line_match.group(1)
            # Prepend if not already in title (simple check)
            if line_str not in title:
                title = f"{line_str}: {title}"

        # Title Fallback for "poor" titles
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
