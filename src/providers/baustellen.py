"""Relevance policy for the Stadt-Wien construction-site provider.

The upstream WFS feed (``ogdwien:BAUSTELLEOGD``) lists *every* road
construction site in Vienna — the overwhelming majority of which never
touch public transport. To keep the feed a focused ÖPNV signal we admit
a construction site only when it sits at (or right next to) a rail
*Bahnhof*: a Wien station or a Pendlerbahnhof from the curated station
directory. A lane closure on the forecourt of Wien Floridsdorf is worth
surfacing; one in a back courtyard 2 km from any station is not.

The decision is purely geographic — it compares the construction site's
coordinate against the rail-station coordinates already maintained in
``data/stations.json`` (see :func:`src.utils.stations.nearest_rail_station`).
There is no free-text matching, so there is no ReDoS surface and no
ambiguity from street names that merely echo a station name.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Final

from ..utils.stations import nearest_rail_station

__all__ = [
    "DEFAULT_STATION_RADIUS_M",
    "is_transit_relevant",
    "mentions_oepnv",
    "oepnv_lead",
    "relevant_station",
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
    r"|u-?bahn"
    r"|s-?bahn"
    r"|öpnv"
    r"|öffentliche[rn]?\s+verkehr"
    r"|\bbus(?:se)?\b"
    r"|\bbim\b"
    r"|\blinien?\b"
    r"|\bu[1-6]\b"
    r"|\btram\b",
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


def mentions_oepnv(text: str) -> bool:
    """Return ``True`` if ``text`` mentions public transport (a stop, line,
    bus, tram/Bim, U-/S-Bahn, …)."""

    return bool(_OEPNV_RE.search(text or ""))


def u_bahn_lines(text: str) -> list[str]:
    """Return the sorted, de-duplicated U-Bahn line labels (``U1``–``U6``)
    named in ``text`` — e.g. ``["U2", "U5"]``; empty if none."""

    return sorted({f"U{digit}" for digit in _UBAHN_RE.findall(text or "")})


def oepnv_lead(text: str) -> str:
    """Reorder ``text`` so the first sentence that mentions public transport
    comes first (remaining sentences keep their order).

    The construction feed entries are truncated for display, so the ÖPNV
    impact ("Bus X umgeleitet", "Haltestelle Y verlegt") must lead or it is
    cut off. Returns the text unchanged if no sentence matches or it already
    leads.
    """

    if not text:
        return text
    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    for index, sentence in enumerate(sentences):
        if _OEPNV_RE.search(sentence):
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
