#!/usr/bin/env python3

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
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
from itertools import pairwise

import requests

from ..feed_types import FeedItem
from ..utils.env import get_bool_env
from ..utils.ids import make_guid
from ..utils.stations import (
    StationInfo,
    canonical_name,
    display_name,
    is_in_vienna,
    station_by_oebb_id,
    station_info,
    text_has_vienna_connection,
)
from ..utils.http import (
    fetch_content_safe,
    parse_retry_after,
    session_with_retries,
    validate_http_url,
)
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

# Topics the user explicitly excludes from the feed: facility-only
# notices (broken elevators, escalators) and standalone weather warnings.
# A message is treated as such when its title carries one of these
# keywords AND none of the "real" transit-disruption keywords below.
_FACILITY_KEYWORD_RE = re.compile(
    r"\b(aufzug|aufzüge|aufzuege|aufzugsinfo|lift|fahrstuhl|fahrtreppe|"
    r"fahrtreppen|fahrtreppeninfo|rolltreppe|rolltreppen)\b",
    re.IGNORECASE,
)
_WEATHER_KEYWORD_RE = re.compile(
    r"\b(sturm|sturmwarnung|unwetter|gewitter|hochwasser|wetter|wetterlage|"
    r"glatteis|schneefall|schneefälle|murenabgang|lawinengefahr)\b",
    re.IGNORECASE,
)
_TRANSIT_KEYWORD_RE = re.compile(
    # Match common stems and accept trailing inflection (Verspätung →
    # Verspätungen, Sperrung → Sperrungen, etc.). The trailing
    # ``\w{0,4}`` keeps the regex bounded but covers German plural and
    # genitive forms without enumerating every variant.
    r"\b(bauarbeit\w{0,4}|störung\w{0,4}|stoerung\w{0,4}|"
    r"verspätung\w{0,4}|verspaetung\w{0,4}|sperre\w{0,4}|sperrung\w{0,4}|"
    r"gesperrt|geschlossen|unterbrochen|eingestellt|"
    r"umleitung\w{0,4}|ersatzverkehr|haltausfall\w{0,4}|zugausfall\w{0,4}|"
    r"streckenunterbrechung\w{0,4}|unterbrechung\w{0,4}|teilausfall\w{0,4}|"
    r"baustelle\w{0,4}|gleisbauarbeit\w{0,4}|"
    r"schienenersatzverkehr|sev|fahrplanänderung\w{0,4}|"
    r"fahrplanaenderung\w{0,4}|"
    r"verkehrseinschränkung\w{0,4}|verkehrseinschraenkung\w{0,4}|"
    r"einschränkung\w{0,4}|einschraenkung\w{0,4})\b",
    re.IGNORECASE,
)


def _is_facility_or_weather_only(title: str, description: str) -> bool:
    """Decide whether the message has no place in the Wien-ÖPNV feed.

    Per project spec elevator/escalator notices have nothing to do in
    the feed — including titles that combine "Bauarbeiten" with
    "Aufzug betroffen", because the actual subject is still the broken
    elevator and not a service-affecting track disruption. Any mention
    of a facility keyword in the title therefore drops the message.

    Weather titles are dropped only when the title doesn't also carry a
    real disruption keyword: ``Sturmschaden: Strecke Wien-Mödling
    gesperrt`` describes a genuine transit interruption (cause: Sturm)
    and stays in the feed, while ``Sturm im Raum Wien`` / ``Wetterlage
    Wien`` are pure weather warnings and drop.
    """
    if not title:
        return False
    title_low = title.lower()
    has_facility = bool(_FACILITY_KEYWORD_RE.search(title_low))
    if has_facility:
        # Strict: any facility-keyword title drops, with or without an
        # accompanying transit keyword. Side-mentions of "Aufzug" should
        # never reach the feed per user spec.
        return True
    has_weather = bool(_WEATHER_KEYWORD_RE.search(title_low))
    if not has_weather:
        return False
    # Weather: drop only when the title has no real disruption signal.
    return not _TRANSIT_KEYWORD_RE.search(title_low)


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


# Recognises an ÖBB line marker at the very start of a title. The
# prefix is split off before the segment iteration runs and re-attached
# afterwards — without this, ``S40: Wien Franz-Josefs-Bahnhof ↔ Wien
# Heiligenstadt`` produced ``Wien Heiligenstadt ↔ S40: Wien
# Franz-Josefs-…`` because the Vienna-first reorder logic mistakenly
# classifies ``S40: Wien Franz-Josefs-Bahnhof`` (with the line prefix
# still glued on) as "not in Vienna" — the prefix breaks the
# station_info lookup — and swaps the endpoints.
_LEADING_LINE_PREFIX_RE = re.compile(
    r"^\s*((?:REX|RJX|RJ|EC|ICE|IC|WB|NJ|CJX|S-Bahn|S|U-Bahn|U|R|D)\s*\d+[A-Za-z]?)\s*:\s*",
    re.IGNORECASE,
)


def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    t = html.unescape(t)

    # Preserve a leading line marker (S40:, REX 7:, …) through the
    # cleanup. The endpoint-reordering logic below would otherwise
    # treat the prefix as part of the first endpoint and silently
    # swap A↔B when only one side resolves to a Vienna station.
    line_prefix = ""
    line_match = _LEADING_LINE_PREFIX_RE.match(t)
    if line_match:
        line_prefix = line_match.group(1).strip()
        t = t[line_match.end():]

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
    canonical_parts: list[str] = []
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
                            c = display_name(c)
                        processed_subs.append(c)
                    canon = " / ".join(processed_subs)

            if not canon:
                canon = cleaned

        if canon:
            canon = display_name(canon)
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

            # Multi-part titles arise from chains like ``A ↔ B / C ↔ D``
            # where ``ARROW_ANY_RE`` split off three parts (``A``, ``B / C``,
            # ``D``). Joining the tail with a plain space silently drops
            # the inner ``↔`` separators so the latent intermediate read
            # ``A ↔ B / C  D`` — only ``_format_route_title`` later
            # rebuilt a clean title. Joining everything with `` ↔ ``
            # keeps the inter-route arrows so the cleaner can be relied
            # on as a stand-alone pre-processor too.
            t = " ↔ ".join(parts)
    elif parts:
        t = parts[0]
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"&lt;|&gt;|&#60;|&#x3C;|&#62;|&#x3E;|[<>«»‹›]+", "", t)
    t = t.strip()
    # Re-attach the leading line prefix that was split off above so
    # downstream consumers (``_extract_line_prefix``) still recognise it.
    if line_prefix and t:
        t = f"{line_prefix}: {t}"
    elif line_prefix:
        t = line_prefix
    return t

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
#
# The lookahead is the tricky part. Two real-world failure modes drove the
# current shape:
#
# 1. A bare "." in the boundary class truncated abbreviated station names:
#    "und St. Pölten ist …" was captured as endpoint "St". The fix accepts
#    a period as a boundary only when it ends a sentence (end-of-string,
#    closing tag, or followed by whitespace + a lowercase German word) —
#    not when it sits inside an abbreviation like "St.".
# 2. Several common follow-up words ("aufgrund", "wegen", em-dash, …) were
#    not in the boundary list, so the regex over-extended into them
#    ("Mödling aufgrund Sturm" became the second endpoint). The list below
#    covers the recurring ÖBB phrasings.
_ZWISCHEN_PLAIN_RE = re.compile(
    r"zwischen\s+(?P<a>.+?)\s+und\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:"
    # Time/date prepositions
    r"von|bis|am|im|in\s+der|jeweils|ab|seit|um|gegen|nach|vor|"
    # Causal/circumstantial
    r"aufgrund|wegen|durch|f[üu]r|infolge|trotz|während|waehrend|"
    # Verbs typical for the predicate that follows the route phrase
    r"nicht|der\s+Zug|halten|fahren|kommt|f[äa]hrt|fallen|k[öo]nnen|"
    r"werden|wird|kann|d[üu]rfen|sollen|soll|m[üu]ssen|"
    # State / past-participle endings of the sentence
    r"ist|sind|war|waren|gesperrt|geschlossen|blockiert|eingestellt|"
    r"unterbrochen|gestört|gestoert|ausgefallen|verspätet|verspaetet|"
    r"verz[öo]gert|aufgehoben|freigegeben|"
    # Connectors that introduce a side clause / next "zwischen X und Y"
    r"sowie|sondern|sowie\s+zwischen|und\s+zwischen|,\s*und|"
    # Quantifiers that introduce a noun phrase about affected trains
    # ("…Bruck/Leitha Bahnhof einige Nahverkehrszüge ausgefallen"). Without
    # these, the non-greedy ``b`` over-extended into the entire affected-
    # train clause and produced frankenstring endpoints.
    r"einige|keine|kein|alle|mehrere|wenige|s[äa]mtliche|"
    # Intermediate-via marker — ends the captured endpoint at the via stop
    # so "Mödling über Wiener Neudorf" yields b="Mödling".
    r"[üu]ber|via"
    r")\b"
    r"|[,;!?]"  # Plain sentence punctuation (period excluded — see above)
    r"|[—–]"  # German em-/en-dash often introduces a side remark
    r"|<"  # HTML tag start (defensive: we strip HTML, but stay safe)
    # Period followed by a German sentence-starter word — typical ÖBB
    # closers like "Mödling. Auch Auswirkung …" or "Mödling. Wir bitten
    # …". Listed words cannot be the second part of a station name, so
    # "St. Pölten" stays intact while sentence boundaries are recognised.
    r"|\.\s+(?:Auch|Bitte|Wir|Es|Hier|Hinweis|Achtung|Wegen|Aufgrund|"
    r"Heute|Morgen|Reisende|Details|Diese|Dieser|Bei|Im|Aus|Mit|"
    r"Fahrgäste|Fahrgaeste|Beachten|ACHTUNG|HINWEIS)"
    r"|\.\s*$"  # Period only when it terminates the description
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


# Alternative route-phrasing — "von X nach Y", "ab X bis Y" — used in
# ÖBB descriptions when a relation is described directionally rather
# than as a connection. Captures both endpoints so the strict route
# check can reject Wien↔Distant variants (e.g. "ab Wien Hbf bis Graz
# Hbf").
_VON_NACH_PLAIN_RE = re.compile(
    r"\b(?:von|ab)\s+(?P<a>.+?)\s+(?:nach|bis)\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:gesperrt|geschlossen|unterbrochen|eingestellt|"
    r"betroffen|beeintr[äa]chtigt|gest[öo]rt|eingeschr[äa]nkt|"
    r"auf|au[ßs]er\s+betrieb|nicht|kein|von|bis|am|im|in\s+der|"
    r"f[üu]r|wegen|aufgrund|durch|infolge|"
    r"ist|sind|war|waren|wird|werden|kann|k[öo]nnen|"
    r"f[äa]hrt|fahren|kommt|fallen|halten|halten\s+nicht)\b"
    r"|[,;!?]"
    r"|[—–]"
    r"|<"
    r"|\.\s+(?:Auch|Bitte|Wir|Es|Hier|Hinweis|Achtung|Wegen|Aufgrund|"
    r"Heute|Morgen|Reisende|Details|Diese|Dieser|Bei|Im|Aus|Mit|"
    r"Fahrgäste|Fahrgaeste|Beachten|ACHTUNG|HINWEIS)"
    r"|\.\s*$"
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


# Alternative route-phrasing some descriptions use instead of "zwischen X und Y":
# "Strecke X — Y", "Verbindung X-Y", "Linie X bis Y". The hyphen / en-dash /
# em-dash separator is required to be surrounded by whitespace so we don't
# split compound station names like "Wien Mitte-Landstraße".
_STRECKE_PLAIN_RE = re.compile(
    r"(?:strecke|verbindung|linie|abschnitt)\s+(?P<a>.+?)\s+[-—–]\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:gesperrt|geschlossen|unterbrochen|eingestellt|"
    r"betroffen|beeintr[äa]chtigt|gest[öo]rt|eingeschr[äa]nkt|"
    r"auf|au[ßs]er\s+betrieb|nicht|kein|von|bis|am|im|in\s+der|"
    r"f[üu]r|wegen|aufgrund|durch|infolge|"
    r"ist|sind|war|waren|wird|werden|kann|k[öo]nnen)\b"
    r"|[,;!?]"
    r"|[—–]"
    r"|<"
    r"|\.\s*$"
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Suffixes that should be stripped before looking up a station name.
_BAHNHOF_TRAILING_RE = re.compile(
    r"\s*\b(?:Hauptbahnhof|Bahnhof|Bahnhst|Hbf|Bhf|Bf)\b\.?",
    re.IGNORECASE,
)
# Variant that anchors at end-of-string AND rejects a leading hyphen so
# compound proper nouns like ``Wien Franz-Josefs-Bahnhof`` keep the
# trailing ``-Bahnhof`` intact. Stripping it produced a dangling
# ``Wien Franz-Josefs-`` that leaked into dedup keys (and into visible
# titles whenever the alias resolution in :func:`station_info` failed to
# bridge the truncation).
_BAHNHOF_TRAILING_END_RE = re.compile(
    r"(?<![\-‐-―])\s+\b(?:Hauptbahnhof|Bahnhof|Bahnhst|Hbf|Bhf|Bf)\b\.?\s*$",
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
    cleaned = _BAHNHOF_TRAILING_END_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    # If the endpoint absorbed a sentence boundary ("Mödling. Auch ..."),
    # truncate to the part before the period when *that* part resolves
    # against the directory. Abbreviations like "St. Pölten" stay intact
    # because "St" alone doesn't resolve.
    if ". " in cleaned:
        head, _, _tail = cleaned.partition(". ")
        head_clean = head.strip()
        if head_clean and station_info(head_clean) is not None:
            cleaned = head_clean

    return cleaned


def _looks_like_station_name(text: str) -> bool:
    """Reject pure dates/numbers and date/time fragments.

    Real station names start with a letter (typically capitalised) and
    contain at least three consecutive alphabetic characters. Dates,
    times and number-prefixed phrases like ``03.10.2026 (23:15 Uhr)``
    are rejected up-front so the route extractor never treats them as
    endpoints.
    """
    if not text:
        return False
    text = text.strip()
    if not text:
        return False
    # Reject if starts with a digit — almost always a date/time fragment
    # ("03.10.2026 (23:15 Uhr) einige …").
    if text[0].isdigit():
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]", text):
        return False
    # Reject if no run of three or more alphabetic characters survives
    # — guards against "Hbf (U)" residue after stripping.
    if not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", text):
        return False
    # Defence in depth: reject if the entire string is dates / numbers
    # interleaved with punctuation.
    if re.fullmatch(r"[\d.\-/\s]+", text):
        return False
    return True


def _extract_zwischen_routes(description: str) -> list[tuple[str, str]]:
    """Find all route mentions in *description*.

    Recognises both the dominant ``zwischen X und Y`` phrasing and the
    alternative ``Strecke|Verbindung|Linie|Abschnitt X — Y`` form. Returns a
    list of normalised ``(name_a, name_b)`` tuples, deduplicated regardless
    of A/B order (so ``A ↔ B`` and ``B ↔ A`` count once).
    """
    if not description:
        return []

    # Strip HTML tags and unescape entities; we want plain text for matching.
    plain = re.sub(r"<[^>]+>", " ", description)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    routes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for regex in (_ZWISCHEN_PLAIN_RE, _STRECKE_PLAIN_RE, _VON_NACH_PLAIN_RE):
        for match in regex.finditer(plain):
            a_norm = _normalize_endpoint_name(match.group("a"))
            b_norm = _normalize_endpoint_name(match.group("b"))
            if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
                continue
            # Deduplicate regardless of A/B order
            sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
            key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
            if key in seen:
                continue
            seen.add(key)
            routes.append((a_norm, b_norm))

    return routes


def _extract_routes(title: str, description: str) -> list[tuple[str, str]]:
    """Collect route endpoint pairs from title (split on ↔) and description.

    Pure category words like "Bauarbeiten ↔ Umleitung" are filtered out so
    they don't drag a real station-mention message into the strict-route path
    incorrectly. Likewise candidates whose endpoints are obviously
    non-station references (``Bahnsteig 1`` / ``Bahnsteig 5``, ``Gleis 3``,
    ``Aufzug``-etc.) are discarded so the single-station fall-through can
    pick up the real station mention.

    Real distant routes between two unknown stations (e.g. ``Mürzzuschlag
    ↔ Payerbach-Reichenau``) are *kept* — they look like real station
    names, and the strict route classifier in :func:`_route_is_wien_relevant`
    is then responsible for rejecting them.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # 1. Parse title — split on ↔
    if title and "↔" in title:
        # Multi-route titles like "A ↔ B / C ↔ D" must be split on " / "
        # before pairing, otherwise the inner endpoints fuse into
        # frankenstrings ("B / C" and "C / D") and the strict route check
        # never sees the real (A,B) and (C,D) pairs. We restrict the split
        # to whitespace-bounded slashes so compound names like "Linz/Donau"
        # or "Bruck/Leitha" stay intact.
        title_segments = [
            seg.strip() for seg in re.split(r"\s+/\s+", title) if seg.strip()
        ]
        for segment in title_segments:
            if "↔" not in segment:
                continue
            parts = [p.strip() for p in segment.split("↔")]
            for left, right in pairwise(parts):
                a_raw = _strip_oebb_prefixes(left)
                b_raw = _strip_oebb_prefixes(right)
                if _is_category(a_raw) or _is_category(b_raw):
                    continue
                a_norm = _normalize_endpoint_name(a_raw)
                b_norm = _normalize_endpoint_name(b_raw)
                if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
                    continue
                sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
                key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((a_norm, b_norm))

    # 2. Parse description — "zwischen X und Y" patterns
    for raw_a, raw_b in _extract_zwischen_routes(description):
        sorted_pair = sorted([raw_a.casefold(), raw_b.casefold()])
        desc_key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
        if desc_key in seen:
            continue
        seen.add(desc_key)
        candidates.append((raw_a, raw_b))

    # 3. Drop candidates that describe a station-internal element rather
    #    than a transit connection. This is what allows "Aufzug zwischen
    #    Bahnsteig 1 und Bahnsteig 5 in Wien Mitte defekt" to fall through
    #    to the single-station path and pick up the Wien-Mitte mention.
    routes: list[tuple[str, str]] = []
    for a, b in candidates:
        if _looks_like_facility_endpoint(a) or _looks_like_facility_endpoint(b):
            continue
        routes.append((a, b))

    return routes


def _classify_endpoint(name: str) -> tuple[StationInfo | None, str]:
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


# Word tokens that may legitimately appear in an ÖBB title alongside
# station names without indicating a second endpoint. Anything not in this
# set AND not part of a known station is treated as a suspect unknown
# station mention.
_TITLE_NOISE_WORDS = frozenset({
    # Categories / event types (also covered by NON_LOCATION_PREFIXES, kept
    # explicit for clarity)
    "bauarbeiten",
    "bauarbeit",
    "störung",
    "stoerung",
    "verspätung",
    "verspaetung",
    "verspätungen",
    "verspaetungen",
    "ausfall",
    "ausfälle",
    "ausfaelle",
    "zugausfall",
    "umleitung",
    "haltausfall",
    "schienenersatzverkehr",
    "ersatzverkehr",
    "sev",
    "polizeieinsatz",
    "rettungseinsatz",
    "personenschaden",
    "fahrzeugschaden",
    "weichenstörung",
    "weichenstoerung",
    "signalstörung",
    "signalstoerung",
    "info",
    "information",
    "hinweis",
    "achtung",
    # Bahnhof-Suffixe
    "hbf",
    "bf",
    "bhf",
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
    # German articles / prepositions / conjunctions
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einer",
    "einem",
    "eines",
    "und",
    "oder",
    "aber",
    "sowie",
    "in",
    "im",
    "an",
    "am",
    "auf",
    "aus",
    "bei",
    "mit",
    "von",
    "vom",
    "zu",
    "zum",
    "zur",
    "nach",
    "vor",
    "über",
    "ueber",
    "via",
    "ab",
    "bis",
    "wegen",
    "aufgrund",
    "während",
    "waehrend",
    "zwischen",
    "neue",
    "neuer",
    "neues",
    "alle",
    "alle.",
    "kein",
    "keine",
    "halt",
    # Time/calendar words that occasionally surface in titles
    "heute",
    "morgen",
    "wochenende",
    "wochenenden",
    "feiertag",
    "feiertage",
    # Weather words (Sturm/Wetter etc.) — not station names; mentioning
    # them after a known Vienna station shouldn't drag the message into
    # the implicit-route heuristic.
    "sturm",
    "sturmschaden",
    "sturmwarnung",
    "unwetter",
    "gewitter",
    "hochwasser",
    "wetter",
    "wetterlage",
    "wettersituation",
    "glatteis",
    "schnee",
    "schneefall",
    "schneefälle",
    "schneefaelle",
    "regen",
    "wind",
    "hagel",
    "frost",
    "murenabgang",
    "lawinengefahr",
    "lawine",
    # Facility / generic location words (already in _NON_STATION_FIRST_WORDS
    # but we list them here too for the title-residual check)
    "aufzug",
    "lift",
    "fahrtreppe",
    "rolltreppe",
    "bahnsteig",
    "gleis",
    "wagen",
    "ausgang",
    "eingang",
    "halle",
    "platz",
    "sektor",
    "zone",
    # German short forms
    "b",
    "u",
    "s",
    # Generic transit-meta words that shouldn't be misread as endpoints
    "linie",
    "linien",
    "strecke",
    "strecken",
    "verbindung",
    "verbindungen",
    "abschnitt",
    "abschnitte",
    "bereich",
    "bereiche",
    "richtung",
    "fahrtrichtung",
    "haltestelle",
    "haltestellen",
    "station",
    "stationen",
    "betrieb",
    "verkehr",
    "fahrgäste",
    "fahrgaeste",
    "fahrt",
    "fahrten",
    "zug",
    "zuege",
    "züge",
    "fernverkehr",
    "fernverkehrszüge",
    "fernverkehrszuege",
    "nahverkehr",
    "nahverkehrszüge",
    "nahverkehrszuege",
})


def _title_has_unknown_endpoint(title: str) -> bool:
    """Heuristic: detect titles like ``Wiener Neustadt Hauptbahnhof Semmering``
    that pair a known Wien/Pendler station with a station-name-shaped token
    that doesn't resolve against the directory.

    Real ÖBB titles list disrupted endpoints directly (``<Station1>
    <Station2>``); when one of those stations is missing from
    stations.json the strict route check never sees the connection and the
    single-station fall-through used to keep the message even though the
    real route is Pendler↔Distant or Wien↔Distant.

    Returns ``True`` when the title likely encodes such an implicit route
    so the caller can drop the message. Conservative: only fires when at
    least one Wien/Pendler station is present in the title AND the
    remaining content has at least one capitalized, non-stop-word token of
    three or more letters.
    """
    if not title:
        return False
    if "↔" in title:
        # Explicit-route titles are handled by _extract_routes; do not
        # second-guess them here.
        return False

    stripped = _strip_oebb_prefixes(title)
    # Remove any leading category prefix like "Bauarbeiten:" / "Hinweis:".
    while True:
        match = re.match(r"^\s*([^:]+):\s*", stripped)
        if not match:
            break
        prefix = match.group(1).strip()
        if not _is_category(prefix):
            break
        stripped = stripped[match.end():]

    if not stripped:
        return False

    # Collect every textual span that resolves to a known station so we
    # can subtract it from the title. We use a sliding-window approach
    # similar to _find_stations_in_text but record start/end offsets.
    tokens_re = re.finditer(r"\S+", stripped)
    tokens = [(m.start(), m.end(), m.group(0)) for m in tokens_re]
    if not tokens:
        return False

    used = [False] * len(tokens)
    has_relevant_station = False
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            if any(used[idx : idx + size]):
                continue
            chunk = " ".join(t[2] for t in tokens[idx : idx + size])
            chunk_clean = chunk.strip(" ,.;:()[]")
            if size == 1:
                token_norm = chunk_clean.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_STATION_TOKENS:
                    continue
            canon = canonical_name(chunk_clean)
            if not canon:
                continue
            info = station_info(chunk_clean)
            if info and (info.in_vienna or info.pendler):
                has_relevant_station = True
            for j in range(idx, idx + size):
                used[j] = True

    if not has_relevant_station:
        return False

    # The implicit second endpoint can only sit AFTER the last known
    # station match — otherwise we'd flag tokens that are clearly part of
    # the message preamble (e.g. "Sturm im Raum Wien" has "Sturm" before
    # the known "Wien" and is just a generic Vienna weather notice, not a
    # Sturm-↔-Wien route).
    last_known_idx = -1
    for idx, flag in enumerate(used):
        if flag:
            last_known_idx = idx

    for idx in range(last_known_idx + 1, len(tokens)):
        if used[idx]:
            continue
        raw = tokens[idx][2]
        clean = raw.strip(" .,;:()[]/")
        if not clean:
            continue
        norm = clean.casefold()
        if norm in _TITLE_NOISE_WORDS:
            continue
        if norm in _GENERIC_STATION_TOKENS:
            continue
        if norm in _NON_STATION_FIRST_WORDS:
            continue
        alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", clean)
        if len(alpha) < 3:
            continue
        if not clean[0:1].isupper():
            continue
        # Found a station-name-shaped token after the last known match —
        # treat as an implicit second endpoint of an unknown station.
        return True

    return False


def _is_relevant(title: str, description: str) -> bool:
    """Decide whether an ÖBB message is relevant for Wien-Pendler.

    Strict rules:

    1. **Connection messages (A ↔ B / "zwischen X und Y")** – at least one
       extracted route must be Vienna ↔ Vienna or Vienna ↔ Pendler. If the
       message describes routes but none of them is Wien-relevant (e.g.
       Pendler ↔ Pendler, Wien ↔ Distant, or unknown endpoints), the message
       is dropped.
    2. **Single-station / general messages** – if no explicit route can be
       parsed:

       a. When at least one *known* distant station (in stations.json with
          ``in_vienna=False`` and ``pendler=False``) is mentioned alongside
          relevant stations, the message almost always describes a
          Wien↔Distant or Pendler↔Distant route — drop it. Standalone
          "Aufzug defekt am Wien Hbf" messages never drag in München names,
          so this rule is safe in practice.
       b. Otherwise, the message must mention at least one Vienna or
          Pendler station. If only distant stations are mentioned, drop.
       c. As a final fall-back, use the generic Vienna-text heuristic for
          U-Bahn references and the like.

    Before any of the above runs, messages whose primary topic is a
    broken facility (Aufzug, Lift, Fahrtreppe, …) or a standalone weather
    warning (Sturm, Wetterlage, …) are dropped: they are explicitly out
    of scope per project spec. Mixed transit messages that merely mention
    weather/facility as cause or side-effect still go through.
    """
    if _is_facility_or_weather_only(title, description):
        return False

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

    # Step 2a: a known-distant station mention together with anything else
    # almost always implies a route into the distant station. Wien↔Distant,
    # Pendler↔Distant and Distant↔Distant routes are all not relevant per
    # spec, so drop the message even when a Wien station is co-mentioned.
    if has_distant:
        return False

    # Step 2b: same idea for stations that the directory doesn't carry yet —
    # if the title pairs a known Wien/Pendler station with a capitalized
    # token that isn't a stop word and isn't part of a known station name,
    # treat the pairing as an implicit Pendler↔Unknown / Wien↔Unknown
    # route. This catches Pendler↔Distant Fernverkehr items like
    # "Bauarbeiten: Wiener Neustadt Hauptbahnhof Semmering" until the
    # missing stations are added to data/stations.json (bug M).
    if has_relevant and _title_has_unknown_endpoint(title):
        return False

    if has_relevant:
        return True

    # OEBB_ONLY_VIENNA narrows the fallback to text-detected Vienna references.
    if OEBB_ONLY_VIENNA:
        return False

    return text_has_vienna_connection(text)

# ---------------- Region helpers ----------------
_MAX_STATION_WINDOW = 4

# ---------------- Fallback Helpers ----------------
def _extract_id_from_url(url: str) -> int | None:
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
    # German preposition that aliases to "Wien Hauptbahnhof" via several
    # "(VOR)"-suffixed entries in stations.json. Without skipping it, words
    # like "vor Reiseantritt" inside an ÖBB description silently classify
    # the message as Wien-relevant.
    "vor",
})


# Endpoint candidates whose first word matches one of these never describe
# a real transit connection — they're typical inside-station references
# ("Bahnsteig 1 und Bahnsteig 5", "Gleis 3 und Gleis 7"). Routes built from
# such candidates are dropped so the message can fall through to the
# single-station path and pick up the real station mention.
_NON_STATION_FIRST_WORDS = frozenset({
    "bahnsteig",
    "gleis",
    "steig",
    "wagen",
    "waggon",
    "abteil",
    "ausgang",
    "eingang",
    "tor",
    "tür",
    "tuer",
    "sektor",
    "zone",
    "halle",
    "platz",
    "lift",
    "aufzug",
    "rolltreppe",
    "fahrtreppe",
})


def _looks_like_facility_endpoint(name: str) -> bool:
    """Return True if *name* describes a non-station element (platform,
    track, exit, …) rather than a transit station."""
    if not name:
        return False
    first = name.strip().split(maxsplit=1)[0].casefold().rstrip(".:,;")
    return first in _NON_STATION_FIRST_WORDS


def _find_stations_in_text(blob: str) -> list[str]:
    """
    Scans text for known station names using a sliding window.
    Returns a list of unique canonical station names found.
    """
    if not blob:
        return []
    # Strip HTML tags and unescape entities — otherwise tokens like
    # "Hbf<" (left over from "<b>Graz Hbf</b>") slip through the
    # _GENERIC_STATION_TOKENS filter and canonicalise to flagship
    # stations through their alias rules.
    cleaned = re.sub(r"<[^>]+>", " ", blob)
    cleaned = html.unescape(cleaned)
    # Use whitespace splitting to preserve punctuation like '.' in 'St. Pölten'
    tokens = [t for t in re.split(r"[\s/]+", cleaned) if t]
    if not tokens:
        return []

    # Drop tokens that can never be part of a station name. The arrow
    # characters appear in route titles ("A ↔ B"), and including them in
    # sliding-window chunks let canonical_name silently expand "Hbf ↔"
    # into "Wien Hauptbahnhof" via the directory's alias-expansion rules.
    _NOISE_TOKEN_RE = re.compile(r"^[↔→←↗↘↙↖<>=–—\-«»‹›]+$")
    tokens = [t for t in tokens if not _NOISE_TOKEN_RE.match(t)]
    if not tokens:
        return []

    found = set()
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for idx in range(len(tokens) - size + 1):
            chunk_tokens = tokens[idx : idx + size]
            chunk = " ".join(chunk_tokens)
            chunk_alpha = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", chunk)
            if size == 1:
                # Skip single-token chunks that are either generic
                # aliases ("Hbf", "Bahnhof", …) or two-letter
                # abbreviations like "SG"/"NÖ" — these falsely match
                # flagship stations through the directory's alias
                # expansions.
                token_norm = chunk.casefold().rstrip(".:,;")
                if token_norm in _GENERIC_STATION_TOKENS:
                    continue
                if len(chunk_alpha) < 3:
                    continue
            canon = canonical_name(chunk)
            if canon:
                found.add(canon)

    # Filter out shorter overlapping matches
    sorted_found = sorted(list(found), key=len, reverse=True)
    filtered: list[str] = []
    for station in sorted_found:
        if not any(station in longer_station for longer_station in filtered):
            filtered.append(station)

    return sorted(filtered)

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> ET.Element | None:
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
                    header = e.response.headers.get("Retry-After")
                    parsed_delay = parse_retry_after(header)
                    # Default to 1.0s if header is missing or unparseable.
                    wait_seconds = parsed_delay if parsed_delay is not None else 1.0
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

def _get_text(elem: ET.Element | None, tag: str) -> str:
    e = elem.find(tag) if elem is not None else None
    return (e.text or "") if e is not None else ""

def _parse_dt_rfc2822(s: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
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


def _extract_line_prefix(title: str) -> tuple[str, str]:
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
_STATION_NAME_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
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


def _format_route_title(routes: list[tuple[str, str]], line_prefix: str = "") -> str:
    """Build a clean ``A ↔ B`` title from extracted route(s).

    For each route we use the canonical station name from the directory when
    available (so ``Wien Westbf`` → ``Wien Westbahnhof``). The Vienna endpoint
    is placed first to keep the feed visually consistent. Multiple routes are
    joined with ``" / "`` to indicate that several segments are affected.

    Routes that resolve to the same canonical endpoint pair (e.g. one
    description writes ``St. Pölten`` while another writes ``St.Pölten``)
    are deduplicated here — the upstream extraction keys on raw casefold
    text so whitespace variants both survive. Without this pass the
    formatted title repeats the same route twice.
    """
    if not routes:
        return ""

    formatted: list[str] = []
    seen_canon: set[tuple[str, str]] = set()
    for raw_a, raw_b in routes:
        info_a = station_info(raw_a)
        info_b = station_info(raw_b)
        name_a = info_a.name if info_a else raw_a
        name_b = info_b.name if info_b else raw_b
        # Apply feed-display overrides (also strips a trailing "(VOR)"
        # provenance suffix that is irrelevant for the user-facing title).
        name_a = display_name(name_a)
        name_b = display_name(name_b)
        name_a = _expand_station_abbreviations(name_a)
        name_b = _expand_station_abbreviations(name_b)

        # Vienna endpoint always goes first when only one side is in Vienna.
        a_in_vienna = bool(info_a and info_a.in_vienna)
        b_in_vienna = bool(info_b and info_b.in_vienna)
        if b_in_vienna and not a_in_vienna:
            name_a, name_b = name_b, name_a

        # Dedup against already-rendered routes after canonical resolution.
        canon_key: tuple[str, str] = tuple(  # type: ignore[assignment]
            sorted((name_a.casefold(), name_b.casefold()))
        )
        if canon_key in seen_canon:
            continue
        seen_canon.add(canon_key)

        formatted.append(f"{name_a} ↔ {name_b}")

    title = " / ".join(formatted)
    if line_prefix:
        title = f"{line_prefix}: {title}"
    return title


# ---------------- Public ----------------
def fetch_events(timeout: int = 25) -> list[FeedItem]:
    root = _fetch_xml(OEBB_URL, timeout=timeout)

    if root is None:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: list[FeedItem] = []
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
                    title = display_name(found_name)

            # Attempt 2: Text extraction (if still poor)
            if _is_poor_title(title):
                stations_found = _find_stations_in_text(desc)
                if len(stations_found) == 1:
                    title = display_name(stations_found[0])
                elif len(stations_found) >= 2:
                    title = (
                        f"{display_name(stations_found[0])} ↔ "
                        f"{display_name(stations_found[1])}"
                    )

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
