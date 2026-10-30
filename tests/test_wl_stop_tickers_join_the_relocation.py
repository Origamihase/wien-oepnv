"""Display tickers of a relocated stop go up in the relocation notice.

Live 27.–29.10.2026: the German feed carried "40A: Bauernfeldplatz" (the
notice: "Nach: Liechtensteinstraße 38" towards Döblinger Friedhof, "Nach:
Liechtensteinstraße 39" towards Schottentor U) and, right beside it, "40A:
Bauarbeiten" ("Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38.",
the two display tickers WL republishes every morning). The board says
"Bauarbeiten", the notice "Kabelarbeiten", and a relocation's title names the
stop, so the works rule of ``_absorb_works_tickers`` never matched. A ticker
that only names the stops the notice moves the stop to now goes up in it, as
long as the notice's short text still shows each of them; where the 180 cut
hides one, the ticker is the only place the feed reads that stop and keeps
its slot.

The shapes are the entries of ``cache/wl_9d709a/events.json`` of 29.10.2026.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

NOTICE_40A = (
    "<p><strong><u>Haltestellenverlegung der Linie 40A in beiden Richtungen</u></strong></p>"
    "<p><strong><u>Haltestelle:</u></strong> Bauernfeldplatz</p><p></p>"
    "<p><strong><u>Fahrtrichtung:</u></strong> Döblinger Friedhof, Felix-Dahn-Straße</p>"
    "<p><strong><u>Von:</u></strong> Liechtensteinstraße 42</p>"
    "<p><strong><u>Nach:</u></strong> Liechtensteinstraße 38</p><p></p>"
    "<p><strong><u>Fahrtrichtung:</u></strong> Schottentor U</p>"
    "<p><strong><u>Von:</u></strong> Liechtensteinstraße 37a gegenüber Bauernfeldplatz</p>"
    "<p><strong><u>Nach:</u></strong> Liechtensteinstraße 39</p><p></p>"
    "<p><strong><u>Dauer:</u></strong> Ab 27. Oktober 2026, etwa 07:00 Uhr, Dauer unbekannt</p>"
    "<p><strong><u>Grund:</u></strong> Kabelarbeiten</p>"
)
NOTICE_3A = (
    "<p><strong><u>Haltestellenverlegung der Linie 3A in Richtung Schottenring U</u></strong></p>"
    "<p><strong><u>Haltestelle:</u></strong> Oper, Karlsplatz</p>"
    "<p><strong><u>Von:</u></strong> Kärntner Ring, Nebenfahrbahn vor Kärntner Straße</p>"
    "<p><strong><u>Nach:</u></strong> Kärntner Ring 5, Nebenfahrbahn</p>"
    "<p><strong><u>Grund:</u></strong> Haussanierung</p>"
)

NOTICE_15A = (
    "<p><strong><u>Haltestellenverlegung der Linien 7A in Richtung Reumannplatz U, 15A in Richtung Enkplatz U, "
    "Grillgasse und N62 in Richtung Oper, Karlsplatz (U)</u></strong></p>"
    "<p><strong><u>Haltestelle:</u></strong> Eibesbrunnergasse</p>"
    "<p><strong><u>Von:</u></strong> Wienerbergstraße 27b-27c</p>"
    "<p><strong><u>Nach:</u></strong> Wienerbergstraße 27a</p>"
    "<p><strong><u>Grund:</u></strong> Instandsetzungsarbeiten</p>"
    "<p><strong><u>Haltestellenverlegung der Linien 7A in Richtung Meidling Hauptstraße U, 15A in Richtung "
    "Bhf. Meidling S U, Schedifkaplatz und N62 in Richtung Lainzer Tor</u></strong></p>"
    "<p><strong><u>Haltestelle:</u></strong> Eibesbrunnergasse</p>"
    "<p><strong><u>Von:</u></strong> Wienerbergstraße 12, Stiege 21-22</p>"
    "<p><strong><u>Nach:</u></strong> Wienerbergstraße 14-14A</p>"
    "<p><strong><u>Grund:</u></strong> Instandsetzungsarbeiten</p>"
)

DAY = datetime(2026, 10, 29, 3, 45, 18, tzinfo=UTC)  # 04:45:18 in Vienna
NOTICE_START = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


def _wl(title: str, description: str, category: str, start: datetime, end: datetime | None, guid: str) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": guid,
        "pubDate": start,
        "starts_at": start,
        "ends_at": end,
        "_identity": f"wl|{category.casefold()}|{title.casefold()}",
    }


def _ticker(title: str, description: str, guid: str) -> FeedItem:
    return _wl(title, description, "Störung", DAY, DAY + timedelta(hours=19, minutes=14), guid)


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged, absorbed."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = cast(list[FeedItem], deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read))))
    return bf._absorb_works_tickers(bf._merge_wl_ticker_clusters(fuzzy))


def _notice() -> FeedItem:
    return _wl("40A: Bauernfeldplatz", NOTICE_40A, "Hinweis", NOTICE_START, None, "38dbe79595a5ecf1")


def test_a_stop_ticker_the_notice_shows_goes_up_in_it() -> None:
    items = [
        _notice(),
        _ticker("40A: Busse halten Liechtensteinstraße 38", "Bauarbeiten\nBusse halten Liechtensteinstraße 38", "08dab1d1a5c0"),
    ]
    (item,) = _built(items)
    assert (item["guid"], item["title"]) == ("38dbe79595a5ecf1", "40A: Bauernfeldplatz")


def test_both_live_40a_tickers_go_up_once_the_notice_shows_both_stops() -> None:
    """Since 2026-10-29 the notice reads "Richtung Schottentor U: → Liechtensteinstraße 39"."""
    items = [
        _notice(),
        _ticker("40A: Busse halten Liechtensteinstraße 38", "Bauarbeiten\nBusse halten Liechtensteinstraße 38", "08dab1d1a5c0"),
        _ticker("40A: Busse halten Liechtensteinstraße 39", "Bauarbeiten\nBusse halten Liechtensteinstraße 39", "844d0de929c4"),
    ]
    assert [i["guid"] for i in _built(items)] == ["38dbe79595a5ecf1"]


def test_a_stop_the_cut_hides_keeps_its_ticker() -> None:
    """15A/7A/N62 (06.10.2026): the second stop stands behind three lines and their directions."""
    items = [
        _wl("15A/7A/N62: Eibesbrunnergasse beide Fahrtrichtungen", NOTICE_15A, "Hinweis", NOTICE_START, None, "15a-notice"),
        _ticker("7A: Busse halten Wienerbergstraße 14-14A", "Bauarbeiten\nBusse halten Wienerbergstraße 14-14A", "ticker"),
    ]
    assert sorted(i["guid"] for i in _built(items)) == ["15a-notice", "ticker"]


def test_the_board_may_shorten_the_notice_address() -> None:
    """"Busse halten Kärntner Ring 5" is the notice's "Nach: Kärntner Ring 5, Nebenfahrbahn"."""
    items = [
        _wl("3A: Oper, Karlsplatz", NOTICE_3A, "Hinweis", NOTICE_START, None, "3a-notice"),
        _ticker("3A: Busse halten Kärntner Ring 5", "Busse halten\nKärntner Ring 5", "3a-ticker"),
    ]
    assert [i["guid"] for i in _built(items)] == ["3a-notice"]


@pytest.mark.parametrize(
    ("title", "description"),
    [
        # Another stop than the one the notice moves to.
        ("40A: Busse halten Liechtensteinstraße 3", "Bauarbeiten\nBusse halten Liechtensteinstraße 3"),
        # The notice's origin, not its target.
        ("40A: Busse halten Liechtensteinstraße 42", "Bauarbeiten\nBusse halten Liechtensteinstraße 42"),
    ],
)
def test_a_ticker_that_says_more_keeps_its_slot(title: str, description: str) -> None:
    items = [_notice(), _ticker(title, description, "ticker")]
    assert sorted(i["guid"] for i in _built(items)) == ["38dbe79595a5ecf1", "ticker"]


def test_a_consequence_beyond_the_stop_keeps_the_entry() -> None:
    """Joined with "Betrieb ab Sensengasse", the stop ticker says more than the notice."""
    items = [
        _notice(),
        _ticker("40A: Busse halten Liechtensteinstraße 38", "Bauarbeiten\nBusse halten Liechtensteinstraße 38", "08dab1d1a5c0"),
        _ticker("40A: Betrieb ab Sensengasse", "Bauarbeiten\nBetrieb ab Sensengasse", "sensengasse"),
    ]
    assert len(_built(items)) == 2


def test_a_ticker_of_another_line_keeps_its_slot() -> None:
    items = [
        _notice(),
        _ticker("37A: Busse halten Liechtensteinstraße 38", "Bauarbeiten\nBusse halten Liechtensteinstraße 38", "ticker"),
    ]
    assert sorted(i["guid"] for i in _built(items)) == ["38dbe79595a5ecf1", "ticker"]


def test_a_ticker_after_the_relocation_ended_keeps_its_slot() -> None:
    """N20 "Busse halten Donaufelder Straße 40-42" ran on after its notice ended (15.10.)."""
    ended = _wl("40A: Bauernfeldplatz", NOTICE_40A, "Hinweis", NOTICE_START - timedelta(days=20),
                NOTICE_START - timedelta(days=10), "38dbe79595a5ecf1")
    items = [
        ended,
        _ticker("40A: Busse halten Liechtensteinstraße 38", "Bauarbeiten\nBusse halten Liechtensteinstraße 38", "ticker"),
    ]
    assert "ticker" in [i["guid"] for i in _built(items)]
