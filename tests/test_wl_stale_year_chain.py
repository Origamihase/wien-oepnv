"""A WL notice whose period carries the year of an older notice, from the fetch to the feed.

"39B: Friedhofsverkehr", published 11.11.2026 15:55 for a bus on
Christmas Eve, read "Zeitraum: Am 24. Dezember 2024, zwischen ca. 8:00 Uhr
und 17:00 Uhr" (the text of an older notice) beside ``time.end``
24.12.2026 17:00. A begin date before the publication counted as a phase
already running, so the publication stayed the start: from 11.11. on the
feed held the notice in its normal place, "… [Bis 24.12.]", as if the bus
ran then. Operator decision 2026-11-11 ("ja, bitte"): such a date takes
the year between publication and a real end
(``wl_plausibility._restated_year``, ``STALE_YEAR_MAX_LEAD_DAYS``).

Two stages are pinned here: the WL fetch with the record as WL sent it,
and the feed chain, where an announcement waits behind what runs until the
day before it begins (``filters._defer_upcoming_items``).

The rule itself is pinned in ``tests/test_wl_plausibility.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build import item_format, time_line
from src.feed_types import FeedItem
from src.providers import wl_fetch
from src.providers import wl_plausibility as wp
from src.providers.wl_item_fields import _is_active
from src.utils import raw_capture

VIENNA = ZoneInfo("Europe/Vienna")
CET = timezone(timedelta(hours=1))

# The record as WL sent it on 11.11.2026 (data/raw/wl/newsList.json).
CEMETERY_DESCRIPTION = (
    "<h2>Inbetriebnahme einer Autobuslinie</h2><p></p><p>Anlässlich des 24. Dezember wird die "
    "Autobuslinie 39B in Betrieb genommen und diverse Linien zu Friedhöfen verstärkt geführt.</p>"
    "<p><span><strong><u>Zeitraum:<br></u></strong></span>Am 24. Dezember 2024, zwischen ca. 8:00 "
    "Uhr und 17:00 Uhr.</p><p><span><strong><u>Maßnahmen:<br></u></strong></span></p><p><strong>"
    "Linie 39B:</strong><span><u><br></u></span>Rundkurs von der Haltestelle Karthäuserstraße zum "
    "Sieveringer Friedhof und zurück.</p><p><span><u>Fahrtverlauf:</u><strong><u><br></u></strong>"
    "</span>Sieveringer Straße - Brechergasse - Nottebohmstraße - Karthäuserstraße - Sieveringer "
    "Straße.</p><p><span><u>Betriebszeiten:</u><strong><u><br></u></strong></span>Karthäuserstraße "
    "- Sieveringer Friedhof: Erster Bus 08:04 Uhr, letzter Bus 16:54 Uhr.<br>Sieveringer Friedhof - "
    "Karthäuserstraße: Erster Bus 08:08 Uhr, letzter Bus 16:58 Uhr.</p><p><span><u>Haltestellen:</u>"
    "<strong><u><br></u></strong></span>Die Haltestelle Karthäuserstraße des 39B befindet sich in "
    "der Sieveringer Straße 118!</p>"
)
PUBLISHED = datetime(2026, 11, 11, 15, 55, tzinfo=CET)
END = datetime(2026, 12, 24, 17, 0, tzinfo=CET)
CHRISTMAS_EVE = datetime(2026, 12, 24, tzinfo=VIENNA)


# The evening WL published the notice.
FETCHED = datetime(2026, 11, 11, 20, 0, tzinfo=UTC)


def _cemetery_record() -> dict[str, Any]:
    """The 39B record in the shape of WL's newsList."""
    return {
        "description": CEMETERY_DESCRIPTION,
        "id": "39B-friedhofsverkehr",
        "name": "39B-friedhofsverkehr",
        "relatedLines": ["39B"],
        "time": {
            "start": "2026-11-11T15:55:00.000+0100",
            "validFrom": "2026-11-11T15:55:00.000+0100",
            "end": "2026-12-24T17:00:00.000+0100",
        },
        "title": "39B: Friedhofsverkehr",
    }


def test_fetch_gives_the_notice_the_day_wl_meant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Whether WL still lists the notice is asked for the evening it was
    # published. (A frozen ``wl_fetch.datetime`` would also turn the start
    # day of ``_identity`` into "None": ``_wl_identity`` asks ``isinstance``.)
    monkeypatch.setattr(wl_fetch, "_is_active", lambda start, end, now: _is_active(start, end, FETCHED))
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    responses = {
        "trafficInfoList": {"data": {"trafficInfos": []}, "message": {"value": "OK"}},
        "newsList": {"data": {"pois": [_cemetery_record()]}, "message": {"value": "OK"}},
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])

    items = wl_fetch.fetch_events()

    assert [str(item["title"]) for item in items] == ["39B: Friedhofsverkehr"]
    item = items[0]
    assert item["pubDate"] == PUBLISHED
    assert item["starts_at"] == CHRISTMAS_EVE
    assert item["ends_at"] == END
    assert "|D=2026-12-24|" in str(item["_identity"])
    assert [(title, c.kind) for title, c in wp.collected_corrections()] == [
        ("39B: Friedhofsverkehr", wp.STALE_YEAR)
    ]


# --- The feed chain -------------------------------------------------------------


def _cached(title: str, guid: str, published: datetime, starts: datetime, ends: datetime | None, description: str) -> dict[str, Any]:
    """A WL notice with the keys of ``cache/wl/events.json``."""
    return {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": guid,
        "pubDate": published.isoformat(),
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat() if ends else None,
        "_identity": f"wl|hinweis|{guid}",
    }


def _feed(now: datetime) -> list[tuple[str, str]]:
    """Titles and time lines of the feed built at *now* from a running notice and the 39B one.

    The time line is phrased for *now* as well, not for the clock of the run.
    """
    running = _cached(
        "31: Wexstraße",
        "wl-31",
        datetime(2026, 11, 5, 13, 45, tzinfo=CET),
        datetime(2026, 11, 9, tzinfo=VIENNA),
        None,
        "<p>Haltestellenverlegung der Linie 31 in Richtung Stammersdorf. Grund: Haltestellenumbau.</p>",
    )
    cemetery = _cached("39B: Friedhofsverkehr", "wl-39b", PUBLISHED, CHRISTMAS_EVE, END, CEMETERY_DESCRIPTION)
    items: list[FeedItem] = bf._normalize_item_datetimes([running, cemetery])
    state: dict[str, Any] = {}
    now_utc = now.astimezone(UTC)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(item_format, "format_local_times", partial(time_line.format_local_times, now=now_utc))
        rss = bf._make_rss(bf._assemble_feed_items(items, state, now_utc), now_utc, state)
    out = []
    for node in ET.fromstring(rss).findall("./channel/item"):
        description = str(node.findtext("description"))
        out.append((str(node.findtext("title")), description[description.rfind("[") :]))
    return out


def test_announcement_waits_behind_what_runs_until_the_day_before() -> None:
    evening = _feed(datetime(2026, 11, 11, 21, 0, tzinfo=VIENNA))
    assert [title for title, _ in evening] == ["31: Wexstraße", "39B: Friedhofsverkehr"]
    assert evening[1][1] == "[Am\xa024.12.]"

    day_before = _feed(datetime(2026, 12, 23, 18, 0, tzinfo=VIENNA))
    assert [title for title, _ in day_before] == ["39B: Friedhofsverkehr", "31: Wexstraße"]
    assert day_before[0][1] == "[Am\xa0Do\xa024.12.]"

    christmas_eve = _feed(datetime(2026, 12, 24, 9, 0, tzinfo=VIENNA))
    assert christmas_eve[0] == ("39B: Friedhofsverkehr", "[Heute]")
