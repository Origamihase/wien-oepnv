"""Post-filters of the WL, ÖBB and Baustellen caches before the feed build.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..providers.wl_lines import line_order_key
from ..providers.wl_title_context import INCOMPLETE_TITLE_TAIL_RE

from .display_titles import _repair_baustellen_title
from .wl_measures import (
    _collapse_repeated_wl_line_segments, _strip_wl_description_line_prefix, _tidy_wl_dangling_location,
)

__all__ = [
    "_attribute_obb_replacement_bus", "_post_filter_wl", "_post_filter_oebb", "_post_filter_baustellen",
]


# German prepositions/connectors that *require* a following object —
# when a WL title ends with one of these alone (no object), the WL
# source data was truncated mid-sentence and the meldung is useless.
# Real cache item ``41E: Ersatzbus 41E hält gegenüber`` is the
# canonical example — the user reads "stops opposite [nothing]". The
# pattern lives in ``src/providers/wl_title_context.py`` since 2026-11-07:
# the WL fetch uses it too, so that such a title never names a bucket that
# also holds a complete one (``_title_quality_key``).
_INCOMPLETE_TITLE_TAIL_RE = INCOMPLETE_TITLE_TAIL_RE


# WL's display boards show the S-Bahn logo as a glyph that reaches the data as
# "<": "Bhf. Hütteldorf / ÖBB-Ersatzbus für <80", published daily since
# 2026-09-12 with ``relatedLines`` "1". Tram line 1 does not serve Hütteldorf
# (operator, 2026-09-25); an ÖBB replacement bus replaces an ÖBB line, and 80
# is the S80, which starts at Hütteldorf. Only Vienna's S-Bahn numbers count.
_VIENNA_S_BAHN_NUMBERS: frozenset[str] = frozenset(
    {"1", "2", "3", "4", "7", "40", "45", "50", "60", "80"}
)
_OBB_REPLACEMENT_BUS_RE = re.compile(r"ÖBB-Ersatzbus\s+für\s+(?:<\s*|S\s?)?(\d{1,2})\b")


def _attribute_obb_replacement_bus(item: dict[str, Any]) -> dict[str, Any]:
    """``1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80`` → ``S80: ÖBB-Ersatzbus``.

    The title takes the S-Bahn line the text names instead of WL's line and
    says what it is; the place goes into the description (operator decision
    2026-09-25): ``S80: ÖBB-Ersatzbus`` over ``Bhf. Hütteldorf``. Whatever
    else WL's description says stays; only the "ÖBB-Ersatzbus für …" phrase
    the title now carries is taken out of it.
    """
    title = item.get("title")
    if not isinstance(title, str):
        return item
    desc = item.get("description")
    desc_text = desc if isinstance(desc, str) else ""
    match = _OBB_REPLACEMENT_BUS_RE.search(f"{title}\n{desc_text}")
    if match is None or match.group(1) not in _VIENNA_S_BAHN_NUMBERS:
        return item
    body = title.split(":", 1)[1] if ":" in title else title
    place = body.split("ÖBB-Ersatzbus", 1)[0].strip(" -–:/")
    rest = re.sub(r"\s+", " ", _OBB_REPLACEMENT_BUS_RE.sub(" ", desc_text)).strip(" -–:/")
    fixed = dict(item)
    fixed["title"] = f"S{match.group(1)}: ÖBB-Ersatzbus"
    fixed["description"] = rest or place
    return fixed


# A WL notice ("Hinweis") whose description holds no text: WL put "5B:
# Bauarbeiten" online on 2026-11-06 at 13:59 with the description "<p></p>"
# and filled it at 14:11; the 14:01 feed showed the title with only the time
# line ("[Seit heute]", KI-Wächter, issue #2170). Operator decision
# 2026-11-07 ("Zurückhalten bis Text da ist"): such a notice waits until WL
# fills it; the next fetch with text brings it in. Notices rank below
# disruptions, and their title ("Bauarbeiten") does not say what changes for
# the passenger. A disruption without text stays: its title names line and
# cause ("U2: Betriebsstörung"); WL sent six of those since 2026-06-01, each
# for one or a few fetches. Measured over every WL cache version since
# 2026-06-01 (6 328 versions, 337 notices): the 5B notice is the only empty one.
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _is_empty_wl_notice(item: dict[str, Any]) -> bool:
    """``True`` for a WL "Hinweis" whose description has no visible text
    (empty, or only tags, ``&nbsp;`` and whitespace).

    The WL provider always writes ``description`` as a string; an item
    without one (test fixtures, a hand-built dictionary) is not judged.
    """
    if item.get("category") != "Hinweis":
        return False
    desc = item.get("description")
    if not isinstance(desc, str):
        return False
    text = html.unescape(_HTML_TAG_RE.sub(" ", desc))
    return not text.replace("\xa0", " ").strip()


def _post_filter_wl(items: list[Any]) -> list[Any]:
    """Defence-in-depth: normalise / drop bad WL items loaded from cache.

    The WL cache is only refreshed periodically, so:

    1. Title-formatting fixes (e.g. newline collapse per Bug 12A) need
       to be re-applied at cache-read time.
    2. WL itself sometimes serves data that's truncated mid-sentence —
       ``Ersatzbus 41E hält gegenüber`` (with no location after
       ``gegenüber``) is meaningless. Such items are dropped.
    3. WL Störung items occasionally arrive without any
       ``relatedLines`` AND without a line code in the title — for
       example ``Verkehrsunfall Betrieb ab Nordbrücke``. The line
       prefix is the key signal for transit-line attribution; without
       it the user can't tell which line is affected, so we drop
       these too.
    3a. A WL notice without any text waits until WL fills it
       (:func:`_is_empty_wl_notice`, operator decision 2026-11-07).
    4. Cached items can carry a stacked / non-canonical line prefix
       (``40: 40+41: Betrieb ab Gersthof``) when an older
       ``_ensure_line_prefix`` prepended ``relatedLines`` on top of the
       title's own ``40+41:`` block. The re-parse via
       ``_extract_prefix_lines`` unions the layers and re-emits a
       canonical ``40/41: …`` prefix so the user immediately sees
       which lines are affected.
    """
    from ..providers.wl_lines import (
        _extract_prefix_lines,
        _strip_redundant_line_token,
    )

    out: list[Any] = []
    for original in items:
        if not isinstance(original, dict):
            out.append(original)
            continue
        if _is_empty_wl_notice(original):
            continue
        item = _attribute_obb_replacement_bus(original)
        prefix_lines: list[str] = []
        title = item.get("title")
        if isinstance(title, str) and title:
            cleaned = re.sub(r"\s+", " ", title).strip()
            # Rebuild a stacked or non-canonical line prefix (e.g.
            # ``40: 40+41: Betrieb ab Gersthof`` →
            # ``40/41: Betrieb ab Gersthof``). Only touches items where
            # ``_extract_prefix_lines`` recovers ≥1 line tokens AND the
            # rebuilt form differs from the cached title — leaves bodies
            # without a prefix block untouched. The lines take WL's order
            # (``line_order_key``) like every other prefix, also in a title
            # cached before that order (``41: 40+41:`` → ``40/41:``).
            body, prefix_lines = _extract_prefix_lines(cleaned)
            if prefix_lines and body:
                canonical = "/".join(sorted(prefix_lines, key=line_order_key))
                rebuilt = f"{canonical}: {body}"
                if rebuilt != cleaned:
                    cleaned = rebuilt
            if cleaned != title:
                item = dict(item)
                item["title"] = cleaned
            # Drop items whose visible title ends with a preposition
            # that demands an object — the WL source is clearly
            # incomplete and the user gets no useful information.
            title_body = cleaned.split(":", 1)[-1].strip() if ":" in cleaned else cleaned
            if _INCOMPLETE_TITLE_TAIL_RE.search(title_body):
                continue
            # Drop WL Störung items without a recognisable line
            # prefix — WL didn't provide a line code and the user
            # can't disambiguate the affected line from the title
            # alone. ``prefix_lines`` was computed via
            # :func:`_extract_prefix_lines` above (with the strict
            # line-token gate) so a generic German word prefix like
            # ``Einstieg: Brünner Straße 31-31A`` or
            # ``Sperre Bahnsteig Richtung Siebenhirten`` (both real
            # cache items) is correctly classified as "no line
            # prefix" and dropped.
            category = item.get("category")
            if category == "Störung" and not prefix_lines:
                continue
        # Strip a redundant ``Linie 40:`` / ``40+41:`` prefix from the
        # description — the title already attributes the line(s).
        desc = item.get("description")
        if isinstance(desc, str) and desc:
            # Collapse repeated ``Linie X:`` segments BEFORE the leading
            # prefix is stripped — the comparison needs every segment to
            # still carry its own prefix.
            stripped = _tidy_wl_dangling_location(
                _strip_wl_description_line_prefix(
                    _collapse_repeated_wl_line_segments(desc)
                )
            )
            # Störung descriptions repeat the line code without the colon
            # the regexes above require (``"3A Netzänderung\nBetrieb ab
            # Riemergasse"``). The title already attributes the line, so
            # the repeat is noise — and leaving it in also defeats the
            # summary/title duplicate check in ``_format_item_content``,
            # which would surface the same sentence twice per item.
            stripped = _strip_redundant_line_token(stripped, prefix_lines)
            if stripped != desc:
                if item is original:
                    item = dict(item)
                item["description"] = stripped
        out.append(item)
    return out


def _post_filter_oebb(items: list[Any]) -> list[Any]:
    """Re-apply the ÖBB relevance filter and re-derive titles for cached items.

    The cache is only refreshed by `update_oebb_cache.py`, so a filter
    update doesn't reach the feed until the next cache refresh. Without
    this defence-in-depth re-check the feed can carry items that the
    *current* spec considers irrelevant (e.g. Wien↔Distant routes that
    slipped through an older filter version).

    We also re-derive the title via ``_apply_route_title`` so a title-
    formatting improvement (such as the multi-route chain collapse or
    the affected-line-prefix tightening) reaches the feed even when the
    cache still holds an older rendering. The original title is left in
    place if the re-derived version is empty or identical.

    Items without a title are passed through unchanged so test fixtures
    and other generic dictionaries aren't accidentally dropped.
    """
    from ..providers.oebb import (  # local import: avoids circular at module load
        _UPDATE_PREFIX_RE,
        _apply_route_title,
        _clean_title_keep_places,
        _is_relevant,
    )
    from ..utils.stations import main_station_names, railway_station_names

    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        if not title and not description:
            # Stub / metadata item — leave it alone.
            out.append(item)
            continue
        if _UPDATE_PREFIX_RE.match(title):
            # A cached title still carrying ÖBB's "Update N (…)" headline
            # predates the provider fix; clean it the way the provider now
            # does, so the feed repairs without waiting for a cache refresh.
            title = _clean_title_keep_places(title) or title
        if not _is_relevant(title, description):
            continue
        # Main stations as the directory shows them now ("St. Pölten Hbf",
        # ``main_station_names``), also in titles cached before 2026-11-02, and
        # the railway station instead of the Wiener Linien stop in front of it
        # (``railway_station_names``, 2026-11-08).
        rederived = main_station_names(
            railway_station_names(_apply_route_title(title, description) or title)
        )
        if rederived != item.get("title"):
            item = dict(item)
            item["title"] = rederived
        out.append(item)
    return out


def _baustellen_title_names_station(title: str, label: str) -> bool:
    """Return ``True`` if ``title`` already mentions a distinctive token of
    the station/line ``label``, so the prefix isn't doubled up (e.g. avoid
    ``Wien Floridsdorf: Umbau Bahnhofsvorplatz Floridsdorf`` or
    ``U2: … U2 …``). The generic ``Wien`` token (and other ≤4-char tokens)
    is ignored on purpose — except the short U-Bahn line labels ``U1``–``U6``,
    which are distinctive and matched as a whole word."""
    lowered = title.lower()
    for token in re.split(r"\W+", label.lower()):
        if not token:
            continue
        if len(token) > 4 and token in lowered:
            return True
        if re.fullmatch(r"u[1-6]", token) and re.search(rf"\b{token}\b", lowered):
            return True
    return False


def _post_filter_baustellen(items: list[Any]) -> list[Any]:
    """Defence-in-depth relevance gate plus ÖPNV title enrichment.

    ``update_baustellen_cache.py`` already drops non-ÖPNV-relevant sites at
    ingestion, but the on-disk cache (or the bundled fallback sample) may
    predate the current policy — so the gate is re-applied here, the same
    defence-in-depth contract as :func:`_post_filter_oebb`.

    An item is kept when its text says what changes for public transport
    (:func:`src.providers.baustellen.mentions_oepnv`; nearness to a station
    alone no longer counts, operator decision 2026-10-06). When the site lies
    next to a rail Bahnhof, its name is prefixed onto the title so the entry
    reads as a transit message at a glance — mirroring the line/route
    prefixes WL and ÖBB carry. The prefix is skipped when the title already
    names the station, keeping the headline compact.

    Before the prefix, :func:`_repair_baustellen_title` completes a last
    word the city's 100-character cap cut off — from the item's own
    description, and only when that is unambiguous — and drops the
    placeholder ``Unbenannte Verkehrsfläche`` from an endpoint list that
    also names a real place.

    Items carrying neither a title nor a description are treated as
    stubs/metadata and passed through unchanged.
    """
    from ..providers.baustellen import mentions_oepnv, relevant_station
    from ..utils.stations import display_name

    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        if not title and not description:
            # Stub / metadata item — leave it alone.
            out.append(item)
            continue
        repaired = _repair_baustellen_title(title, description)
        if repaired != title:
            item = dict(item)
            item["title"] = repaired
            title = repaired
        if not mentions_oepnv(f"{title}. {description}"):
            continue
        # Title prefix: the Bahnhof next to the site, the most concrete
        # locator. Line numbers are not guessed (see the provider module):
        # the effect leads the description instead. No U-Bahn prefix any
        # more: in this source the U-Bahn is the builder, and "U2/U5: …"
        # read like a U-Bahn disruption (operator decision 2026-10-06).
        station = relevant_station(item.get("location"))
        label = ""
        if station is not None:
            label = display_name(station) or station
        if label and not _baustellen_title_names_station(title, label):
            item = dict(item)
            item["title"] = f"{label}: {title}" if title else label
        out.append(item)
    return out
