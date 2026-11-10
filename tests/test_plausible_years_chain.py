"""A Wiener Linien time stamp at the edge of the calendar, from the fetch to the feed.

Operator decision of 2026-11-10: a time stamp whose year lies outside
2000 to 2100 counts as not set (``src.utils.vienna_clock.PLAUSIBLE_YEARS``).
The item stays, only without that date.

The replay of that day showed what such a value did before. One WL
disruption with ``time.end = 9999-12-31T23:59:59.000+0100`` reached the
cache, and every feed build ended with ``OverflowError`` in
``src/feed_build/filters.py`` (``_drop_after``). A ``time.start`` or
``time.end`` in the year 1 ended the WL fetch itself
(``wl_plausibility.placeholder_end`` / ``incident_begin``).

Two stages are pinned here:

* the WL fetch (``wl_fetch.fetch_events``) with a payload in the shape of
  the WL API: it returns, the item is there, the edge field is ``None``,
  an ordinary item is unchanged;
* the feed chain: items as they sit in ``cache/wl/events.json`` with the
  edge value as a string, and as a provider hands them over with the edge
  value as a ``datetime``, go through the collection step, the chain and
  the rendering, and the item is in the feed.

The parsers themselves are pinned in ``tests/test_plausible_years.py``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.providers import wl_fetch
from src.utils import raw_capture
from src.utils.cache import read_cache, write_cache
from src.utils.serialize import serialize_for_cache
from tests._conftest.build_feed_bindings import part, setattr_all

CET = timezone(timedelta(hours=1))
WEST = timezone(timedelta(hours=-1))


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now`` is 2026-11-10 13:00 in Vienna."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


_FROZEN_NOW = _FrozenDatetime(2026, 11, 10, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

# The edge values as WL writes a time stamp.
WL_END_9999 = "9999-12-31T23:59:59.000+0100"
WL_YEAR_1 = "0001-01-01T00:00:00.000+0100"
WL_YEAR_1_WEST = "0001-01-01T00:00:00.000-0100"
WL_YEAR_2101 = "2101-01-01T00:00:00.000+0100"
WL_YEAR_1999 = "1999-12-31T23:59:59.000+0100"

# An ordinary disruption of the frozen day.
WL_START = "2026-11-10T11:10:00.000+0100"
WL_END = "2026-11-10T16:00:00.000+0100"
START = datetime(2026, 11, 10, 11, 10, tzinfo=CET)
END = datetime(2026, 11, 10, 16, 0, tzinfo=CET)

# An ordinary notice: published a week before, valid for three more weeks.
WL_NOTICE_START = "2026-11-03T09:00:00.000+0100"
WL_NOTICE_END = "2026-11-30T23:59:00.000+0100"
NOTICE_START = datetime(2026, 11, 3, 9, 0, tzinfo=CET)
NOTICE_END = datetime(2026, 11, 30, 23, 59, tzinfo=CET)


# --- The WL fetch ---------------------------------------------------------------


def _disruption(name: str, line: str, cause: str, *, start: str | None, end: str | None) -> dict[str, Any]:
    """One record in the shape of WL's trafficInfoList (data/raw/wl/trafficInfoList.json)."""
    time: dict[str, str] = {}
    if start is not None:
        time["start"] = start
    if end is not None:
        time["end"] = end
    return {
        "alertCause": "undefined alert cause",
        "alertCauseId": 255,
        "description": f"Wegen eines {cause}es kommt es auf der Linie {line} zu längeren Wartezeiten.",
        "name": name,
        "owner": "WL",
        "parentName": name,
        "priority": "1",
        "reasonId": 0,
        "reasonName": "undefined",
        "refTrafficInfoCategoryId": 1,
        "relatedLines": [line],
        "time": time,
        "title": f"{line}: {cause}",
    }


def _notice(name: str, line: str, *, start: str | None, end: str | None) -> dict[str, Any]:
    """One record in the shape of WL's newsList (data/raw/wl/newsList.json)."""
    time: dict[str, str] = {}
    if start is not None:
        time["start"] = start
        time["validFrom"] = start
    if end is not None:
        time["end"] = end
    return {
        "description": (
            f"<p>Wegen Bauarbeiten fahren die Züge der Linie {line} in der Station "
            "Nestroyplatz ohne Halt durch.</p>"
        ),
        "id": name,
        "location": {"properties": {"attributes": {}, "coordName": "WGS84", "type": "news"}, "type": "Feature"},
        "name": name,
        "refPoiCategoryId": 13296,
        "relatedLines": [line],
        "time": time,
        "title": f"{line}: Nestroyplatz, kein Halt",
    }


def _fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    infos: list[dict[str, Any]],
    pois: list[dict[str, Any]],
    *,
    frozen: bool = True,
) -> dict[str, dict[str, Any]]:
    """Run ``fetch_events`` on the two lists; the items by their title."""
    if frozen:
        monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime)
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    responses = {
        "trafficInfoList": {"data": {"trafficInfos": infos}, "message": {"value": "OK"}},
        "newsList": {"data": {"pois": pois}, "message": {"value": "OK"}},
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])
    items = wl_fetch.fetch_events()
    by_title = {str(item["title"]): item for item in items}
    assert len(by_title) == len(items)
    return by_title


def _ordinary_disruption() -> dict[str, Any]:
    return _disruption("I20261110-0004", "U4", "Rettungseinsatz", start=WL_START, end=WL_END)


def _assert_ordinary_disruption(item: dict[str, Any]) -> None:
    assert item["pubDate"] == START
    assert item["starts_at"] == START
    assert item["ends_at"] == END
    assert item["_incident_start"] == START
    assert item["_wl_ids"] == ["I20261110-0004"]
    assert item["category"] == "Störung"


def test_fetch_keeps_every_disruption_with_an_edge_time_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    infos = [
        _disruption("I20261110-0001", "U1", "Feuerwehreinsatz", start=WL_START, end=WL_END_9999),
        _disruption("I20261110-0002", "U2", "Polizeieinsatz", start=WL_YEAR_1, end=WL_END),
        _disruption("I20261110-0003", "U3", "Verkehrsunfall", start=WL_YEAR_1_WEST, end=WL_END),
        _ordinary_disruption(),
    ]

    items = _fetch(monkeypatch, tmp_path, infos, [])

    assert sorted(items) == [
        "U1: Feuerwehreinsatz",
        "U2: Polizeieinsatz",
        "U3: Verkehrsunfall",
        "U4: Rettungseinsatz",
    ]
    # The end of the year 9999 is "no end known"; the start stands.
    no_end = items["U1: Feuerwehreinsatz"]
    assert no_end["ends_at"] is None
    assert no_end["pubDate"] == START
    assert no_end["starts_at"] == START
    # A start of the year 1 is "unreadable"; the end stands.
    for title in ("U2: Polizeieinsatz", "U3: Verkehrsunfall"):
        no_start = items[title]
        assert no_start["pubDate"] is None
        assert no_start["starts_at"] is None
        assert "_incident_start" not in no_start
        assert no_start["ends_at"] == END
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])


def test_fetch_of_ordinary_records_gives_the_dates_wl_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pois = [_notice("N20261103-0001", "U1", start=WL_NOTICE_START, end=WL_NOTICE_END)]

    items = _fetch(monkeypatch, tmp_path, [_ordinary_disruption()], pois)

    assert sorted(items) == ["U1: Nestroyplatz, kein Halt", "U4: Rettungseinsatz"]
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])
    notice = items["U1: Nestroyplatz, kein Halt"]
    assert notice["category"] == "Hinweis"
    assert notice["pubDate"] == NOTICE_START
    assert notice["starts_at"] == NOTICE_START
    assert notice["ends_at"] == NOTICE_END


# Every edge value in ``time.end`` and in ``time.start``, one record each.
_EDGE_STAMPS = (WL_END_9999, WL_YEAR_1, WL_YEAR_1_WEST, WL_YEAR_2101, WL_YEAR_1999)


@pytest.mark.parametrize("stamp", _EDGE_STAMPS)
def test_disruption_with_an_edge_end_has_no_end(
    stamp: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    infos = [
        _disruption("I20261110-0001", "U1", "Feuerwehreinsatz", start=WL_START, end=stamp),
        _ordinary_disruption(),
    ]

    items = _fetch(monkeypatch, tmp_path, infos, [])

    assert sorted(items) == ["U1: Feuerwehreinsatz", "U4: Rettungseinsatz"]
    edge = items["U1: Feuerwehreinsatz"]
    assert edge["ends_at"] is None
    assert edge["pubDate"] == START
    assert edge["starts_at"] == START
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])


@pytest.mark.parametrize("stamp", _EDGE_STAMPS)
def test_disruption_with_an_edge_start_has_no_start(
    stamp: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    infos = [
        _disruption("I20261110-0002", "U2", "Polizeieinsatz", start=stamp, end=WL_END),
        _ordinary_disruption(),
    ]

    items = _fetch(monkeypatch, tmp_path, infos, [])

    assert sorted(items) == ["U2: Polizeieinsatz", "U4: Rettungseinsatz"]
    edge = items["U2: Polizeieinsatz"]
    assert edge["pubDate"] is None
    assert edge["starts_at"] is None
    assert "_incident_start" not in edge
    assert edge["ends_at"] == END
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])


@pytest.mark.parametrize("stamp", _EDGE_STAMPS)
def test_disruption_with_edge_start_and_end_has_neither(
    stamp: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    infos = [_disruption("I20261110-0003", "U3", "Verkehrsunfall", start=stamp, end=stamp)]

    items = _fetch(monkeypatch, tmp_path, infos, [])

    assert list(items) == ["U3: Verkehrsunfall"]
    edge = items["U3: Verkehrsunfall"]
    assert (edge["pubDate"], edge["starts_at"], edge["ends_at"]) == (None, None, None)


@pytest.mark.parametrize("stamp", _EDGE_STAMPS)
def test_notice_with_an_edge_end_has_no_end(
    stamp: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pois = [_notice("N20261103-0001", "U1", start=WL_NOTICE_START, end=stamp)]

    items = _fetch(monkeypatch, tmp_path, [_ordinary_disruption()], pois)

    assert sorted(items) == ["U1: Nestroyplatz, kein Halt", "U4: Rettungseinsatz"]
    edge = items["U1: Nestroyplatz, kein Halt"]
    assert edge["ends_at"] is None
    assert edge["pubDate"] == NOTICE_START
    assert edge["starts_at"] == NOTICE_START
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])


@pytest.mark.parametrize("stamp", _EDGE_STAMPS)
def test_notice_with_an_edge_start_has_no_start(
    stamp: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pois = [_notice("N20261103-0001", "U1", start=stamp, end=WL_NOTICE_END)]

    items = _fetch(monkeypatch, tmp_path, [_ordinary_disruption()], pois)

    assert sorted(items) == ["U1: Nestroyplatz, kein Halt", "U4: Rettungseinsatz"]
    edge = items["U1: Nestroyplatz, kein Halt"]
    assert edge["pubDate"] is None
    assert edge["starts_at"] is None
    assert edge["ends_at"] == NOTICE_END
    _assert_ordinary_disruption(items["U4: Rettungseinsatz"])


def test_edge_start_gives_way_to_the_next_time_stamp_of_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ``_best_ts``: without a readable ``time.start`` the item starts when WL
    # last updated it. An edge start is such an unreadable one.
    record = _disruption("I20261110-0002", "U2", "Polizeieinsatz", start=WL_YEAR_1, end=WL_END)
    record["updated"] = WL_START

    items = _fetch(monkeypatch, tmp_path, [record], [])

    edge = items["U2: Polizeieinsatz"]
    assert edge["pubDate"] == START
    assert edge["starts_at"] == START
    assert edge["ends_at"] == END


# --- The feed chain -------------------------------------------------------------

# The edge values as the cache holds them (``datetime.isoformat``), as WL
# wrote them, and as a provider hands them over.
_CHAIN_EDGES: tuple[tuple[str, Any], ...] = (
    ("9999 iso", "9999-12-31T23:59:59+01:00"),
    ("9999 wl", WL_END_9999),
    ("9999 datetime", datetime(9999, 12, 31, 23, 59, 59, tzinfo=CET)),
    ("9999 naive datetime", datetime(9999, 12, 31, 23, 59, 59)),
    # Midnight written as 24:00: the day after, which the calendar does not have.
    ("9999 24:00 iso", "9999-12-31T24:00:00+01:00"),
    ("9999 24:00 naive iso", "9999-12-31T24:00:00"),
    ("year 1 iso", "0001-01-01T00:00:00+01:00"),
    ("year 1 wl", WL_YEAR_1),
    ("year 1 datetime", datetime(1, 1, 1, tzinfo=CET)),
    ("year 1 naive datetime", datetime(1, 1, 1)),
    ("year 1 west iso", "0001-01-01T00:00:00-01:00"),
    ("year 1 west wl", WL_YEAR_1_WEST),
    ("year 1 west datetime", datetime(1, 1, 1, tzinfo=WEST)),
    ("2101 iso", "2101-01-01T00:00:00+01:00"),
    ("2101 datetime", datetime(2101, 1, 1, tzinfo=CET)),
    ("1999 iso", "1999-12-31T23:59:59+01:00"),
    ("1999 datetime", datetime(1999, 12, 31, 23, 59, 59, tzinfo=CET)),
)
_CHAIN_FIELDS = ("ends_at", "starts_at", "pubDate")


def _cached(guid: str, title: str, now: datetime, **fields: Any) -> dict[str, Any]:
    """A WL disruption with the keys of ``cache/wl/events.json``."""
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": "Wegen eines Rettungseinsatzes kommt es zu längeren Wartezeiten.",
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": guid,
        "pubDate": (now - timedelta(minutes=50)).isoformat(),
        "starts_at": (now - timedelta(minutes=50)).isoformat(),
        "ends_at": (now + timedelta(hours=4)).isoformat(),
        "_identity": f"wl|störung|{guid}",
    }
    item.update(fields)
    return item


def _collected(raw: list[dict[str, Any]]) -> list[FeedItem]:
    """*raw* with its dates normalised, as ``_collect_items`` does for every provider result."""
    items: list[FeedItem] = bf._normalize_item_datetimes(raw)
    return items


def _titles_in(rss: str) -> list[str]:
    return [str(node.text) for node in ET.fromstring(rss).findall("./channel/item/title")]


@pytest.mark.parametrize("field", _CHAIN_FIELDS)
@pytest.mark.parametrize("edge", [value for _, value in _CHAIN_EDGES], ids=[name for name, _ in _CHAIN_EDGES])
def test_cached_item_with_an_edge_date_reaches_the_feed(field: str, edge: Any) -> None:
    raw = [
        _cached("wl-edge", "U1: Feuerwehreinsatz", NOW, **{field: edge}),
        _cached("wl-ordinary", "U4: Rettungseinsatz", NOW),
    ]
    state: dict[str, Any] = {}

    ordered = bf._assemble_feed_items(_collected(raw), state, NOW)
    rss = bf._make_rss(ordered, NOW, state)

    assert raw[0][field] is None
    assert sorted(_titles_in(rss)) == ["U1: Feuerwehreinsatz", "U4: Rettungseinsatz"]
    assert sorted(str(item["title"]) for item in ordered) == ["U1: Feuerwehreinsatz", "U4: Rettungseinsatz"]


def test_cached_item_with_every_date_at_the_edge_reaches_the_feed() -> None:
    raw = [
        _cached(
            "wl-edge",
            "U1: Feuerwehreinsatz",
            NOW,
            pubDate="0001-01-01T00:00:00+01:00",
            starts_at=datetime(1, 1, 1, tzinfo=WEST),
            ends_at=datetime(9999, 12, 31, 23, 59, 59, tzinfo=CET),
        ),
        _cached("wl-ordinary", "U4: Rettungseinsatz", NOW),
    ]
    state: dict[str, Any] = {}

    rss = bf._make_rss(bf._assemble_feed_items(_collected(raw), state, NOW), NOW, state)

    assert (raw[0]["pubDate"], raw[0]["starts_at"], raw[0]["ends_at"]) == (None, None, None)
    assert sorted(_titles_in(rss)) == ["U1: Feuerwehreinsatz", "U4: Rettungseinsatz"]


def test_ordinary_cached_dates_stay_as_they_are() -> None:
    raw = _cached(
        "wl-ordinary",
        "U4: Rettungseinsatz",
        NOW,
        pubDate="2026-11-10T11:10:00+01:00",
        starts_at=START,
        ends_at="2026-11-10T16:00:00+01:00",
    )

    _collected([raw])

    assert raw["pubDate"] == START
    assert raw["starts_at"] is START
    assert raw["ends_at"] == END


# ``_incident_start`` is no field the collection step normalises: the chain
# reads it where it needs it (``time_line._wl_incident_start``).
_INCIDENT_EDGES = [(name, value) for name, value in _CHAIN_EDGES if "naive" not in name]


@pytest.mark.parametrize("edge", [value for _, value in _INCIDENT_EDGES], ids=[name for name, _ in _INCIDENT_EDGES])
def test_cached_item_with_an_edge_incident_start_reaches_the_feed(edge: Any) -> None:
    raw = [
        _cached("wl-edge", "U1: Feuerwehreinsatz", NOW, _wl_ids=["I20261110-0001"], _incident_start=edge),
        _cached("wl-ordinary", "U4: Rettungseinsatz", NOW),
    ]
    state: dict[str, Any] = {}

    rss = bf._make_rss(bf._assemble_feed_items(_collected(raw), state, NOW), NOW, state)

    assert sorted(_titles_in(rss)) == ["U1: Feuerwehreinsatz", "U4: Rettungseinsatz"]


@pytest.mark.parametrize("edge", [value for _, value in _INCIDENT_EDGES], ids=[name for name, _ in _INCIDENT_EDGES])
def test_notice_stays_beside_an_incident_with_an_edge_start(edge: Any) -> None:
    # ``wl_notices._yield_notices_to_incidents`` compares the notice with the
    # start of every running incident of its lines; an edge start is none.
    notice = _cached(
        "wl-notice",
        "U1: Gleisbauarbeiten",
        NOW,
        category="Hinweis",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(hours=6),
    )
    incident = _cached("wl-incident", "U1: Feuerwehreinsatz", NOW, _wl_ids=["I20261110-0001"], _incident_start=edge)

    kept = bf._yield_notices_to_incidents(_collected([notice, incident]), NOW)

    assert [str(item["title"]) for item in kept] == ["U1: Gleisbauarbeiten", "U1: Feuerwehreinsatz"]


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
@pytest.mark.parametrize("edge", [value for _, value in _CHAIN_EDGES], ids=[name for name, _ in _CHAIN_EDGES])
def test_notice_with_an_edge_date_stays_beside_an_incident(field: str, edge: Any) -> None:
    # Here the dates are read where they are used, without the collection step.
    notice: Any = _cached("wl-notice", "U1: Gleisbauarbeiten", NOW, category="Hinweis", **{field: edge})
    incident: Any = _cached(
        "wl-incident", "U1: Feuerwehreinsatz", NOW, _wl_ids=["I20261110-0001"], _incident_start=START, pubDate=START
    )

    kept = bf._yield_notices_to_incidents([notice, incident], NOW)

    assert [str(item["title"]) for item in kept] == ["U1: Gleisbauarbeiten", "U1: Feuerwehreinsatz"]


# --- The whole build ------------------------------------------------------------

_ALL_TITLES = ["U1: Feuerwehreinsatz", "U2: Polizeieinsatz", "U3: Verkehrsunfall", "U4: Rettungseinsatz"]


def _redirect_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every file ``main`` writes at *tmp_path*; the path of the feed."""
    out_file = tmp_path / "feed.xml"
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", tmp_path / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", tmp_path / "feed-health.json")
    monkeypatch.setattr(bf, "_save_state", lambda state, deletions=None: None)
    monkeypatch.setattr(bf, "_load_state", lambda: {})
    # ``main`` would read the configuration from the environment again.
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    return out_file


def _write_wl_cache(items: list[Any]) -> None:
    """Write *items* as ``scripts/update_wl_cache.py`` does.

    ``_cache_dir_stays`` (tests/_conftest/netz.py) points the cache at the
    test's own directory.
    """
    write_cache("wl", [serialize_for_cache(item) for item in items])


def _edge_items(now: datetime) -> list[dict[str, Any]]:
    """The three items of the replay plus an ordinary one, dates as ``datetime``."""
    year_1 = datetime(1, 1, 1, tzinfo=CET)
    year_1_west = datetime(1, 1, 1, tzinfo=WEST)
    return [
        _cached("wl-end-9999", "U1: Feuerwehreinsatz", now, ends_at=datetime(9999, 12, 31, 23, 59, 59, tzinfo=CET)),
        _cached("wl-start-1", "U2: Polizeieinsatz", now, starts_at=year_1, pubDate=year_1),
        _cached("wl-start-1-west", "U3: Verkehrsunfall", now, starts_at=year_1_west, pubDate=year_1_west),
        _cached("wl-ordinary", "U4: Rettungseinsatz", now),
    ]


def _assert_build_holds_every_item(out_file: Path, tmp_path: Path) -> None:
    assert sorted(_titles_in(out_file.read_text(encoding="utf-8"))) == _ALL_TITLES
    health = json.loads((tmp_path / "feed-health.json").read_text(encoding="utf-8"))
    # No item was left out of the run (``src.feed_build.isolation``).
    assert health.get("skipped_items", []) == []


def test_build_from_a_cache_file_with_edge_dates_holds_every_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_file = _redirect_build(monkeypatch, tmp_path)
    _write_wl_cache(_edge_items(datetime.now(UTC)))
    cached = json.dumps(read_cache("wl"))
    # The file holds the edge values as the serialiser writes a datetime.
    assert "9999-12-31T23:59:59+01:00" in cached
    assert "0001-01-01T00:00:00+01:00" in cached
    assert "0001-01-01T00:00:00-01:00" in cached

    assert bf.main() == 0

    _assert_build_holds_every_item(out_file, tmp_path)


def test_build_from_a_cache_file_with_a_date_beyond_the_calendar_holds_every_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The parser itself cannot hold this value (``OverflowError`` inside
    # ``isoparse``). The collection step reads it before any item is
    # isolated, so the build ended with "Feed-Bau fehlgeschlagen".
    out_file = _redirect_build(monkeypatch, tmp_path)
    items = _edge_items(datetime.now(UTC))
    items[0]["ends_at"] = "9999-12-31T24:00:00+01:00"
    _write_wl_cache(items)
    assert "9999-12-31T24:00:00+01:00" in json.dumps(read_cache("wl"))

    assert bf.main() == 0

    _assert_build_holds_every_item(out_file, tmp_path)


def test_build_from_a_provider_with_edge_datetimes_holds_every_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_file = _redirect_build(monkeypatch, tmp_path)
    items = _edge_items(datetime.now(UTC))

    def provider_items(provider: str) -> list[Any]:
        return list(items) if provider == "wl" else []

    monkeypatch.setattr(part(bf, "providers_setup"), "read_cache", provider_items)

    assert bf.main() == 0

    _assert_build_holds_every_item(out_file, tmp_path)


def test_wl_payload_with_edge_time_stamps_reaches_the_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fetch, cache and build in one go, on today's clock: ``main`` reads it.
    now = datetime.now(UTC).replace(microsecond=0)
    wl_format = "%Y-%m-%dT%H:%M:%S.000+0000"
    start = (now - timedelta(minutes=50)).strftime(wl_format)
    end = (now + timedelta(hours=4)).strftime(wl_format)
    infos = [
        _disruption("I20261110-0001", "U1", "Feuerwehreinsatz", start=start, end=WL_END_9999),
        _disruption("I20261110-0002", "U2", "Polizeieinsatz", start=WL_YEAR_1, end=end),
        _disruption("I20261110-0003", "U3", "Verkehrsunfall", start=WL_YEAR_1_WEST, end=end),
        _disruption("I20261110-0004", "U4", "Rettungseinsatz", start=start, end=end),
    ]
    out_file = _redirect_build(monkeypatch, tmp_path)

    fetched = _fetch(monkeypatch, tmp_path, infos, [], frozen=False)
    _write_wl_cache(list(fetched.values()))

    assert sorted(fetched) == _ALL_TITLES
    assert fetched["U1: Feuerwehreinsatz"]["ends_at"] is None
    assert fetched["U4: Rettungseinsatz"]["ends_at"] == now + timedelta(hours=4)
    assert bf.main() == 0
    _assert_build_holds_every_item(out_file, tmp_path)
