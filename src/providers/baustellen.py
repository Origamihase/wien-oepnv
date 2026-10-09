"""Relevance policy for the Stadt-Wien construction-site provider.

The upstream WFS layers (``ogdwien:BAUSTELLENLINOGD`` / ``…PKTOGD``) list
every traffic-relevant road construction site in Vienna, most of which
never touch public transport. A construction site reaches the feed only
when its own description says what changes for public transport: a stop
moved or closed, a line diverted or shortened, tram service suspended, a
rail replacement bus (operator decision 2026-10-06, "Nur mit Öffi-Folgen").

What does *not* count:

* **Nearness to a station.** Until 2026-10-06 a site within 150 m of a
  rail Bahnhof was admitted on that alone; "Rennweg 33A–37", a night lane
  closure next to S Rennweg, held places 2 to 10 for five days with
  nothing about transit in it.
* **The U-Bahn as the builder.** "Im Zuge des U-Bahnbaus", "Bauvorhaben
  U2 / U5", "Vorarbeit für die U5": road closures for the construction of
  a line, not an effect on one. Every U-Bahn mention in the source since
  May 2026 is of this kind; titled "U2/U5: …" they read like a U-Bahn
  disruption on the display.
* **Transit named as unaffected.** "in der betriebslosen Zeit der
  Straßenbahn", "nicht beeinträchtigt", "einschließlich des öffentlichen
  Verkehrs … aufrecht gehalten" — see :data:`_NO_IMPACT_RE`.
* **The referral to Wiener Linien and the operator's name** — see
  :data:`REFERRAL_BOILERPLATE_RE` and ``_OPERATOR_NAME_RE``.

The checks are literal-alternation regexes with bounded runs. No
backtracking surface.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Final

from ..utils.stations import nearest_rail_station
from ..utils.text import repair_glued_words

__all__ = [
    "DEFAULT_STATION_RADIUS_M",
    "REFERRAL_BOILERPLATE_RE",
    "is_transit_relevant",
    "mentions_oepnv",
    "names_site_street",
    "shares_address",
    "oepnv_lead",
    "relevant_station",
    "site_street",
    "transit_text",
]

# Sentence splitter for surfacing the ÖPNV-relevant sentence. Splits after
# ., ! or ? followed by whitespace — linear, no backtracking.
_SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[.!?])\s+")

# Abbreviations / ordinals whose trailing period is NOT a sentence end.
# :func:`_split_into_sentences` re-merges any fragment that ``_SENTENCE_SPLIT_RE``
# cut right after one of these — "Die Haltestelle Nr. 4351 …" and "ab 3. März
# …" must stay intact (bug b5). Unlike an uppercase-follower gate this leaves a
# genuine boundary before a lowercase- or number-initial next sentence
# splittable, so the ÖPNV sentence can still be reordered to the front.
# Anchored at end-of-fragment; the alternation is plain literals — linear.
_NO_SENTENCE_BREAK_RE: Final = re.compile(
    r"(?:"
    r"\b(?:nr|bzw|ca|usw|etc|inkl|exkl|ggf|evtl|max|min|vgl|str|pl|dr|"
    r"hausnr|mio|mrd|tel|geb|lt|bspw|abs|kfz|mind|onr)"
    # "O.Nr." (Ordnungsnummer, the house number). Without it and "ONr" the
    # stop's new address was cut in two: "… wird von Burggasse ONr.67 nach
    # Burggasse ONr. Derrechte Fahrstreifen …" (German feed, 361 times
    # 2026-07-23 to 08-04; the "69 verlegt." landed behind the next
    # sentences), the same with "Brünner Straße ONr. 31" (May 2026).
    r"|\bo\.\s?nr"
    r"|\bz\.\s?b|\bu\.\s?a|\bd\.\s?h"  # z.B. / u.a. / d.h.
    r"|\b\d{1,2}"  # day/month ordinal ("3." in "3. März")
    r")\.\s*$",
    re.IGNORECASE,
)

# Public-transport vocabulary for the text signal: a sentence that names one
# of these, and does not say transit is unaffected (:data:`_NO_IMPACT_RE`),
# says what changes for passengers (a bus/tram stop relocated, a line
# diverted). The alternation is plain literals + bounded character classes — linear, no
# catastrophic backtracking. Clear compound terms match as substrings;
# short/ambiguous tokens (bus, bim, linie, tram) require word
# boundaries so "Busch" or "Baulinie" do not false-trigger.
_OEPNV_RE: Final = re.compile(
    # Not "Haltestellenkap": the curb of a stop as a road surface ("der
    # Verkehr wird über das befahrbare Haltestellenkap geführt").
    r"haltestelle(?!nkap)"
    r"|stra[sß]+enbahn"
    r"|schienenersatz"
    r"|verkehrsmittel"
    r"|buslinie"
    r"|autobus"
    # Leading \b ONLY: matches "S-Bahn"/"S-Bahnstation" but not the substring
    # in "Verkehrsbahnhof" ("sbahn"). The U-Bahn is not in this list: in this
    # source it is always the builder (module docstring).
    r"|\bs-?bahn"
    r"|öpnv"
    # "der öffentliche Verkehr", "des öffentlichen Verkehrsmittels" — but not
    # "die öffentliche Verkehrsfläche", the city's word for the road itself
    # ("in der Mitte der öffentlichen Verkehrsfläche", Matzleinsdorfer Platz).
    r"|öffentliche[rmns]?\s+verkehr(?:s(?:mittel\w*)?)?\b"
    # "Der Busverkehr der Wiener Linien wird über den Kreisverkehr geführt"
    # (Eßlinger Hauptstraße): the bus is named in a compound.
    r"|\bbusverkehr"
    r"|\bbus(?:se)?\b"
    r"|\bbim\b"
    r"|\blinien?\b"
    r"|\btram\b",
    re.IGNORECASE,
)

# A sentence that names transit to say it is NOT affected, or names it as the
# reason for the works. Measured on every sentence of the cache history since
# May 2026 that names transit (2026-10-06): the works happen "in der
# betriebslosen Zeit der Straßenbahn" (seven sites, among them Antonsplatz,
# Simonygasse, Schottenring 11, Wipplingerstraße 13), "Der Betrieb der
# Straßenbahnlinie 49 wird … nicht beeinträchtigt" (Linzer Straße), "Der
# Fahrzeugverkehr einschließlich des öffentlichen Verkehrs kann in allen
# bestehenden Fahrrelationen aufrecht gehalten werden" (Donaufelder Straße),
# "aufgrund der Verlängerung der Straßenbahnlinie 18" (Stadionbrücke). None
# of these sentences said anything else about transit; a site with a real
# effect states it in another sentence (Linzer Straße: "Die Haltestelle …
# wird provisorisch … verlegt").
_NO_IMPACT_RE: Final = re.compile(
    r"betriebslose"
    r"|\bnicht\s+(?:beeinträchtigt|betroffen|behindert)"
    r"|in\s+allen\s+(?:bestehenden\s+)?Fahrrelationen"
    r"|\b(?:Verlängerung|Neubau|Ausbau)\w*\s+(?:der|des)\s+Straßenbahn",
    re.IGNORECASE,
)

# The Stadt-Wien referral. Roadworks descriptions carry a sentence that tells
# the reader to ask Wiener Linien about "the affected public transport", in
# three wordings that differ only in the middle::
#
#     Nähere Informationen zu den ...................... betroffenen ...
#     Nähere Informationen zur Umleitung sowie Haltestellenverlegung der ...
#     Nähere Informationen zur Haltestellenverlegungen der .................
#
# It names no line and no stop. The feed emitter drops it from the summary
# for that reason (``build_feed._format_item_content``, since 2026-09-18) —
# yet at the relevance gate the same sentence still counted as the ÖPNV
# mention: 5 of 22 cached sites reached the feed on it alone and showed the
# viewer a street name with nothing about transit underneath ("Ruthnergasse
# Kreuzung Justgasse", 151 of 338 feed revisions over seven days held such an
# item; audit 2026-09-19, F.1). One definition for both decisions: what says
# nothing about ÖPNV on the display says nothing about ÖPNV at the gate.
#
# A fourth wording has the verb in the singular ("… des betroffenen
# öffentlichen Verkehrsmittels ist der Auskunft …", Johnstraße and
# Alaudagasse, July 2026); it counted as an ÖPNV mention until 2026-10-06.
#
# Anchored on the two invariant ends; the middle varies within a bounded,
# dot-free, non-greedy run so a match can never cross a sentence boundary.
# ``\s*`` before ``öffentlichen``: the upstream text loses spaces
# ("betroffenenöffentlichen"), and that lowercase collision is the one
# :func:`repair_glued_words` cannot see.
REFERRAL_BOILERPLATE_RE: Final = re.compile(
    r"N[äa]here\s+Informationen\s+zu\w*\s+[^.]{0,80}?"
    r"betroffenen\s*öffentlichen\s+Verkehrsmittel\w*\s+(?:sind|ist)\s+der\s+Auskunft\s+"
    r"der\s+Wiener\s+Linien\b[^.]{0,40}?zu\s+entnehmen\.?\s*",
    re.IGNORECASE,
)

#: Default proximity (in metres) between a construction site and a rail
#: Bahnhof for the Bahnhof to be named in front of the title (since
#: 2026-10-06 only that; it no longer admits a site). 150 m mirrors the
#: project's existing "effectively at the station" threshold
#: (:data:`src.utils.geo.STATION_DRIFT_TOLERANCE_METERS`).
DEFAULT_STATION_RADIUS_M: Final = 150.0

# Operator override bounds (``BAUSTELLEN_STATION_RADIUS_M``). The upper bound
# keeps a far station from being named; the lower bound keeps the match
# meaningful over GPS jitter.
_MIN_STATION_RADIUS_M: Final = 25.0
_MAX_STATION_RADIUS_M: Final = 2_000.0

_RADIUS_ENV: Final = "BAUSTELLEN_STATION_RADIUS_M"


def _resolve_radius_m() -> float:
    """Return the proximity radius, honouring the clamped env override."""

    raw = os.getenv(_RADIUS_ENV, "")
    if not raw.strip():
        return DEFAULT_STATION_RADIUS_M
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STATION_RADIUS_M
    if not math.isfinite(value):
        return DEFAULT_STATION_RADIUS_M
    return min(max(value, _MIN_STATION_RADIUS_M), _MAX_STATION_RADIUS_M)


def relevant_station(location: Any, *, radius_m: float | None = None) -> str | None:
    """Return the rail Bahnhof a construction ``location`` is tied to.

    ``location`` is the provider's location mapping, shaped
    ``{"coordinates": {"lat": ..., "lon": ...}, ...}``. Anything without
    a usable coordinate pair is treated as not relevant (fail closed) and
    yields ``None``.
    """

    if not isinstance(location, dict):
        return None
    coordinates = location.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    radius = _resolve_radius_m() if radius_m is None else radius_m
    match = nearest_rail_station(coordinates.get("lat"), coordinates.get("lon"), radius)
    return match[0] if match else None


# The operator's name is not a mention of a line: "Abdichtungsarbeiten der
# Wiener Linien GmbH & Co KG an der Decke des U-Bahn-Bauwerks" (Favoritenstraße,
# a pedestrian zone) and "im Schatten der Gleisbauarbeiten der Wiener Linien"
# (Aumannplatz) reached the feed on ``\blinien?\b`` alone. Where the text does
# name an impact it says so in its own words ("Die Buslinie 7A wird …").
_OPERATOR_NAME_RE: Final = re.compile(
    r"\bWiener\s+Linien\b(?:\s+GmbH(?:\s*&\s*Co\.?\s*KG)?)?", re.IGNORECASE
)


def transit_text(text: str) -> str:
    """Return ``text`` as the ÖPNV vocabulary check should see it.

    Glued words are repaired first so the referral's run-together spelling
    ("NähereInformationen") is recognised, then the referral and the
    operator's name are removed. What remains is the part of the
    description that can name a line, a stop or a mode.
    """

    if not text:
        return ""
    text = REFERRAL_BOILERPLATE_RE.sub("", repair_glued_words(text))
    return _OPERATOR_NAME_RE.sub("", text)


def mentions_oepnv(text: str) -> bool:
    """Return ``True`` if ``text`` says what changes for public transport.

    Sentence by sentence: one that names a stop, a line, a bus, a tram, the
    S-Bahn or a rail replacement and does not say transit is unaffected
    (:data:`_NO_IMPACT_RE`). The referral to Wiener Linien and the
    operator's name are removed first (:func:`transit_text`).
    """

    return any(_sentence_names_impact(s) for s in _split_into_sentences(transit_text(text)))


def _sentence_names_impact(sentence: str) -> bool:
    return bool(_OEPNV_RE.search(sentence)) and not _NO_IMPACT_RE.search(sentence)


def _split_into_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences, re-merging fragments that the linear
    splitter cut at a German abbreviation or a day/month ordinal so they
    stay intact (bug b5). No uppercase-follower gate, so a genuine boundary
    before a lowercase- or number-initial next sentence is still split."""
    fragments = _SENTENCE_SPLIT_RE.split(text)
    merged: list[str] = []
    for fragment in fragments:
        if merged and _NO_SENTENCE_BREAK_RE.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {fragment}"
        else:
            merged.append(fragment)
    return merged


def oepnv_lead(text: str) -> str:
    """Reorder ``text`` so the first sentence that mentions public transport
    comes first (remaining sentences keep their order).

    The construction feed entries are truncated for display, so the ÖPNV
    impact ("Bus X umgeleitet", "Haltestelle Y verlegt") must lead or it is
    cut off. Returns the text unchanged if no sentence matches or it already
    leads. The Stadt-Wien referral never leads: it says nothing, and the
    emitter drops it from the summary anyway — letting it take sentence one
    would push the sentence that does say something behind the truncation.
    """

    if not text:
        return text
    sentences = _split_into_sentences(text.strip())
    for index, sentence in enumerate(sentences):
        if mentions_oepnv(sentence):
            if index == 0:
                return text
            reordered = [sentences[index], *sentences[:index], *sentences[index + 1 :]]
            return " ".join(reordered)
    return text


def is_transit_relevant(item: Any) -> bool:
    """Return ``True`` if a construction ``item`` reaches the feed.

    Its title or description must say what changes for public transport
    (:func:`mentions_oepnv`); nearness to a station alone does not count
    (module docstring). ``item`` is the provider's event mapping
    (``title`` + ``description``). Non-dict input is treated as not
    relevant (fail closed).
    """

    if not isinstance(item, dict):
        return False
    text = f"{item.get('title') or ''}. {item.get('description') or ''}"
    return mentions_oepnv(text)


# The street a site lies on: the head of the city's title, up to the section
# ("Neilreichgasse von Gudrunstraße bis Davidgasse", "Inzersdorfer Straße
# Kreuzung Leibnizgasse", "Universitätsring und Schottenring von …") or the
# house number ("Burggasse 67"). A station prefix the feed put in front
# ("Wien Hetzendorf: …") is not part of it. Plain literals and one lazy run up
# to a fixed set of separators — linear.
_SITE_STREET_RE: Final = re.compile(
    r"^\s*(?P<street>[^\d,(]+?)"
    r"(?=\s+(?:von|Kreuzung|bis|zwischen|und|ggü\.?|gegenüber|vor|nach|unter|im|in|beim?)\s"
    r"|\s+(?:O\.\s?Nr|ONr|Nr)\.|\s*\d|\s*,|\s*\(|\s*$)",
)


def site_street(title: str) -> str | None:
    """Return the street a construction site lies on, from its title.

    ``None`` when the title does not start with a street name of at least
    two words' worth of letters (a bare "A4" or an empty title).
    """

    if not title:
        return None
    head = title.rsplit(": ", 1)[-1]
    match = _SITE_STREET_RE.match(head)
    if match is None:
        return None
    street = match.group("street").strip()
    return street if len(street) >= 5 else None


# The sentence a WL notice gives its cause in: "Wegen Bauarbeiten im Bereich
# Neilreichgasse # Davidgasse wird die Linie 7A umgeleitet.", "Aufgrund von
# Bauarbeiten in der Maxingstraße …", "Wegen der Sanierung der Floridsdorfer
# Brücke …". Up to the first full stop after it; bounded, no nesting.
_WL_CAUSE_SENTENCE_RE: Final = re.compile(r"\b(?:Wegen|Aufgrund)\b[^.]{0,300}")

# An address: a street name (up to three words, the last one ending in a street
# word) and a house number, with the city's "ONr." / "ON" / "Nr." in between
# or not ("Burggasse ONr. 69", "Nach: Burggasse 69", "Landstraßer Hauptstraße
# 140-142"; only the first number of a span counts).
_ADDRESS_RE: Final = re.compile(
    r"((?:[A-ZÄÖÜ][\wäöüß.-]*\s+){0,2}[A-ZÄÖÜ][\wäöüß-]*"
    r"(?:gasse|straße|platz|weg|ring|kai|brücke|allee|gürtel|zeile|steig|ufer|lände))"
    r"\s+(?:(?:O\.\s?Nr|ONr|Nr|ON)\.?\s*)?(\d{1,4})(?!\d)"
)


def _addresses(text: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2)) for m in _ADDRESS_RE.finditer(text)}


def _same_address(a: tuple[str, str], b: tuple[str, str]) -> bool:
    # "Haltestelle Burggasse 69" and "Burggasse 69" are one address.
    return a[1] == b[1] and (a[0].endswith(b[0]) or b[0].endswith(a[0]))


def names_site_street(text: str, street: str) -> bool:
    """Return ``True`` if a WL notice's cause sentence places it on ``street``.

    The sentence WL opens a planned measure with names where the works are:
    "Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse …", "Aufgrund
    von Bauarbeiten in der Maxingstraße …". A street that appears only in a
    detour route ("über Neilreichgasse – Troststraße"), a list of
    provisional stops ("Herzgasse (Neilreichgasse 56)") or a stop name
    ("Richtung Burggasse") does not count: long streets carry several
    measures at once (Burggasse: 48A St.-Ulrichs-Platz, a flea-market detour
    of the 13A and the stop at Burggasse 69, summer 2026).
    """

    if not text or not street:
        return False
    cause = _WL_CAUSE_SENTENCE_RE.search(text)
    if cause is None:
        return False
    return re.search(r"\b" + re.escape(street) + r"\b", cause.group(0)) is not None


def shares_address(site_text: str, notice_text: str) -> bool:
    """Return ``True`` if both texts name the same street address.

    The city writes where a stop goes ("von Burggasse ONr.67 nach Burggasse
    ONr. 69"), WL writes the same stop ("Nach: Burggasse 69"). Same street and
    same house number; a neighbouring stop on the same street (48A
    "St.-Ulrichs-Platz", Burggasse 25 to 27) is not the same.
    """

    site = _addresses(site_text)
    if not site:
        return False
    notice = _addresses(notice_text)
    return any(_same_address(a, b) for a in site for b in notice)
