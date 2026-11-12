"""WL notices: Baustellen twins, display tickers of works, notices that yield.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, time, timedelta

from ..feed_types import FeedItem
from ..utils.logging import sanitize_log_arg
from ..providers.baustellen import names_site_street, shares_address, site_street
from ..utils.text import html_to_text

from .abbreviations import without_full_stop
from .base import _VIENNA_TZ, _parse_datetime, _to_utc, log
from .time_line import _wl_incident_start
from .title_words import _is_baustelle, _is_wl_notice, _is_wl_ticker, _rendered_title
from .item_format import _format_item_content
from .wl_tickers import (
    _cause_key, _group_span, _ticker_lines, _ticker_part, _ticker_prefix, _ticker_text, _without_cause,
)

__all__ = [
    "_drop_baustellen_twins", "_absorb_works_tickers", "_yield_notices_to_incidents",
]


# --- Display tickers of planned works (2026-10-02) --------------------------
#
# WL announces planned works as a notice (``Hinweis``) and shows them on the
# displays as tickers ("Gleisbauarbeiten / Betrieb ab Johnstraße U"). On
# 2026-10-01 the fuzzy merge took the long message "D: Gleisbauarbeiten"
# into the notice "D: Gleisbauarbeiten Althanstraße"; the four tickers were
# left to the WL merge and stood beside the notice as "D: Gleisbauarbeiten
# [Am 01.10.2026]" for works from 28.09. to 07.11. "12A", "25", "N20",
# "N49", "37A" and "74A" did the same, in each of the 68 WL cache versions
# of 30.09.–02.10. WL republishes the tickers every night with a new
# identity, so they took a front slot every day; "D: Gleisbauarbeiten"
# stood in 53 of 94 German feed versions, its notice in none of them.
#
# Running the WL merge before the fuzzy merge removed them too, but took a
# rescue operation on D (02.10. 05:31, joined with the tickers into "D:
# Rettungseinsatz, Gleisbauarbeiten") into the notice as well: "D:
# Gleisbauarbeiten Althanstraße & Rettungseinsatz, Gleisbauarbeiten". So
# only one incident's entry goes up in a notice, and only when the notice
# names its cause, covers its lines, spans its validity and names a street
# of it. The street keeps two sites of one cause apart: "66A: Busse halten
# Salvatorianerplatz" (Bauarbeiten, 02.10.) is not the notice "65A/66A:
# Inzersdorfer Straße # Leibnizgasse" (Bauarbeiten too).
_WORKS_SPAN_SLACK = timedelta(days=1)
_HEADING_RE = re.compile(r"^\s*<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_STREET_SUFFIXES = "straße|str|gasse|platz|weg|allee|ring|kai|brücke|zeile|markt|steig|gürtel|lände"
_STREET_RE = re.compile(
    rf"\b[A-ZÄÖÜ][\w\-]*(?i:{_STREET_SUFFIXES})\b"
    rf"|\b[A-ZÄÖÜ][\w\-]*er(?=\s+(?i:{_STREET_SUFFIXES})\b)",
    re.UNICODE,
)


def _streets(text: str) -> set[str]:
    """The street names in *text*, casefolded ("Schweglerstraße", "Donaufelder" of "Donaufelder Straße")."""
    return {match.group(0).casefold() for match in _STREET_RE.finditer(text)}


def _notice_names_cause(notice: FeedItem, cause: str) -> bool:
    """Whether the title or the ``<h2>`` heading of *notice* names *cause* (a ``_cause_key``)."""
    title = _rendered_title(notice)
    body = title[len(_ticker_prefix(title)):]
    heading = _HEADING_RE.match(str(notice.get("description") or ""))
    for text in (body, html_to_text(heading.group(1)) if heading else ""):
        if f" {cause} " in f" {_cause_key(text)} ":
            return True
    return False


def _shares_a_street(item: FeedItem, notice: FeedItem) -> bool:
    named = _streets(f"{_rendered_title(item)} {_ticker_text(item)}")
    return bool(named & _streets(f"{_rendered_title(notice)} {html_to_text(str(notice.get('description') or ''))}"))


def _within(inner: FeedItem, outer: FeedItem) -> bool:
    """Whether the validity of *inner* lies in that of *outer*, give or take a day."""
    span, works = _group_span([inner]), _group_span([outer])
    if span is None or works is None:
        return False
    if span[0] < works[0] - _WORKS_SPAN_SLACK:
        return False
    if works[1] is None:
        return True
    return span[1] is not None and span[1] <= works[1] + _WORKS_SPAN_SLACK


# --- Stop tickers of a relocation (2026-10-29) -------------------------------
#
# WL shows a relocated stop on the displays as well: "40A: Busse halten
# Liechtensteinstraße 39" and "… 38", every day from 27.10.2026, beside the
# notice "40A: Bauernfeldplatz" that moves the stop there ("Nach:
# Liechtensteinstraße 38", "Nach: Liechtensteinstraße 39"). A relocation's
# title names the stop, not the cause, and the board said "Bauarbeiten" where
# the notice says "Kabelarbeiten", so the rule above never matched: the German
# feed showed the same new stop twice in about 100 versions of 27.–29.10.
# (Jules audits 28. and 29.10. took both as correct). The WL cache since July
# holds six such pairs (13A, 12A/14A, 7A/N65/N66, 3A, N20, 40A). A ticker that
# says nothing but "Busse/Züge halten <Adresse>", every address one the notice
# moves the stop to, goes up in the notice while it is in force — as long as
# the notice's own short text still shows each address. "40A: Bauernfeldplatz"
# loses "Liechtensteinstraße 39" behind the 180 cut ("… gegenüber
# Bauernfeldplatz → …"); there the ticker is the only place the new stop is
# read, and it keeps its slot.
_STOP_TICKER_RE = re.compile(r"(?:Busse|Züge|Straßenbahnen) halten (?P<places>.+)")
_RELOCATION_TARGET_RE = re.compile(r"^Nach:\s*(?P<place>.+?)\s*$", re.MULTILINE)


def _same_place(shown: str, target: str) -> bool:
    """Whether the board's *shown* address is the notice's *target* ("Kärntner Ring 5" of "Kärntner Ring 5, Nebenfahrbahn")."""
    shown, target = " ".join(shown.casefold().split()), " ".join(target.casefold().split())
    return target == shown or (target.startswith(shown) and target[len(shown)] in " ,(")


def _relocated_stops_shown(item: FeedItem, notice: FeedItem) -> bool:
    """Whether the WL ticker *item* only shows the stops *notice* relocates."""
    targets = _RELOCATION_TARGET_RE.findall(html_to_text(str(notice.get("description") or "")))
    if not targets:
        return False
    part = _ticker_part(item)
    text = without_full_stop((part.extra or part.consequence).strip())
    text = _without_cause(text, part.cause)
    messages = [m.strip() for m in text.split(";") if m.strip()]
    if not messages:
        return False
    shown_places: list[str] = []
    for message in messages:
        match = _STOP_TICKER_RE.fullmatch(message)
        if match is None:
            return False
        places = [p.strip() for p in match["places"].split(",") if p.strip()]
        if not places or not all(any(_same_place(p, t) for t in targets) for p in places):
            return False
        shown_places.extend(places)
    shown = _format_item_content(
        notice,
        str(notice.get("guid") or ""),
        _parse_datetime(notice.get("starts_at")),
        _parse_datetime(notice.get("ends_at")),
        lang="de",
    ).desc_text_truncated.casefold()
    return all(
        re.search(rf"(?<![\w-]){re.escape(place.casefold())}(?![\w-])", shown) for place in shown_places
    )


def _works_shown(item: FeedItem, notices: Sequence[FeedItem]) -> FeedItem | None:
    """The notice whose planned works or relocated stops the WL disruption *item* only shows, if any."""
    if not _is_wl_ticker(item):
        return None
    cause = _cause_key(_ticker_part(item).cause)
    lines = set(_ticker_lines(item).split("/")) - {""}
    if not lines:
        return None
    for notice in notices:
        if not lines <= set(_ticker_lines(notice).split("/")) or not _within(item, notice):
            continue
        if _notice_reported_live(notice, [item]) is not None:
            # A live incident the notice gives way to (``_yield_notices_to_incidents``).
            continue
        if cause and _notice_names_cause(notice, cause) and _shares_a_street(item, notice):
            return notice
        if _relocated_stops_shown(item, notice):
            return notice
    return None


def _drop_baustellen_twins(items: list[FeedItem], now: datetime) -> list[FeedItem]:
    """Drop a Stadt Wien Baustelle that a running WL notice already reports.

    The city's roadworks list says what changes for transit without naming
    the line ("Die Haltestelle des betroffenen öffentlichen Verkehrsmittels
    wird von Burggasse ONr.67 nach Burggasse ONr. 69 verlegt"); Wiener Linien
    publish the same measure with line, direction and reason ("48A:
    Neubaugasse, Burggasse … Nach: Burggasse 69"). Since May 2026 nearly
    every site that passed the transit rule had such a WL twin, and twice
    both stood among the ten displayed items for days ("Inzersdorfer Straße
    Kreuzung Leibnizgasse" next to "65A/66A/7A: Inzersdorfer Straße #
    Leibnizgasse", 06.–13.08.; "Floridsdorfer Brücke …" next to the line 31,
    09.–13.07.). Operator decision 2026-10-09: the WL notice is enough.

    A twin is a WL ``Hinweis`` (a planned measure, not a live incident) that
    is in force now and either gives the site's street as the place of its
    cause ("Wegen Bauarbeiten im Bereich Neilreichgasse …",
    :func:`src.providers.baustellen.names_site_street`) or names an address
    the site's text names too ("Nach: Burggasse 69",
    :func:`src.providers.baustellen.shares_address`). A street that only
    appears in a detour or a stop list does not make a twin. A site without
    one stays as before.
    """
    now_utc = _to_utc(now)
    notices: list[str] = []
    for it in items:
        if str(it.get("source") or "").strip().casefold() != "wiener linien":
            continue
        if str(it.get("category") or "").strip().casefold() != "hinweis":
            continue
        start = _parse_datetime(it.get("starts_at"))
        if isinstance(start, datetime) and _to_utc(start) > now_utc:
            continue
        notices.append(
            f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
        )
    if not notices:
        return items
    out: list[FeedItem] = []
    for it in items:
        if _is_baustelle(it):
            street = site_street(str(it.get("title") or ""))
            site_text = f"{it.get('title') or ''}. {it.get('description') or ''}"
            twin = next(
                (
                    text
                    for text in notices
                    if (street and names_site_street(text, street))
                    or shares_address(site_text, text)
                ),
                None,
            )
            if twin is not None:
                log.info(
                    "Baustelle %s verworfen: WL meldet dieselbe Maßnahme (%s).",
                    sanitize_log_arg(str(it.get("title") or "")),
                    sanitize_log_arg(twin[:80]),
                )
                continue
        out.append(it)
    return out


def _absorb_works_tickers(items: list[FeedItem]) -> list[FeedItem]:
    """Drop the WL disruptions that only show a notice's planned works or relocated stops (see above)."""
    notices = [item for item in items if _is_wl_notice(item)]
    if not notices:
        return items
    kept: list[FeedItem] = []
    for item in items:
        works = _works_shown(item, notices)
        if works is None:
            kept.append(item)
            continue
        log.info(
            "WL-Kurzmeldung in den Hinweis aufgenommen: %s → %s",
            sanitize_log_arg(_rendered_title(item)),
            sanitize_log_arg(_rendered_title(works)),
        )
    return kept


# --- A notice gives way to the live incident that reports it (2026-11-01) -----
#
# WL announces a planned event as a notice ("1/2/71/D: Nationalfeiertag",
# <h2> "Veranstaltung", Ringstraße, in the feed since 16.10.) and, when the
# day comes, opens a live incident for it as well ("1/2/71/D: Veranstaltung",
# I20261026-0004, "Grund: Veranstaltung im Bereich Ringstraße."). Both said
# the same diversions; on 26.10.2026 they stood side by side from 09:01 to
# 17:31 Vienna time on two of the ten places (the KI watcher's finding of
# 01.11.; on 25.10. the pair of that day shared one title, and the fuzzy
# merge already showed it once). ``_absorb_works_tickers`` does not take
# the incident: a live incident has no end, so it never lies within the
# notice (``_within``). The incident is the better of the two for the viewer:
# it carries WL's expected end ("Voraussichtlich bis 18:00 Uhr") and leaves
# the feed the moment WL closes it. So the notice gives way while a running
# WL incident (a long message with WL's incident start) names its cause (in
# the notice's title or its <h2>), shares a street with it, began no earlier
# than a day before the notice and covers every line of the notice. An
# incident on only some of the notice's lines ("4A/74A: Demonstration"
# beside the notice of 16 lines on 22.10.) says nothing about the others,
# and both stay. Only a notice of one operating day gives way: it must end
# by 04:00 after the incident's Vienna date (``_service_day_end``). A notice
# of weeks ("1: Gleisbauarbeiten", 21.10. to 05.12.) tells what holds after
# a short incident of the same cause is over, and it stays. The same holds
# when WL gives the incident an end: ``_absorb_works_tickers`` no longer
# takes such an incident into a notice of one day. On 11.10.2026 it took
# "18: Laufveranstaltung" (I20261011-0007, "bis 23:55") into "18:
# LCC-Herbstmarathon am 11.10.2026" from 09:01 to 14:30 Vienna time, the
# opposite of the 26.10. rule only because WL named an end. When the
# incident ends, the notice is back as before. The replay of 555 feed
# states since 01.10. and of every state of 11.10. and 26.10. changes two
# days only: 11.10. 09:01 to 14:30 and 26.10. 09:01 to 17:31 Vienna time.
# Operator decision 2026-11-01 ("Störung bleibt"): of the two, the live
# incident stays. His general rule of the same day: "Störungen haben höchste
# Priorität im Feed! Hinweise sind weniger wichtig."


# WL's operating day ends in the night: night works end "04:00" of the next
# date ("1: Gleisbauarbeiten" … 05.12.2026 04:00).
_SERVICE_DAY_END = timedelta(hours=4)


def _service_day_end(moment: datetime) -> datetime:
    """The end (UTC) of the WL operating day of the Vienna date of *moment*."""
    local = _to_utc(moment).astimezone(_VIENNA_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(), tzinfo=_VIENNA_TZ)
    return _to_utc(midnight + _SERVICE_DAY_END)


def _notice_reported_live(notice: FeedItem, items: Sequence[FeedItem]) -> FeedItem | None:
    """The running WL incident among *items* that reports *notice*'s measures, if any (see above)."""
    lines = set(_ticker_lines(notice).split("/")) - {""}
    if not lines:
        return None
    start = _parse_datetime(notice.get("starts_at"))
    end = _parse_datetime(notice.get("ends_at"))
    if not isinstance(end, datetime):
        return None
    for item in items:
        if not _is_wl_ticker(item):
            continue
        begin = _wl_incident_start(item)
        if begin is None:
            continue
        if isinstance(start, datetime) and _to_utc(begin) < _to_utc(start) - _WORKS_SPAN_SLACK:
            continue
        if _to_utc(end) > _service_day_end(begin):
            continue
        if not lines <= set(_ticker_lines(item).split("/")):
            continue
        cause = _cause_key(_ticker_part(item).cause)
        if cause and _notice_names_cause(notice, cause) and _shares_a_street(item, notice):
            return item
    return None


def _yield_notices_to_incidents(items: list[FeedItem], now: datetime) -> list[FeedItem]:
    """Drop a WL notice in force while a running WL incident reports the same measures (see above)."""
    now_utc = _to_utc(now)
    kept: list[FeedItem] = []
    for item in items:
        if _is_wl_notice(item):
            start = _parse_datetime(item.get("starts_at"))
            if not isinstance(start, datetime) or _to_utc(start) <= now_utc:
                incident = _notice_reported_live(item, items)
                if incident is not None:
                    log.info(
                        "WL-Hinweis tritt zurück, solange die Störung läuft: %s → %s",
                        sanitize_log_arg(_rendered_title(item)),
                        sanitize_log_arg(_rendered_title(incident)),
                    )
                    continue
        kept.append(item)
    return kept
