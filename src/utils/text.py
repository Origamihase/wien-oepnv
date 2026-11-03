#!/usr/bin/env python3
"""Text utilities.

Accent stripping, the planned-measure and station-access classifiers and
the drop labels live here; the rest is split into parts and re-exported:

* :mod:`src.utils.text_html` - HTML truncation and HTML to text,
* :mod:`src.utils.text_markdown` - Markdown-sink safety helpers,
* :mod:`src.utils.text_repair` - repair of run-together words.
"""

import re
import unicodedata

from src.utils.text_html import (
    BLOCK_END_MARK,
    PREPOSITIONS,
    VOID_ELEMENTS,
    HTMLTruncator,
    html_to_text,
    normalize_bullets,
    truncate_html,
)
from src.utils.text_markdown import (
    _MARKDOWN_NORMALISE_UNSAFE_RE,
    escape_markdown,
    escape_markdown_cell,
    normalise_markdown_text,
    safe_markdown_codespan,
)
from src.utils.text_repair import repair_glued_words, repair_saint_abbreviation

__all__ = [
    "BLOCK_END_MARK",
    "DROP_ACCESS",
    "DROP_LIFT",
    "DROP_RAMP",
    "PLANNED_DISRUPTION_RE",
    "PREPOSITIONS",
    "STATION_ACCESS_RE",
    "VOID_ELEMENTS",
    "HTMLTruncator",
    "_MARKDOWN_NORMALISE_UNSAFE_RE",
    "escape_markdown",
    "escape_markdown_cell",
    "facility_drop_label",
    "html_to_text",
    "is_station_access_only",
    "names_planned_measure",
    "normalise_markdown_text",
    "normalize_bullets",
    "repair_glued_words",
    "repair_saint_abbreviation",
    "safe_markdown_codespan",
    "strip_accents",
    "truncate_html",
]


def strip_accents(value: str) -> str:
    """*value* without diacritics: NFKD decomposition, combining marks dropped.

    The one accent stripper of the station helpers (``src/utils/stations.py``,
    ``src/places/normalize.py`` and the station scripts); ``ß`` is no accent
    and stays.
    """
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


# A disruption that is planned rather than an incident: an event, a
# demonstration, works, a state visit, a drill, a relocated stop. Its time
# line stays "Heute" — the hour it began tells a reader nothing (operator
# wish 2026-10-03). The words are the planned causes among the 2,324 WL
# disruptions cached from July to October 2026 ("Laufveranstaltung",
# "Gleisbauarbeiten", "Kranarbeiten", "Arbeiten am Stellwerk",
# "Polizeiübung", "Haltestellenverlegung", "Netzänderung" included); ÖBB
# names its planned closures "Bauarbeiten". Repairs are the consequence of
# an incident, not a plan: "Wegen Reparaturarbeiten nach einem Unfall"
# (ÖBB, 27.08.2026), "dringende Reperaturarbeiten an den Treppen" (WL,
# "U3: Betriebsstörung", 17.09.2026) keep their "Seit". ``wl_resolved`` uses it
# to tell the display tickers of planned measures from those of incidents.
PLANNED_DISRUPTION_RE: re.Pattern[str] = re.compile(
    r"veranstaltung|demonstration|kundgebung|(?<!rep[ae]ratur)arbeiten\b|staatsbesuch|übung\b"
    r"|verlegung\b|netzänderung",
    re.IGNORECASE,
)


def names_planned_measure(text: str) -> bool:
    """True when *text* names a planned measure (:data:`PLANNED_DISRUPTION_RE`)."""
    return bool(PLANNED_DISRUPTION_RE.search(text))


# A closed way into a station (Aufgang, Ausgang, Zugang, Eingang,
# Stiegen-/Treppenabgang) is treated like a broken lift: the trains still
# stop, passengers take another exit (operator decision 2026-10-09). Real
# WL titles: "U1: Nestroyplatz, Aufgangssperre ab 12.10.2026", "U1:
# Keplerplatz, Sperre eines Aufgangs", "U1: Sperre Ausgang beim
# Keplerplatz". The roots match inside compounds like the lift roots; a
# bare "abgang" is left out because "Murenabgang" is a rail closure cause.
STATION_ACCESS_RE: re.Pattern[str] = re.compile(
    r"\b\w*(?:aufg|ausg|zug|eing|stiegenabg|treppenabg)(?:a|ä|ae)ng\w*\b",
    re.IGNORECASE,
)

# What the trains themselves do. A title naming one of these is about the
# line, not only about a way into the station, and stays. Besides WL's
# measures this holds ÖBB's words for the trains (check of 2026-10-09): a
# train that fails or runs late ("Zugausfall", "fällt aus", "Verspätungen"),
# a changed timetable or offer ("Fahrplanänderung", "geändertes Zugangebot",
# where "Zugangebot" itself reads as "Zugang" plus "ebot"), single-track
# running and a closed line ("Streckensperre"). Without them "Geändertes
# Zugangebot …" or "Zugausfall ab Ausgangsbahnhof …" would have left the
# feed like a closed exit.
_LINE_MEASURE_RE: re.Pattern[str] = re.compile(
    r"\bkein(?:en)?\s+(?:halt|betrieb)\b|\bohne\s+halt\b|\bdurchf(?:ahr|ähr|aehr)\w*"
    r"|\bfahren(?:\s+\w+){0,3}\s+durch\b"
    r"|\b(?:hält|haelt|halten)\s+nicht\b|\beingestellt\b|\bumgeleitet\b"
    r"|umleitung|ersatzverkehr|unterbrech|kurz\s*ge(?:führt|fuehrt)|kurzf(?:ü|ue)hrung"
    r"|\bentf(?:ällt|aellt|allen)\b"
    r"|ausf(?:a|ä|ae)ll|\bf(?:ällt|aellt|allen)\s+aus\b|versp(?:ä|ae)t"
    r"|fahrplan|angebot|eingleisig|(?:gleis|strecken|bahn)\w*sperr",
    re.IGNORECASE,
)


def is_station_access_only(title: str) -> bool:
    """True when *title* is about a closed station access and nothing else.

    Mirrors the lift rule (title-driven: a word in the description is a
    side-mention), with one addition: a title that also names a measure
    for the line (:data:`_LINE_MEASURE_RE`, "kein Halt", "durchfahren",
    "Ersatzverkehr" …) keeps the item.
    """
    if not title or not STATION_ACCESS_RE.search(title):
        return False
    return not _LINE_MEASURE_RE.search(title)


# Why the fetch dropped a notice about station equipment, as written to
# ``data/raw/<source>/verworfen.json`` (``src/utils/raw_capture.py``). WL
# and ÖBB share them, so one cause reads the same in both files. Until
# 2026-10-10 ÖBB wrote "nicht Wien-relevant" for every drop, lifts included,
# and WL wrote "nur Aufzug/Fahrtreppe" for the folding ramps of the trains.
DROP_LIFT = "nur Aufzug/Fahrtreppe"
DROP_RAMP = "nur Klapprampe"
DROP_ACCESS = "nur Aufgang/Ausgang"


def facility_drop_label(words: list[str]) -> str:
    """Drop label for the facility words a provider's title regex matched.

    :data:`DROP_RAMP` when every word is a folding ramp ("Klapprampensperre"),
    else :data:`DROP_LIFT`.
    """
    if words and all("klapprampe" in word.casefold() for word in words):
        return DROP_RAMP
    return DROP_LIFT
