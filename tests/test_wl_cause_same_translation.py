"""Two WL causes with one English translation are one cause (operator decision 2026-11-06).

From 05.11.2026 20:31 to 06.11. 05:30 the feed read "13A: Wasserrohrbruch,
Wasserrohrgebrechen", in English "13A: Burst water pipe, burst water pipe":
WL's display wrote "Wasserrohrbruch", its long message "Wasserrohrgebrechen",
and the two became two incidents of the line, named side by side
(``_run_incidents``). The glossary renders both as "burst water pipe"
(``src/feed/glossary.py``): what the English feed cannot tell apart is one
cause. The items below are the WL cache entries of 05.11.2026 20:31
(``cache/wl_9d709a/events.json``), read the way the feed reads them.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed.glossary import _GLOSSARY_BASE
from src.feed_build.wl_tickers import _cause_key
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

VIENNA = ZoneInfo("Europe/Vienna")
DAY_END = datetime(2026, 11, 5, 23, 59, 59, tzinfo=VIENNA)


def _wl(title: str, description: str, published: datetime, ends_at: datetime | None, guid: str) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": guid,
        "pubDate": published,
        "starts_at": published,
        "ends_at": ends_at,
        "_identity": f"wl|störung|L=13A|D=2026-11-05|TK={guid}",
    }


LONG_MESSAGE = _wl(
    "13A: Wasserrohrgebrechen",
    "Die Linie 13A wird in Richtung Hauptbahnhof S U zwischen Lederergasse und Neubaugasse, Burggasse über "
    "Josefstädter Straße - Lerchenfelder Gürtel und Burggasse umgeleitet. Die Haltestelle Strozzigasse kann "
    "dadurch nicht eingehalten werden. Voraussichtliche Dauer: Bis Betriebsschluss. "
    "Grund: Wasserrohrgebrechen im Bereich Neubaugasse 81.",
    datetime(2026, 11, 5, 19, 34, tzinfo=VIENNA),
    None,
    "0ba266ec",
)
SERVICE_FROM = _wl(
    "13A: Wasserrohrbruch Betrieb ab Neubaugasse, Burggasse",
    "Wasserrohrbruch\nBetrieb ab Neubaugasse, Burggasse",
    datetime(2026, 11, 5, 19, 34, 15, tzinfo=VIENNA),
    DAY_END,
    "425523da",
)
DIVERSION = _wl(
    "13A: Wasserrohrbruch Umleitung über Josefstädter Straße",
    "Wasserrohrbruch\nUmleitung über Josefstädter Straße",
    datetime(2026, 11, 5, 20, 10, 19, tzinfo=VIENNA),
    DAY_END,
    "a20b4649",
)
LINE_13A = [LONG_MESSAGE, DIVERSION, SERVICE_FROM]


def _merge(items: list[FeedItem]) -> list[FeedItem]:
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    return bf._merge_wl_ticker_clusters(read)


def test_the_glossary_makes_them_one_cause() -> None:
    assert _GLOSSARY_BASE["Wasserrohrbruch"] == _GLOSSARY_BASE["Wasserrohrgebrechen"]
    assert _cause_key("Wasserrohrbruch") == _cause_key("Wasserrohrgebrechen")


def test_every_pair_the_glossary_renders_alike_shares_a_key() -> None:
    by_english: dict[str, list[str]] = {}
    for german, english in _GLOSSARY_BASE.items():
        by_english.setdefault(english.casefold(), []).append(german)
    alike = [terms for terms in by_english.values() if len(terms) > 1]
    assert alike
    for terms in alike:
        assert len({_cause_key(term) for term in terms}) == 1, terms


def test_wl_synonyms_and_the_glossary_compose() -> None:
    # "Fahrleitungsschaden" is WL's "Oberleitungsgebrechen" (_CAUSE_SYNONYMS), which the
    # glossary renders like "Oberleitungsstörung" and "Fahrleitungsgebrechen"
    keys = {_cause_key(c) for c in ("Fahrleitungsschaden", "Beschädigte Oberleitung", "Oberleitungsstörung", "Fahrleitungsgebrechen")}
    assert len(keys) == 1


def test_different_translations_stay_apart() -> None:
    assert _cause_key("Bauarbeiten") != _cause_key("Gleisbauarbeiten")
    assert _cause_key("Polizeieinsatz") != _cause_key("Rettungseinsatz")
    assert _cause_key("Verkehrsstörung") != _cause_key("Betriebsstörung")


def test_the_line_keeps_one_entry_with_one_cause() -> None:
    # until 2026-11-06: "13A: Wasserrohrbruch, Wasserrohrgebrechen" over
    # "Wasserrohrbruch: Umleitung über Josefstädter Straße." — the long message's text gone
    (item,) = _merge(LINE_13A)
    assert item["title"] == "13A: Wasserrohrgebrechen"
    assert str(item["description"]).startswith("Die Linie 13A wird in Richtung Hauptbahnhof S U zwischen Lederergasse")
    assert sorted(item["_members"]) == ["0ba266ec", "425523da", "a20b4649"]


def test_the_feed_names_the_cause_once() -> None:
    merged = _merge(LINE_13A)
    de = bf._make_rss(merged, datetime(2026, 11, 5, 20, 31, tzinfo=VIENNA), {}, lang="de")
    assert "<![CDATA[13A: Wasserrohrgebrechen]]>" in de
    assert "Wasserrohrbruch, Wasserrohrgebrechen" not in de
