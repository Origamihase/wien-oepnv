"""Display tickers of planned works go up in the notice of the same works.

Live 2026-10-01: "D: Gleisbauarbeiten [Am 01.10.2026]" stood in the feed
for works from 28.09. to 07.11. The fuzzy merge took the long message
"D: Gleisbauarbeiten" into the notice "D: Gleisbauarbeiten Althanstraße";
its four display tickers were left to ``_merge_wl_ticker_clusters`` and
became an item with a day line of their own. "12A: Gleisbauarbeiten" and
"25: Gleisbauarbeiten" did the same without a long message.
``_absorb_works_tickers`` drops such an entry when the notice names its
cause, covers its lines, spans its validity and names one of its streets.

Running the WL merge before the fuzzy merge would have removed the
remnants too, but took a rescue operation joined with the D tickers into
the notice ("D: Gleisbauarbeiten Althanstraße & Rettungseinsatz,
Gleisbauarbeiten", 02.10. 05:31): ``test_an_incident_joined_with_the_tickers_keeps_its_slot``.

The shapes are the entries of ``cache/wl_9d709a/events.json`` of those days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock, patch

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem

NOTICE_D = (
    "<h2>Gleisbauarbeiten</h2> <p>Wegen Gleisbauarbeiten in der Althanstra&szlig;e wird die Linie D "
    "geteilt gef&uuml;hrt.</p> <p><strong>Zeitraum:</strong><br />Ab 28. September 2026, 04:00 Uhr bis "
    "etwa Mitte November 2026.</p> <p><strong>Ma&szlig;nahmen:</strong><br />Betrieb nur zwischen "
    "Absberggasse und B&ouml;rse sowie zwischen Augasse und Nu&szlig;dorf.</p>"
)
LONG_D = (
    "Linie D: Kein Betrieb zwischen Börse und Augasse. Weichen Sie ersatzweise auf die Linien "
    "S40, U2, U4, U6, 1, 5, 12, 37, 38, 71 und 40A. aus. Grund: Gleisbauarbeiten im Bereich Althanstraße."
)
NOTICE_12A = (
    "<h2>Gleisbauarbeiten</h2> <p>Wegen Gleisbauarbeiten im Bereich M&auml;rzstra&szlig;e und Huglgasse "
    "werden die Linien 12A und N49 umgeleitet.</p> <p><strong>Zeitraum:</strong><br />Ab 15. September "
    "2026, etwa 00:30 Uhr, bis voraussichtlich Ende November 2026.</p> <p>Ersatzhaltestelle:<br />- "
    "Schweglerstra&szlig;e (Schweglerstra&szlig;e 19-21)</p>"
)
NOTICE_65A = (
    "<h2>Bauarbeiten</h2> <p>Wegen Stra&szlig;enbauarbeiten im Bereich Inzersdorfer Stra&szlig;e # "
    "Leibnizgasse werden die Linien 65A und 66A umgeleitet.</p>"
)


def _wl(
    title: str,
    description: str,
    category: str,
    start: datetime,
    end: datetime,
    guid: str,
    identity: str,
) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": start,
        "starts_at": start,
        "ends_at": end,
        "_identity": identity,
    }


def _line_d(day: datetime) -> list[FeedItem]:
    """The D works as on 2026-10-01; *day* is the tickers' day at 00:00:14 Vienna."""
    works = day - timedelta(days=3)
    works_end = day + timedelta(days=37)
    tickers_end = day + timedelta(days=1, hours=23, minutes=59, seconds=45)

    def ticker(title: str, description: str, guid: str) -> FeedItem:
        return _wl(title, description, "Störung", day, tickers_end, guid, f"wl|störung|L=D|TK={guid}")

    return [
        _wl("D: Gleisbauarbeiten Althanstraße", NOTICE_D, "Hinweis", works, works_end + timedelta(hours=2), "b25a186e",
            "wl|hinweis|L=D|TK=d gleisbauarbeiten althanstrasse"),
        _wl("D: Gleisbauarbeiten", LONG_D, "Störung", works + timedelta(hours=4, minutes=30), works_end, "7ac7f5d0",
            "wl|störung|L=D|TK=d gleisbauarbeiten"),
        ticker("D: Betrieb ab Augasse", "Gleisbauarbeiten\nBetrieb ab Augasse", "77d3c63c"),
        ticker("D: Betrieb ab Börse", "Gleisbauarbeiten\nBetrieb ab Börse", "90216ce9"),
        ticker("D: Züge halten in Schleife", "Züge halten in\nSchleife", "67078944"),
        ticker("D: Züge halten Wipplingerstr 39", "Züge halten\nWipplingerstr 39", "c56c71bc"),
    ]


DAY = datetime(2026, 9, 30, 22, 0, 14, tzinfo=UTC)  # 01.10. 00:00:14 in Vienna


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged, absorbed."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = cast(list[FeedItem], deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read))))
    return bf._absorb_works_tickers(bf._merge_wl_ticker_clusters(fuzzy))


def test_the_tickers_of_line_d_go_up_in_the_notice() -> None:
    items = _line_d(DAY)
    (item,) = _built(items)
    assert (item["guid"], item["title"]) == ("b25a186e", "D: Gleisbauarbeiten Althanstraße")
    # The notice spans the works, not the tickers' day.
    assert (item["starts_at"], item["ends_at"]) == (items[0]["starts_at"], items[0]["ends_at"])
    xml = bf._make_rss([item], DAY + timedelta(hours=8), {}, lang="de")
    assert "[Am 01.10.2026]" not in xml
    assert "28.09.2026" in xml


def test_tickers_without_a_long_message_go_up_in_the_notice_too() -> None:
    tickers_end = DAY + timedelta(days=1, hours=23, minutes=59, seconds=43)
    items = [
        _wl("12A/N49: Gleisbauarbeiten Märzstraße", NOTICE_12A, "Hinweis", DAY - timedelta(days=16),
            DAY + timedelta(days=60), "4d20323a", "wl|hinweis|L=12A,N49|TK=12a n49 gleisbauarbeiten märzstrasse"),
        _wl("12A: Betrieb ab Johnstraße U", "Gleisbauarbeiten\nBetrieb ab Johnstraße U", "Störung",
            DAY + timedelta(seconds=2), tickers_end, "841dbfcb", "wl|störung|L=12A|TK=betrieb ab johnstrasse u"),
        _wl("12A: Betrieb ab Schweglerstraße 19-21", "Gleisbauarbeiten\nBetrieb ab Schweglerstraße 19-21", "Störung",
            DAY + timedelta(seconds=2), tickers_end, "ca51ff9a", "wl|störung|L=12A|TK=betrieb ab schweglerstrasse 19 21"),
    ]
    (item,) = _built(items)
    assert (item["guid"], item["title"]) == ("4d20323a", "12A/N49: Gleisbauarbeiten Märzstraße")


def test_an_incident_joined_with_the_tickers_keeps_its_slot() -> None:
    rescue = _wl(
        "D: Rettungseinsatz Betrieb ab Schottentor",
        "Rettungseinsatz\nBetrieb ab Schottentor",
        "Störung",
        DAY + timedelta(hours=7, minutes=30),
        DAY + timedelta(hours=9),
        "e5f6a7b8",
        "wl|störung|L=D|TK=rettungseinsatz",
    )
    titles = [str(it["title"]) for it in _built([*_line_d(DAY), rescue])]
    assert "D: Gleisbauarbeiten Althanstraße" in titles
    assert any("Rettungseinsatz" in title and "&" not in title for title in titles)
    assert all("Rettungseinsatz" not in title for title in titles if title.startswith("D: Gleisbauarbeiten Althanstraße"))


def test_works_of_the_same_cause_at_another_street_keep_their_slot() -> None:
    # 66A on 2026-10-02: "Bauarbeiten / Busse halten Salvatorianerplatz" is
    # not the 65A/66A notice of the Inzersdorfer Straße.
    notice = _wl("65A/66A: Inzersdorfer Straße # Leibnizgasse", NOTICE_65A, "Hinweis", DAY - timedelta(days=50),
                 DAY + timedelta(days=330), "65a66a", "wl|hinweis|L=65A,66A|TK=inzersdorfer strasse")
    ticker = _wl("66A: Busse halten Salvatorianerplatz", "Bauarbeiten\nBusse halten Salvatorianerplatz", "Störung",
                 DAY + timedelta(hours=4, minutes=40), DAY + timedelta(days=1), "4a1c", "wl|störung|L=66A|TK=salvatorianerplatz")
    assert len(_built([notice, ticker])) == 2


def test_an_incident_of_another_cause_keeps_its_slot() -> None:
    accident = _wl(
        "D: Verkehrsunfall Betrieb ab Schottentor",
        "Verkehrsunfall\nBetrieb ab Schottentor",
        "Störung",
        DAY + timedelta(hours=8),
        DAY + timedelta(hours=10),
        "a1b2c3d4",
        "wl|störung|L=D|TK=verkehrsunfall",
    )
    titles = sorted(str(it["title"]) for it in _built([_line_d(DAY)[0], accident]))
    assert titles == ["D: Gleisbauarbeiten Althanstraße", "D: Verkehrsunfall Betrieb ab Schottentor"]


def test_main_takes_the_tickers_into_the_notice() -> None:
    rendered: list[list[str]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([str(it["title"]) for it in items])
        return ""

    live = _line_d(datetime.now(UTC) - timedelta(hours=2))
    with patch.object(bf, "_invoke_collect_items", return_value=live), \
         patch.object(bf, "_load_state", return_value={}), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch.object(bf, "validate_path", MagicMock()), \
         patch.object(bf, "write_feed_health_report", MagicMock()), \
         patch.object(bf, "write_feed_health_json", MagicMock()):
        assert bf.main() == 0
    assert rendered[0] == ["D: Gleisbauarbeiten Althanstraße"]
