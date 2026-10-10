"""A display ticker WL rewrote for a new incident does not date it back.

On 2026-10-09 WL rewrote the tickers of a demonstration (lines 2 and 71,
started 13:08:16 and 13:10:16) to "Fahrtbehinderung wegen Polizeieinsatz"
for the police operation ``I20261009-0043`` (start 20:08). The tickers kept
their start, the item took the earliest ``pubDate``, and the German feed
read "2/71: Polizeieinsatz [Seit 13:08]" at 20:30 and "2: Polizeieinsatz
[Seit 13:08]", "71: Polizeieinsatz [Seit 13:10]" at 21:01. The records
below are copied from the raw WL answer of 20:30 (``data/raw/wl``).

A ticker that runs a little ahead of its incident still gives the begin:
"9: Falschparker" (09.10.) had a ticker from 14:21 and the incident
message from 15:00.

Mutations checked against this file (each one caught, by the test named):

* ``incident_begin`` returns the ``pubDate`` unchanged →
  ``test_the_rewritten_tickers_give_way_in_the_fetch``,
  ``test_the_merge_of_ticker_and_incident_keeps_the_incident_begin``.
* the window is dropped (incident start always wins) →
  ``test_a_ticker_ahead_of_its_incident_keeps_the_begin``.
* ``_span_group`` keeps no ``_incident_start`` →
  ``test_the_merge_of_ticker_and_incident_keeps_the_incident_begin``.
* the time line ignores ``_incident_start`` →
  ``test_a_carried_old_begin_gives_way_in_the_time_line``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.providers.wl_plausibility import TICKER_LEAD_MAX, incident_begin

VIENNA = ZoneInfo("Europe/Vienna")
NBSP = "\u00a0"


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=VIENNA)


def _ticker(name: str, line: str, start: str, end: str) -> dict[str, Any]:
    return {
        "description": "Fahrtbehinderung\nwegen Polizeieinsatz",
        "name": name,
        "owner": "WL",
        "priority": "1",
        "refTrafficInfoCategoryId": 3,
        "relatedLines": [line],
        "relatedStops": [14],
        "time": {"end": end, "start": start},
        "title": "Fahrtbehinderung\nwegen Polizeieinsatz",
    }


def _follow_up(name: str, line: str) -> dict[str, Any]:
    return {
        "description": f"Linie {line}: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "name": name,
        "owner": "WL",
        "priority": "1",
        "refTrafficInfoCategoryId": 2,
        "relatedLines": [line],
        "relatedStops": [13],
        "status": "active",
        "time": {
            "created": "2026-10-09T20:14:00.000+0200",
            "end": "2026-10-09T23:55:00.000+0200",
            "lastUpdate": "2026-10-09T20:14:00.000+0200",
            "resume": "2026-10-09T20:14:00.000+0200",
            "start": "2026-10-09T20:08:00.000+0200",
        },
        "title": f"{line}: Polizeieinsatz",
    }


RAW_2030 = [
    _follow_up("I20261009-0043-F03", "2"),
    _ticker("R14-102", "2", "2026-10-09T13:08:16.000+0200", "2026-10-09T21:13:00.000+0200"),
    _ticker("R15-102", "2", "2026-10-09T13:09:16.000+0200", "2026-10-09T21:13:00.000+0200"),
]


class _Session:
    headers: dict[str, str] = {}

    def __enter__(self) -> _Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


def _fetch(monkeypatch: pytest.MonkeyPatch, infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: list(infos))
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())
    return wl_fetch.fetch_events()


def test_the_window_bounds_the_lead() -> None:
    incident = _at(20, 8)
    assert incident_begin(_at(13, 8, 16), incident) == incident
    assert incident_begin(incident - TICKER_LEAD_MAX, incident) == incident - TICKER_LEAD_MAX
    assert incident_begin(_at(13, 8, 16), None) == _at(13, 8, 16)
    assert incident_begin(None, incident) is None


def test_a_ticker_ahead_of_its_incident_keeps_the_begin() -> None:
    assert incident_begin(_at(14, 21, 23), _at(15, 0)) == _at(14, 21, 23)


class _At2030(datetime):
    """The fetch of 20:30:49 (no freezegun: it trips over unrelated modules)."""

    @classmethod
    def now(cls, tz: Any = None) -> _At2030:
        moment = _at(20, 30, 49)
        when = moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)
        return cls(*when.timetuple()[:6], tzinfo=when.tzinfo)


def test_the_rewritten_tickers_give_way_in_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "datetime", _At2030)
    events = _fetch(monkeypatch, RAW_2030)
    begins = {e["title"]: e["pubDate"] for e in events}
    assert begins
    for title, begin in begins.items():
        assert begin >= _at(20, 8) - TICKER_LEAD_MAX, title
    with_incident = [e for e in events if e.get("_wl_ids")]
    assert with_incident and with_incident[0]["_incident_start"] == _at(20, 8)


def _item(title: str, pub: datetime, start: datetime, **extra: Any) -> FeedItem:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": f"g-{title}",
        "pubDate": pub,
        "starts_at": start,
        "ends_at": _at(23, 55),
        "_identity": f"wl|{title}",
    }
    item.update(extra)
    return cast(FeedItem, item)


def _line(item: FeedItem, now: datetime) -> str:
    start = item.get("starts_at")
    since = bf._incident_since(item, start if isinstance(start, datetime) else None)
    return bf.format_local_times(
        start if isinstance(start, datetime) else None, None, now, since=since
    ).replace(NBSP, " ")


def test_the_merge_of_ticker_and_incident_keeps_the_incident_begin() -> None:
    # The two items of line 2 as the WL cache held them at 20:30; the
    # ``_incident_start`` comes back from the cache as text.
    ticker = _item("2: Fahrtbehinderung wegen Polizeieinsatz", _at(13, 8, 16), _at(20, 14, 8))
    incident = _item(
        "2: Polizeieinsatz",
        _at(20, 8),
        _at(20, 8),
        _wl_ids=["I20261009-0043"],
        _incident_start="2026-10-09T20:08:00+02:00",
    )
    items = cast(list[FeedItem], bf._normalize_item_datetimes([ticker, incident]))
    merged = bf._merge_wl_ticker_clusters(items)
    assert len(merged) == 1
    assert merged[0]["pubDate"] == _at(20, 8)
    assert _line(merged[0], _at(20, 30)) == "Seit 20:08"


def test_a_carried_old_begin_gives_way_in_the_time_line() -> None:
    # The state of an earlier build can hand the item its old begin back
    # (``_carry_item_identity``); the time line still reads the incident's.
    item = _item(
        "71: Polizeieinsatz",
        _at(13, 10, 16),
        _at(20, 8),
        _wl_ids=["I20261009-0043"],
        _incident_start=_at(20, 8),
    )
    assert _line(item, _at(21, 1)) == "Seit 20:08"


def test_without_an_incident_message_the_ticker_stands() -> None:
    item = _item("37: Feuerwehreinsatz", _at(4, 49, 17), _at(4, 49, 17))
    assert _line(item, _at(9, 31)) == "Seit 04:49"
    assert timedelta(minutes=60) == TICKER_LEAD_MAX
