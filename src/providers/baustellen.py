"""Relevance policy for the Stadt-Wien construction-site provider.

The upstream WFS feed (``ogdwien:BAUSTELLEOGD``) lists *every* road
construction site in Vienna — the overwhelming majority of which never
touch public transport. To keep the feed a focused ÖPNV signal we admit
a construction site on one of two grounds:

* **Geographic** — it sits within a small radius of a rail *Bahnhof*: a
  Wien station or a Pendlerbahnhof from the curated station directory
  (see :func:`src.utils.stations.nearest_rail_station`). A lane closure
  on the forecourt of Wien Floridsdorf is worth surfacing; one in a back
  courtyard 2 km from any station is not.
* **Textual** — its own description names public transport: a stop, a
  line, a bus, a tram, the U-/S-Bahn. The Stadt-Wien referral sentence
  ("Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel
  sind der Auskunft der Wiener Linien … zu entnehmen") is *not* such a
  mention — it names nothing, and the feed does not display it either
  (see :data:`REFERRAL_BOILERPLATE_RE`).

Both checks are linear: a coordinate scan over ~160 stations and a
literal-alternation regex with bounded runs. No backtracking surface.
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
    "oepnv_lead",
    "relevant_station",
    "transit_text",
    "u_bahn_lines",
]

# U-Bahn line labels (U1–U6) are the one line identifier that can be pulled
# from the free text reliably — unambiguous token, no negation traps, and
# it marks the marquee projects. Bus/tram line *numbers* are intentionally
# NOT extracted: the source text negates them ("Linie 49 nicht
# beeinträchtigt"), reuses the operator name ("Wiener Linien") and is full
# of house numbers — extracting them would mislabel entries.
_UBAHN_RE: Final = re.compile(r"\bu([1-6])\b", re.IGNORECASE)

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
    r"hausnr|mio|mrd|tel|geb|lt|bspw|abs|kfz)"
    r"|\bz\.\s?b|\bu\.\s?a|\bd\.\s?h"  # z.B. / u.a. / d.h.
    r"|\b\d{1,2}"  # day/month ordinal ("3." in "3. März")
    r")\.\s*$",
    re.IGNORECASE,
)

# Public-transport vocabulary for the text signal. A construction site whose
# title/description mentions any of these affects ÖPNV even when it is not
# right next to a rail Bahnhof (e.g. a bus/tram stop being relocated). The
# alternation is plain literals + bounded character classes — linear, no
# catastrophic backtracking. Clear compound terms match as substrings;
# short/ambiguous tokens (bus, bim, linie, U1–U6, tram) require word
# boundaries so "Busch" or "Baulinie" do not false-trigger.
_OEPNV_RE: Final = re.compile(
    r"haltestelle"
    r"|stra[sß]+enbahn"
    r"|schienenersatz"
    r"|verkehrsmittel"
    r"|buslinie"
    r"|autobus"
    # Leading \b ONLY: matches "U-Bahn"/"S-Bahn"/"U-Bahnbau"/"U-Bahnstation"
    # but not the substrings in "Hochschaubahn" ("ubahn") or
    # "Verkehrsbahnhof" ("sbahn"). A TRAILING \b after "bahn" would wrongly
    # drop the compound forms (no boundary between "bahn" and "bau") — bug b4.
    r"|\bu-?bahn"
    r"|\bs-?bahn"
    r"|öpnv"
    r"|öffentliche[rn]?\s+verkehr"
    r"|\bbus(?:se)?\b"
    r"|\bbim\b"
    r"|\blinien?\b"
    r"|\bu[1-6]\b"
    r"|\btram\b",
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
# Anchored on the two invariant ends; the middle varies within a bounded,
# dot-free, non-greedy run so a match can never cross a sentence boundary.
# ``\s*`` before ``öffentlichen``: the upstream text loses spaces
# ("betroffenenöffentlichen"), and that lowercase collision is the one
# :func:`repair_glued_words` cannot see.
REFERRAL_BOILERPLATE_RE: Final = re.compile(
    r"N[äa]here\s+Informationen\s+zu\w*\s+[^.]{0,80}?"
    r"betroffenen\s*öffentlichen\s+Verkehrsmittel\w*\s+sind\s+der\s+Auskunft\s+"
    r"der\s+Wiener\s+Linien\b[^.]{0,40}?zu\s+entnehmen\.?\s*",
    re.IGNORECASE,
)

#: Default proximity (in metres) between a construction site and a rail
#: Bahnhof for the site to count as ÖPNV-relevant. 150 m mirrors the
#: project's existing "effectively at the station" threshold
#: (:data:`src.utils.geo.STATION_DRIFT_TOLERANCE_METERS`): a closure
#: within 150 m of a Bahnhof plausibly affects access to it, while the
#: tight radius keeps unrelated road works out of the feed.
DEFAULT_STATION_RADIUS_M: Final = 150.0

# Operator override bounds. The upper bound stops anyone widening the
# radius until the filter re-floods the feed it exists to protect; the
# lower bound keeps the match meaningful (a sub-25 m radius would drop
# legitimate forecourt closures over GPS jitter alone).
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


def transit_text(text: str) -> str:
    """Return ``text`` as the ÖPNV vocabulary check should see it.

    Glued words are repaired first so the referral's run-together spelling
    ("NähereInformationen") is recognised, then the referral is removed.
    What remains is the part of the description that can name a line, a
    stop or a mode.
    """

    if not text:
        return ""
    return REFERRAL_BOILERPLATE_RE.sub("", repair_glued_words(text))


def mentions_oepnv(text: str) -> bool:
    """Return ``True`` if ``text`` mentions public transport (a stop, line,
    bus, tram/Bim, U-/S-Bahn, …) in its own words.

    The Stadt-Wien referral to Wiener Linien does not count: it is removed
    before the vocabulary check (see :data:`REFERRAL_BOILERPLATE_RE`).
    """

    return bool(_OEPNV_RE.search(transit_text(text)))


def u_bahn_lines(text: str) -> list[str]:
    """Return the sorted, de-duplicated U-Bahn line labels (``U1``–``U6``)
    named in ``text`` — e.g. ``["U2", "U5"]``; empty if none."""

    return sorted({f"U{digit}" for digit in _UBAHN_RE.findall(text or "")})


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


def is_transit_relevant(item: Any, *, radius_m: float | None = None) -> bool:
    """Return ``True`` if a construction ``item`` is ÖPNV-relevant.

    Relevance is geographic **or** textual: the site sits within the
    configured radius of a rail Bahnhof (Wien station or Pendlerbahnhof),
    OR its title/description names public transport. ``item`` is the
    provider's event mapping (``location`` + ``title`` + ``description``).
    Non-dict input is treated as not relevant (fail closed).
    """

    if not isinstance(item, dict):
        return False
    if relevant_station(item.get("location"), radius_m=radius_m) is not None:
        return True
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    return mentions_oepnv(text)
