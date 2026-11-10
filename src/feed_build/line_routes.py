"""Fixed English for "über die Strecke der Linien 9 und 44".

WL names the route a diverted line takes by the lines that normally run
there: "Umleitung … zwischen Rosensteingasse und Brünnlbadgasse über die
Strecke der Linien 9 und 44", "über Michelbeuern-AKH U (Strecke Linien 42 und
9)". Both nouns are "line" to the model, so the EN feed doubled the word
(KI-Wächter, issue #2188, and the runs of 27.10. and 30.10.)::

    … over the line line 43 and 9.
    … over the line line 2 …
    … via Michelbeuern-AKH U (line lines 42 and 9).

The same German came back as "over lines 9 and 44", "along lines 2 and 5",
"over line 44 and 2" or "over the line 9 and 43" in other builds: the
rendering depends on the run, not on the text. Of the 13 English texts
the feed published with the phrase from 15.06. to 10.11.2026, 6 doubled the
word. Every German one has the same structure: an optional "über die", the
noun "Strecke(n)", an optional "der", the noun "Linie(n)" and a list of line
codes.

The phrase is rendered here from its parts, before the model sees the text,
and the model gets one glossary placeholder for the noun phrase, article and
line codes included: "die Strecke der Linien 43 und 9" becomes "the route of
lines 43 and 9", "(Strecke Linien O und 18)" becomes "(route of lines O and
18)"; the number of each German noun decides "route(s)" and "line(s)". The
preposition in front stays with the model ("über" → "over", "via"), so the
phrase keeps its place in the sentence: with "über die" inside the
placeholder the model moved it in front of the verb ("line N46 via line 48A
is redirected"). Without a line code after the noun nothing changes.
"""

from __future__ import annotations

import re

from .placeholders import _GLOSSARY_PLACEHOLDER_FORMAT

__all__ = ["_LINE_ROUTE_RE", "_line_route_en", "_mask_line_routes", "_line_route_defect"]


# A WL/ÖBB line code as WL writes it in a list: U-Bahn, S-Bahn, tram, bus,
# night bus, and the trams D and O (their single letter is a line here,
# because the noun "Linie" stands right in front of the list).
_LINE_CODE = r"(?:U[1-6][A-Z]?|S[1-9]\d?[A-Z]?|N?[1-9]\d{0,2}[A-Z]{0,2}|[DO])"
_LINE_LIST_SEPARATOR = r"(?:, | und | bzw\. | sowie |/)"

_LINE_ROUTE_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)(?P<article>[Dd](?:ie|er|en) )?"
    r"(?P<route>Strecken?) (?:der )?(?P<noun>Linien?) "
    rf"(?P<codes>{_LINE_CODE}(?:{_LINE_LIST_SEPARATOR}{_LINE_CODE})*)(?!\w)"
)

_SEPARATOR_EN = {" und ": " and ", " bzw. ": " or ", " sowie ": " and "}


def _line_route_en(match: re.Match[str]) -> str:
    """English for one route phrase: ``the route of lines 9 and 44``."""
    codes = re.sub(
        _LINE_LIST_SEPARATOR,
        lambda sep: _SEPARATOR_EN.get(sep.group(0), sep.group(0)),
        match["codes"],
    )
    noun = "lines" if match["noun"] == "Linien" else "line"
    route = "routes" if match["route"] == "Strecken" else "route"
    english = f"{route} of {noun} {codes}"
    article = match["article"]
    if article:
        # "Die Strecke der Linie 5 …" opens a sentence; the English does too.
        english = ("The " if article[:1].isupper() else "the ") + english
    return english


def _mask_line_routes(text: str, mapping: dict[str, str]) -> str:
    """Replace each route phrase of *text* by a glossary placeholder.

    Runs after :func:`_apply_domain_glossary` and adds to its *mapping*, so
    the placeholders share its format and numbering.
    """
    if not text or "Strecke" not in text:
        return text
    surface_to_placeholder: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        surface = match.group(0)
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        placeholder = _GLOSSARY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = _line_route_en(match)
        surface_to_placeholder[surface] = placeholder
        return placeholder

    return _LINE_ROUTE_RE.sub(_replace, text)


def _line_route_defect(source_text: str, cached: str) -> str | None:
    """``"line route"`` when *cached* lacks a route phrase's fixed English.

    A value cached before 10.11.2026 may read "over the line line 43 and 9";
    only such an item is translated again, not every item, as an epoch move
    would.
    """
    if "Strecke" not in source_text:
        return None
    for match in _LINE_ROUTE_RE.finditer(source_text):
        if _line_route_en(match).casefold() not in cached.casefold():
            return "line route"
    return None
