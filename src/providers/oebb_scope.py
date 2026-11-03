"""ÖBB notices outside the feed's scope: facility-only and pure weather warnings.

Moved verbatim from ``src/providers/oebb.py`` (700-line limit); the facade
``src.providers.oebb`` re-exports the names other code uses.
"""

from __future__ import annotations

import re

from ..utils.text import DROP_ACCESS, facility_drop_label, is_station_access_only


# Topics the user explicitly excludes from the feed: facility-only
# notices (broken elevators, escalators) and standalone weather warnings.
# A message is treated as such when its title carries one of these
# keywords AND none of the "real" transit-disruption keywords below.
_FACILITY_KEYWORD_RE = re.compile(
    # Match the facility root anywhere inside a German compound word —
    # the ÖBB feed uses ``Personenlift``, ``Aufzugsstörung``,
    # ``Liftanlage`` etc. as compound nouns. Word boundaries on both
    # sides would only catch the bare-root form (``Aufzug``, ``Lift``)
    # and miss real cache items like
    # ``Technische Störung des Personenlift in Stockerau: Bahnsteig 1/2``.
    # The optional surrounding ``\w*`` lets the regex match any
    # German prefix/suffix without enumerating every variant.
    r"\b\w*(?:aufzug|aufz(?:ü|ue)ge|aufzugsinfo|lift|fahrstuhl|"
    r"fahrtreppen?(?:info)?|rolltreppen?|klapprampen?)\w*\b",
    re.IGNORECASE,
)
# Label in ``data/raw/oebb/verworfen.json`` for a standalone weather warning
# and for a message the Wien filter (``_is_relevant``) dropped.
DROP_WEATHER = "nur Wetterwarnung"
DROP_NOT_VIENNA = "nicht Wien-relevant"

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
    r"verspätung\w{0,4}|verspaetung\w{0,4}|"
    r"zugverspätung\w{0,4}|zugverspaetung\w{0,4}|"
    # Rail-specific compound closures (Gleissperrung, Streckensperre,
    # Bahnsperre, …). The plain ``sperrung``/``sperre`` stems below carry
    # a leading ``\b`` from the group anchor, so without this rail-prefixed
    # form a weather title like "Hochwasser: Gleissperrung Wien Floridsdorf"
    # was wrongly dropped (no transit signal recognised). Bounded to the
    # gleis/strecken/bahn prefixes so a road "Vollsperrung" stays out.
    r"(?:gleis|strecken|bahn)\w*(?:sperrung|sperre)\w{0,4}|"
    r"sperre\w{0,4}|sperrung\w{0,4}|"
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

    True when :func:`_facility_or_weather_reason` names a reason.
    """
    return _facility_or_weather_reason(title, description) is not None


def _facility_or_weather_reason(title: str, description: str) -> str | None:
    """Why the message has no place in the Wien-ÖPNV feed, else ``None``.

    The reason is the label in ``data/raw/oebb/verworfen.json``, the same
    as WL's for the same cause (``src/utils/text.py``).

    Per project spec elevator/escalator notices have nothing to do in
    the feed — including titles that combine "Bauarbeiten" with
    "Aufzug betroffen", because the actual subject is still the broken
    elevator and not a service-affecting track disruption. Any mention
    of a facility keyword in the title therefore drops the message, and
    so does a closed station access (``is_station_access_only``: the
    trains still stop) unless the title also names a measure for the line.

    Weather titles are dropped only when the title doesn't also carry a
    real disruption keyword: ``Sturmschaden: Strecke Wien-Mödling
    gesperrt`` describes a genuine transit interruption (cause: Sturm)
    and stays in the feed, while ``Sturm im Raum Wien`` / ``Wetterlage
    Wien`` are pure weather warnings and drop.
    """
    if not title:
        return None
    title_low = title.lower()
    facility_words = _FACILITY_KEYWORD_RE.findall(title_low)
    if facility_words:
        # Strict: any facility-keyword title drops, with or without an
        # accompanying transit keyword. Side-mentions of "Aufzug" should
        # never reach the feed per user spec.
        return facility_drop_label(facility_words)
    if is_station_access_only(title):
        # A closed Aufgang/Ausgang counts like a broken lift (2026-10-09).
        return DROP_ACCESS
    has_weather = bool(_WEATHER_KEYWORD_RE.search(title_low))
    if not has_weather:
        return None
    # Weather: drop only when the title has no real disruption signal.
    return None if _TRANSIT_KEYWORD_RE.search(title_low) else DROP_WEATHER
