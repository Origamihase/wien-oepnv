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
from typing import List, Optional, Tuple
from email.utils import parsedate_to_datetime

import requests

from ..feed_types import FeedItem
from ..utils.env import get_bool_env
from ..utils.ids import make_guid
from ..utils.stations import (
    StationInfo,
    canonical_name,
    is_in_vienna,
    station_by_oebb_id,
    station_info,
    text_has_vienna_connection,
)
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
    base_pattern = (r"^(?:(?:REX|RJX|RJ|S|U|EC|ICE|IC|WB|NJ|CJX|D|R)\s*\d+|Störung|Verspätung|Zugausfall"
                    r"|DB-Bauarbeiten|Bauarbeiten|Info|Information|Einschränkung|Unterbrechung"
                    r"|Umleitung|Haltausfall|Schienenersatzverkehr|geänderte\s+Fahrzeiten"
                    r"|Verkehrsmeldung|Hinweis)\s*:\s*")
    while re.search(base_pattern, text, re.IGNORECASE):
        text = re.sub(base_pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


# ---------------- Route extraction ("zwischen X und Y") ----------------

# HTML-tolerant: a description usually contains entries like
# "zwischen <b>Flughafen Wien Bahnhof</b> und <b>Wien Mitte-Landstraße Bahnhof</b>".
# We strip HTML before matching so we only need a single plain-text pattern.
_ZWISCHEN_PLAIN_RE = re.compile(
    r"zwischen\s+(?P<a>.+?)\s+und\s+(?P<b>.+?)"
    r"(?=\s+(?:von|bis|am|im|in\s+der|jeweils|nicht|der\s+Zug|halten|fahren|"
    r"kommt|f[äa]hrt|fallen|k[öo]nnen|sowie|werden|ab|seit|um|gegen)\b|[,;.!?]|\s*$)",
    re.IGNORECASE | re.DOTALL,
)

# Suffixes that should be stripped before looking up a station name.
_BAHNHOF_TRAILING_RE = re.compile(
    r"\s*\b(?:Hauptbahnhof|Bahnhof|Bahnhst|Hbf|Bhf|Bf)\b\.?",
    re.IGNORECASE,
)
_PARENS_TRAILING_RE = re.compile(r"\s*\(\s*[A-Za-z]\d*\s*\)\s*$")


def _normalize_endpoint_name(name: str) -> str:
    """Strip HTML, trailing parenthetical markers and Bahnhof-suffixes.

    The result is suitable as input to :func:`station_info` for canonical
    classification.
    """
    if not name:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", name)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # Iteratively strip trailing parens like (U), (S), (R)
    while True:
        new = _PARENS_TRAILING_RE.sub("", cleaned).strip()
        if new == cleaned:
            break
        cleaned = new
    # Strip a single trailing Bahnhof/Hbf/Bf suffix (only at the end, so we
    # don't mangle names like "Wiener Neustadt Hauptbahnhof" → "Wiener Neustadt"
    # — which is actually what we want for lookup).
    cleaned = re.sub(_BAHNHOF_TRAILING_RE.pattern + r"\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _looks_like_station_name(text: str) -> bool:
    """Reject pure dates/numbers; require at least one alphabetic character."""
    if not text:
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]", text):
        return False
    # Reject things like "13.04.2026" (pure date) — they have no letters anyway,
    # but this guard is here for defence in depth.
    if re.fullmatch(r"[\d.\-/\s]+", text):
        return False
    return True


def _extract_zwischen_routes(description: str) -> List[Tuple[str, str]]:
    """Find all 'zwischen X und Y' route mentions in *description*.

    Returns a list of normalised ``(name_a, name_b)`` tuples. Names are
    deduplicated regardless of order (so ``A ↔ B`` and ``B ↔ A`` count once).
    """
    if not description:
        return []

    # Strip HTML tags and unescape entities; we want plain text for matching.
    plain = re.sub(r"<[^>]+>", " ", description)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    routes: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    for match in _ZWISCHEN_PLAIN_RE.finditer(plain):
        a_norm = _normalize_endpoint_name(match.group("a"))
        b_norm = _normalize_endpoint_name(match.group("b"))
        if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
            continue
        # Deduplicate regardless of A/B order
        sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
        key: Tuple[str, str] = (sorted_pair[0], sorted_pair[1])
        if key in seen:
            continue
        seen.add(key)
        routes.append((a_norm, b_norm))

    return routes


def _extract_routes(title: str, description: str) -> List[Tuple[str, str]]:
    """Collect route endpoint pairs from title (split on ↔) and description.

    Pure category words like "Bauarbeiten ↔ Umleitung" are filtered out so
    they don't drag a real station-mention message into the strict-route path
    incorrectly.
    """
    routes: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    # 1. Parse title — split on ↔
    if title and "↔" in title:
        parts = [p.strip() for p in title.split("↔")]
        for i in range(len(parts) - 1):
            a_raw = _strip_oebb_prefixes(parts[i])
            b_raw = _strip_oebb_prefixes(parts[i + 1])
            if _is_category(a_raw) or _is_category(b_raw):
                continue
            a_norm = _normalize_endpoint_name(a_raw)
            b_norm = _normalize_endpoint_name(b_raw)
            if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
                continue
            sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
            key: Tuple[str, str] = (sorted_pair[0], sorted_pair[1])
            if key in seen:
                continue
            seen.add(key)
            routes.append((a_norm, b_norm))

    # 2. Parse description — "zwischen X und Y" patterns
    for raw_a, raw_b in _extract_zwischen_routes(description):
        sorted_pair = sorted([raw_a.casefold(), raw_b.casefold()])
        desc_key: Tuple[str, str] = (sorted_pair[0], sorted_pair[1])
        if desc_key in seen:
            continue
        seen.add(desc_key)
        routes.append((raw_a, raw_b))

    return routes


def _classify_endpoint(name: str) -> Tuple[Optional[StationInfo], str]:
    """Look up *name* and return ``(info, category)``.

    Categories are one of ``vienna``, ``pendler``, ``distant`` (known but not
    relevant) or ``unknown``.
    """
    info = station_info(name)
    if info is None:
        return None, "unknown"
    if info.in_vienna:
        return info, "vienna"
    if info.pendler:
        return info, "pendler"
    return info, "distant"


def _route_is_wien_relevant(name_a: str, name_b: str) -> bool:
    """Strict-spec route check.

    Per project specification a route is relevant if both endpoints are known
    Vienna or Pendler stations and at least one of them is in Vienna. Pendler
    ↔ Pendler routes and routes with unknown/distant endpoints are excluded.

    When ``OEBB_ONLY_VIENNA`` is enabled, the rule is tightened further: both
    endpoints must lie inside Vienna.
    """
    _, cat_a = _classify_endpoint(name_a)
    _, cat_b = _classify_endpoint(name_b)
    if OEBB_ONLY_VIENNA:
        return cat_a == "vienna" and cat_b == "vienna"
    if cat_a not in ("vienna", "pendler") or cat_b not in ("vienna", "pendler"):
        return False
    return cat_a == "vienna" or cat_b == "vienna"


def _is_relevant(title: str, description: str) -> bool:
    """Decide whether an ÖBB message is relevant for Wien-Pendler.

    Strict rules:

    1. **Connection messages (A ↔ B / "zwischen X und Y")** – at least one
       extracted route must be Vienna ↔ Vienna or Vienna ↔ Pendler. If the
       message describes routes but none of them is Wien-relevant (e.g.
       Pendler ↔ Pendler, Wien ↔ Distant, or unknown endpoints), the message
       is dropped.
    2. **Single-station / general messages** – if no route can be parsed, the
       message must mention at least one Vienna or Pendler station. If only
       distant stations are mentioned, drop. Otherwise fall back to the
       generic Vienna-text heuristic for U-Bahn references.
    """
    routes = _extract_routes(title, description)

    if routes:
        for raw_a, raw_b in routes:
            if _route_is_wien_relevant(raw_a, raw_b):
                return True
        return False

    # No identifiable connection — single-station / general announcement path.
    text = f"{title} {description}"
    found_stations = _find_stations_in_text(text)

    has_relevant = False
    has_distant = False
    for s in found_stations:
        info = station_info(s)
        if not info:
            continue
        if info.in_vienna or info.pendler:
            has_relevant = True
        else:
            has_distant = True

    if has_relevant:
        return True
    if has_distant:
        return False

    # OEBB_ONLY_VIENNA narrows the fallback to text-detected Vienna references.
    if OEBB_ONLY_VIENNA:
        return False

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

# Single-token chunks that should not be treated as stations on their own
# (they would otherwise alias-match high-profile stations such as "Wien
# Hauptbahnhof" via the directory's expansion rules).
_GENERIC_STATION_TOKENS = frozenset({
    "hbf",
    "bhf",
    "bf",
    "bahnhof",
    "bahnhst",
    "hauptbahnhof",
    "westbahnhof",
    "westbf",
    "ostbahnhof",
    "ostbf",
    "südbahnhof",
    "suedbahnhof",
    "südbf",
    "suedbf",
    "nordbahnhof",
    "nordbf",
    "station",
})


def _find_stations_in_text(blob: str) -> List[str]:
    """
    Scans text for known station names using a sliding window.
    Returns a list of unique canonical station names found.
    """
    # Use whitespace splitting to preserve punctuation like '.' in 'St. Pölten'
    tokens = [t for t in re.split(r"[\s/]+", blob) if t]
    if not tokens:
        return []

    found = set()
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            chunk = " ".join(tokens[idx : idx + size])
            # Skip generic single-token aliases ("Hbf", "Bahnhof", …) that
            # would otherwise spuriously canonicalise to a flagship station.
            if size == 1 and chunk.casefold().rstrip(".:,;") in _GENERIC_STATION_TOKENS:
                continue
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


# ---------------- Title formatting helpers ----------------

# Recognises a leading line marker (REX 7, S 50, RJX 12, …) so we can preserve
# it even when we rebuild the title from extracted endpoints.
_LINE_PREFIX_RE = re.compile(
    r"^\s*((?:REX|RJX|RJ|EC|ICE|IC|WB|NJ|CJX|S-Bahn|S|U|R|D)\s*\d+[A-Za-z]?)\s*:?\s*",
    re.IGNORECASE,
)


def _extract_line_prefix(title: str) -> Tuple[str, str]:
    """Split off a leading line marker from *title*.

    Returns ``(line_prefix, remaining_title)``. The line prefix is empty when
    *title* doesn't start with a recognised marker.
    """
    if not title:
        return "", ""
    match = _LINE_PREFIX_RE.match(title)
    if not match:
        return "", title.strip()
    return match.group(1).strip(), title[match.end():].strip()


# Compact directory names sometimes use abbreviations (Westbf, Hbf, …) that
# look truncated in the feed. We expand them only for the user-facing title.
_STATION_NAME_EXPANSIONS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bWestbf\b"), "Westbahnhof"),
    (re.compile(r"\bOstbf\b"), "Ostbahnhof"),
    (re.compile(r"\bNordbf\b"), "Nordbahnhof"),
    (re.compile(r"\bSüdbf\b"), "Südbahnhof"),
    (re.compile(r"\bHbf\b"), "Hauptbahnhof"),
    (re.compile(r"-Bf\b"), "-Bahnhof"),
)


def _expand_station_abbreviations(name: str) -> str:
    """Expand common Bf/Hbf abbreviations for readability."""
    for pattern, replacement in _STATION_NAME_EXPANSIONS:
        name = pattern.sub(replacement, name)
    return name


def _format_route_title(routes: List[Tuple[str, str]], line_prefix: str = "") -> str:
    """Build a clean ``A ↔ B`` title from extracted route(s).

    For each route we use the canonical station name from the directory when
    available (so ``Wien Westbf`` → ``Wien Westbahnhof``). The Vienna endpoint
    is placed first to keep the feed visually consistent. Multiple routes are
    joined with ``" / "`` to indicate that several segments are affected.
    """
    if not routes:
        return ""

    formatted: List[str] = []
    for raw_a, raw_b in routes:
        info_a = station_info(raw_a)
        info_b = station_info(raw_b)
        name_a = info_a.name if info_a else raw_a
        name_b = info_b.name if info_b else raw_b
        # Canonical names from the directory may carry a "(VOR)" suffix that
        # is irrelevant for the user-facing title.
        name_a = re.sub(r"\s+\(VOR\)$", "", name_a).strip()
        name_b = re.sub(r"\s+\(VOR\)$", "", name_b).strip()
        name_a = _expand_station_abbreviations(name_a)
        name_b = _expand_station_abbreviations(name_b)

        # Vienna endpoint always goes first when only one side is in Vienna.
        a_in_vienna = bool(info_a and info_a.in_vienna)
        b_in_vienna = bool(info_b and info_b.in_vienna)
        if b_in_vienna and not a_in_vienna:
            name_a, name_b = name_b, name_a

        formatted.append(f"{name_a} ↔ {name_b}")

    title = " / ".join(formatted)
    if line_prefix:
        title = f"{line_prefix}: {title}"
    return title


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

        # Reconstruct a clean "A ↔ B" title from the authoritative endpoints
        # in the description ("zwischen X und Y") whenever possible. This
        # supersedes the messy raw title (e.g. "Bauarbeiten: Flughafen Wien
        # Wien Mitte-Landstraße") with canonical station names and drops
        # category prefixes such as "Bauarbeiten:" or "DB-Bauarbeiten:".
        existing_line_prefix, _ = _extract_line_prefix(title)
        routes = _extract_routes(title, desc)
        relevant_routes = [
            (a, b) for (a, b) in routes if _route_is_wien_relevant(a, b)
        ]
        if relevant_routes:
            title = _format_route_title(relevant_routes, existing_line_prefix)

        # Append affected line from description (e.g. "REX 1", "S 50", "U1")
        # if not already present in the title.
        line_match = re.search(r"\b((?:REX|S(?:-Bahn)?|U)\s*\d+)\b", desc)
        if line_match:
            line_str = line_match.group(1)
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

        # Region-Filter: Strict — drop messages that don't describe a
        # Wien-relevant connection or station. Run AFTER title fallback so
        # that fallback-derived titles (e.g. resolved via OEBB station ID)
        # contribute to the relevance check.
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


__all__ = ["fetch_events", "station_info"]
