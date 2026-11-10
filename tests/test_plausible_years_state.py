"""The plausible years in the state file (``data/first_seen.json``).

Operator decision of 2026-11-10: a time stamp whose year lies outside 2000
to 2100 counts as not set (``src.utils.vienna_clock.PLAUSIBLE_YEARS``). The
build writes the state file itself and reads its time stamps through
``feed_build.base._state_stamp``. A ``first_seen`` outside the range counts
as unreadable there: the entry stays, ``first_seen`` becomes the current
time and a warning names the value.

Before, the loader took such a value when it could be moved to UTC:
``9999-12-31T23:59:59+00:00`` stayed in the state and was written into the
feed as the ``first_seen`` of its item, and an entry of the year 1 fell out
of the state as older than the retention, so its item counted as new. A
value that cannot be moved to UTC (``0001-01-01T00:00:00+01:00``) in a
state handed to the chain or the rendering raised ``OverflowError`` there.

Four groups:

* ``_state_stamp`` itself: the value as written, or ``None``;
* ``_load_state`` with a state file;
* ``_update_item_state``, the step of the rendering that reads ``first_seen``;
* the chain and the rendering with such a state handed in, and ``main()``
  with such a state file.

``_parse_state_time`` is pinned in ``tests/test_plausible_years_time_zone.py``,
the list of the functions that apply the rule in ``tests/test_plausible_years.py``.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest

import src.build_feed as bf
from src.feed_build.base import _state_stamp
from tests._conftest.build_feed_bindings import setattr_all
from tests.test_build_feed_skipped_item import _build, _ledger_rows, _wl

CET = timezone(timedelta(hours=1))
WEST = timezone(timedelta(hours=-1))
NOW = datetime(2026, 11, 10, 13, 0, tzinfo=CET)
FIRST_SEEN = "{https://wien-oepnv.example/schema}first_seen"

# A first_seen outside the plausible years, in the forms ``fromisoformat`` reads.
EDGE_VALUES = (
    # As ``_save_state`` writes a time stamp.
    "9999-12-31T23:59:59+00:00",
    "0001-01-01T00:00:00+00:00",
    "2101-01-01T00:00:00+00:00",
    "1999-12-31T23:59:59.999999+00:00",
    # These two cannot be moved to UTC.
    "9999-12-31T23:59:59-01:00",
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:59:59+01:00",
    "0001-01-01T00:00:00-01:00",
    "9999-12-31T23:59:59Z",
    "9999-12-31T23:59:59.000+0100",
    # Without an offset, and a day alone.
    "9999-12-31T23:59:59",
    "0001-01-01T00:00:00",
    "9999-12-31",
    "0001-01-01",
    # The year is read as written: both are 2000 and 2100 in UTC.
    "1999-12-31T23:30:00-01:00",
    "2101-01-01T00:30:00+01:00",
)
# Aware values inside the range, with the moment each one names.
ORDINARY_VALUES = [
    ("2026-11-10T07:15:00+00:00", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
    ("2026-11-10T08:15:00+01:00", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
    ("2026-11-10T06:15:00-01:00", datetime(2026, 11, 10, 6, 15, tzinfo=WEST)),
    ("2026-11-10T07:15:00Z", datetime(2026, 11, 10, 7, 15, tzinfo=UTC)),
    ("2026-11-10T07:15:00.123456+00:00", datetime(2026, 11, 10, 7, 15, 0, 123456, tzinfo=UTC)),
    ("2026-11-10T08:15:00.000+0100", datetime(2026, 11, 10, 8, 15, tzinfo=CET)),
    # Both boundaries are inclusive, in the year as written.
    ("2000-01-01T00:00:00+01:00", datetime(2000, 1, 1, tzinfo=CET)),
    ("2100-12-31T23:59:59-01:00", datetime(2100, 12, 31, 23, 59, 59, tzinfo=WEST)),
]


def _warnings(caplog: pytest.LogCaptureFixture, text: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and text in record.getMessage()
    ]


# --- _state_stamp -----------------------------------------------------------------


@pytest.mark.parametrize("value", EDGE_VALUES)
def test_state_stamp_outside_the_plausible_years_is_not_set(value: str) -> None:
    assert _state_stamp(value) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "kein Datum",
        " 2026-11-10T07:15:00+00:00",
        # Outside what a datetime can hold.
        "9999-12-31T24:00:00",
        "10000-01-01T00:00:00+00:00",
        "0000-01-01T00:00:00+00:00",
        # No string.
        5,
        0,
        1794298500,
        253402297199.0,
        True,
        ["2026-11-10T07:15:00+00:00"],
        {"first_seen": "2026-11-10T07:15:00+00:00"},
    ],
)
def test_state_stamp_that_cannot_be_read_is_not_set(value: object) -> None:
    assert _state_stamp(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        *ORDINARY_VALUES,
        # A value without an offset stays without a zone; the callers attach UTC.
        ("2026-11-10T07:15:00", datetime(2026, 11, 10, 7, 15)),
        ("2026-11-10", datetime(2026, 11, 10)),
        ("2000-01-01T00:00:00", datetime(2000, 1, 1)),
        ("2100-12-31T23:59:59.999999", datetime(2100, 12, 31, 23, 59, 59, 999999)),
    ],
)
def test_state_stamp_inside_the_plausible_years_is_returned_as_written(value: str, expected: datetime) -> None:
    result = _state_stamp(value)

    assert result is not None
    assert result == expected
    assert result.tzinfo == expected.tzinfo


# --- _load_state ------------------------------------------------------------------


def _load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stored: dict[str, Any], *, retention_days: int
) -> dict[str, dict[str, Any]]:
    """What ``_load_state`` reads from a state file holding *stored*."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", path)
    monkeypatch.setattr(bf.feed_config, "STATE_RETENTION_DAYS", retention_days)
    loaded: dict[str, dict[str, Any]] = bf._load_state()
    return loaded


@pytest.mark.parametrize("retention_days", [0, 60])
def test_loading_keeps_an_entry_with_an_edge_first_seen_and_sets_it_to_now(
    retention_days: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    last_seen = "2026-11-10T11:00:00+00:00"
    stored = {f"guid-{number}": {"first_seen": value, "last_seen": last_seen} for number, value in enumerate(EDGE_VALUES)}

    before = datetime.now(UTC)
    with caplog.at_level(logging.WARNING):
        loaded = _load(monkeypatch, tmp_path, stored, retention_days=retention_days)
    after = datetime.now(UTC)

    assert list(loaded) == list(stored)
    for entry in loaded.values():
        first_seen = datetime.fromisoformat(entry["first_seen"])
        assert first_seen.utcoffset() == timedelta(0)
        assert before <= first_seen <= after
        assert entry == {"first_seen": entry["first_seen"], "last_seen": last_seen}
    # One warning per entry, with the value as the file holds it.
    assert _warnings(caplog, "unparsebares first_seen") == [
        f"State-Eintrag guid-{number} hat unparsebares first_seen: {value!r}" for number, value in enumerate(EDGE_VALUES)
    ]


def test_loading_reads_ordinary_entries_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stored = {
        "utc": {"first_seen": "2026-11-10T07:15:00+00:00", "last_seen": "2026-11-10T11:00:00+00:00"},
        "offset": {"first_seen": "2026-11-10T08:15:00+01:00", "translations": {"en": {"title": "U1: Delay"}}},
        "zulu": {"first_seen": "2026-11-10T07:15:00Z"},
        "fraction": {"first_seen": "2026-11-10T07:15:00.123456+00:00"},
        # A value without an offset is UTC.
        "naive": {"first_seen": "2026-11-10T07:15:00"},
        "day": {"first_seen": "2026-11-10"},
        # Both boundaries are inclusive.
        "first": {"first_seen": "2000-01-01T00:00:00+00:00"},
        "last": {"first_seen": "2100-12-31T23:59:59+00:00"},
        "last-naive": {"first_seen": "2100-12-31T23:59:59"},
        # The year as written counts; the loader then writes the value in UTC.
        "first-as-written": {"first_seen": "2000-01-01T00:00:00+01:00"},
        "last-as-written": {"first_seen": "2100-12-31T23:59:59-01:00"},
        # No entry: left out, as before.
        "no-entry": "2026-11-10T07:15:00+00:00",
    }

    with caplog.at_level(logging.WARNING):
        loaded = _load(monkeypatch, tmp_path, stored, retention_days=0)

    assert loaded == {
        "utc": {"first_seen": "2026-11-10T07:15:00+00:00", "last_seen": "2026-11-10T11:00:00+00:00"},
        "offset": {"first_seen": "2026-11-10T07:15:00+00:00", "translations": {"en": {"title": "U1: Delay"}}},
        "zulu": {"first_seen": "2026-11-10T07:15:00+00:00"},
        "fraction": {"first_seen": "2026-11-10T07:15:00.123456+00:00"},
        "naive": {"first_seen": "2026-11-10T07:15:00+00:00"},
        "day": {"first_seen": "2026-11-10T00:00:00+00:00"},
        "first": {"first_seen": "2000-01-01T00:00:00+00:00"},
        "last": {"first_seen": "2100-12-31T23:59:59+00:00"},
        "last-naive": {"first_seen": "2100-12-31T23:59:59+00:00"},
        "first-as-written": {"first_seen": "1999-12-31T23:00:00+00:00"},
        "last-as-written": {"first_seen": "2101-01-01T00:59:59+00:00"},
    }
    assert _warnings(caplog, "first_seen") == []


def test_retention_still_drops_an_old_entry_and_keeps_an_edge_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    recent = (now - timedelta(days=59)).isoformat()
    stored = {
        "old": {"first_seen": (now - timedelta(days=61)).isoformat()},
        "recent": {"first_seen": recent},
        # Not older than the retention: unreadable, so seen now.
        "year-1": {"first_seen": "0001-01-01T00:00:00+00:00"},
        "year-1999": {"first_seen": "1999-12-31T23:59:59+00:00"},
    }

    loaded = _load(monkeypatch, tmp_path, stored, retention_days=60)

    assert list(loaded) == ["recent", "year-1", "year-1999"]
    assert loaded["recent"] == {"first_seen": recent}
    assert all(datetime.fromisoformat(loaded[key]["first_seen"]) >= now for key in ("year-1", "year-1999"))


# --- _update_item_state -----------------------------------------------------------


@pytest.mark.parametrize("value", EDGE_VALUES)
def test_rendering_step_sets_an_edge_first_seen_to_now(value: str, caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, dict[str, Any]] = {"guid-1": {"first_seen": value, "last_seen": "2026-11-10T11:00:00+00:00"}}

    with caplog.at_level(logging.WARNING):
        ident, first_seen = bf._update_item_state(_wl(1, NOW), NOW, state)

    assert ident == "guid-1"
    assert first_seen == NOW
    assert first_seen.utcoffset() == timedelta(0)
    assert state == {"guid-1": {"first_seen": "2026-11-10T12:00:00+00:00", "last_seen": "2026-11-10T11:00:00+00:00"}}
    assert _warnings(caplog, "first_seen") == [f"first_seen Parsefehler: {value!r} – fallback to now"]
    # The item was known: it is no new disruption for the statistics.
    assert _ledger_rows() == []


@pytest.mark.parametrize("value", ["kein Datum", "", None, 5])
def test_rendering_step_sets_an_unreadable_first_seen_to_now_as_before(
    value: object, caplog: pytest.LogCaptureFixture
) -> None:
    state: dict[str, dict[str, Any]] = {"guid-1": {"first_seen": value}}

    with caplog.at_level(logging.WARNING):
        _ident, first_seen = bf._update_item_state(_wl(1, NOW), NOW, state)

    assert first_seen == NOW
    assert state == {"guid-1": {"first_seen": "2026-11-10T12:00:00+00:00"}}
    assert _warnings(caplog, "first_seen") == [f"first_seen Parsefehler: {value!r} – fallback to now"]


@pytest.mark.parametrize(("value", "expected"), ORDINARY_VALUES)
def test_rendering_step_reads_an_ordinary_first_seen_as_written(
    value: str, expected: datetime, caplog: pytest.LogCaptureFixture
) -> None:
    state: dict[str, dict[str, Any]] = {"guid-1": {"first_seen": value}}

    with caplog.at_level(logging.WARNING):
        ident, first_seen = bf._update_item_state(_wl(1, NOW), NOW, state)

    assert ident == "guid-1"
    assert first_seen == expected
    assert first_seen.utcoffset() == expected.utcoffset()
    assert state == {"guid-1": {"first_seen": value}}
    assert _warnings(caplog, "first_seen") == []


# --- The chain, the rendering and the whole build ---------------------------------


def _feed_moments(rss: str, tag: str) -> dict[str, datetime | None]:
    """The date under *tag* of every item of the feed text *rss*, by guid."""
    moments: dict[str, datetime | None] = {}
    for item in ET.fromstring(rss).findall("./channel/item"):
        text = item.findtext(tag)
        moments[item.findtext("guid") or ""] = None if text is None else parsedate_to_datetime(text)
    return moments


def _to_the_second(moment: object) -> datetime:
    """*moment* as the feed writes it: without the fraction of a second."""
    assert isinstance(moment, datetime)
    return moment.replace(microsecond=0)


@pytest.mark.parametrize("with_pub_date", [True, False], ids=["pubDate", "no pubDate"])
@pytest.mark.parametrize("value", EDGE_VALUES)
def test_chain_and_rendering_take_a_state_with_an_edge_first_seen(
    value: str, with_pub_date: bool, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    items = [_wl(1, now), _wl(2, now)]
    if not with_pub_date:
        # The rendering then computes the age of the item from first_seen.
        for item in items:
            item["pubDate"] = None
    ordinary = now - timedelta(minutes=2)
    state: dict[str, dict[str, Any]] = {"guid-1": {"first_seen": value}, "guid-2": {"first_seen": ordinary.isoformat()}}

    with caplog.at_level(logging.WARNING):
        ordered = bf._assemble_feed_items(items, state, now)
        rss = bf._make_rss(ordered, now, state)

    assert sorted(str(item["guid"]) for item in ordered) == ["guid-1", "guid-2"]
    assert _feed_moments(rss, FIRST_SEEN) == {"guid-1": now, "guid-2": ordinary}
    assert (state["guid-1"]["first_seen"], state["guid-2"]["first_seen"]) == (now.isoformat(), ordinary.isoformat())
    if with_pub_date:
        assert _feed_moments(rss, "pubDate") == {str(item["guid"]): item["pubDate"] for item in items}
    else:
        # Both were first seen inside the window in which the build time is the pubDate.
        assert _feed_moments(rss, "pubDate") == {"guid-1": now, "guid-2": now}
    assert _warnings(caplog, "first_seen") == [f"first_seen Parsefehler: {value!r} – fallback to now"]
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []
    # Both items were known: neither is a new disruption for the statistics.
    assert _ledger_rows() == []


def test_build_with_edge_first_seen_in_the_state_file_shows_every_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 6)]
    ordinary = (now - timedelta(minutes=2)).isoformat()
    stored = {
        "guid-1": {"first_seen": "9999-12-31T23:59:59+00:00"},
        "guid-2": {"first_seen": "0001-01-01T00:00:00+01:00"},
        "guid-3": {"first_seen": "0001-01-01T00:00:00+00:00"},
        "guid-4": {"first_seen": "9999-12-31T23:59:59"},
        "guid-5": {"first_seen": ordinary},
    }

    run = _build(monkeypatch, tmp_path / "run", collected, stored)
    finished = datetime.now(UTC)

    assert run.code == 0
    assert sorted(run.guids()) == sorted(stored)
    assert sorted(run.guids("feed.en.xml")) == sorted(stored)
    assert run.health["skipped_items"] == []
    # Every item was in the state: none is new, none enters the statistics.
    assert run.health["metrics"]["new_items"] == 0
    assert _ledger_rows() == []
    for name in ("feed.xml", "feed.en.xml"):
        rss = (run.directory / name).read_text(encoding="utf-8")
        published = _feed_moments(rss, "pubDate")
        assert published == {str(item["guid"]): _to_the_second(item["pubDate"]) for item in collected}
        first_seen = _feed_moments(rss, FIRST_SEEN)
        assert first_seen["guid-5"] == _to_the_second(datetime.fromisoformat(ordinary))
        for guid in ("guid-1", "guid-2", "guid-3", "guid-4"):
            moment = first_seen[guid]
            assert moment is not None and _to_the_second(now) <= moment <= finished
    # The state the build saved holds the repaired values.
    assert run.state["guid-5"]["first_seen"] == ordinary
    for guid in ("guid-1", "guid-2", "guid-3", "guid-4"):
        assert now <= datetime.fromisoformat(run.state[guid]["first_seen"]) <= finished
