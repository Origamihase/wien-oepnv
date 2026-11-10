"""The state file beyond an ordinary ``first_seen`` string.

Companion of ``tests/test_plausible_years_state.py`` (operator decision of
2026-11-10: a time stamp whose year lies outside 2000 to 2100 counts as not
set). Three things that file leaves open:

* the other time fields of an entry (``last_seen``, ``members_seen``,
  ``earliest_published``, ``first_published``, ``announced_start``). The
  build reads them through ``_parse_state_time``; before the change a value
  that cannot be moved to UTC in one of them ended ``main()`` with
  ``OverflowError``;
* a ``first_seen`` that is no string. The loader reads what ``str()`` makes
  of it, as before; the step of the rendering takes only a string, as
  before;
* an entry without ``first_seen``.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import src.build_feed as bf
from src.feed_build.base import _parse_state_time
from src.feed_types import FeedItem
from tests.test_build_feed_skipped_item import _build, _wl
from tests.test_plausible_years_state import _load, _warnings

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
# Neither can be moved to UTC; the third and fourth can, into a year nobody can show.
EDGE_VALUES = (
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:59:59-01:00",
    "9999-12-31T23:59:59+00:00",
    "0001-01-01T00:00:00+00:00",
)
FIELDS = ("last_seen", "members_seen", "earliest_published", "first_published", "announced_start")


def _oebb(number: int, now: datetime) -> FeedItem:
    item = _wl(number, now)
    item["source"] = "ÖBB"
    item["title"] = f"Wien Ort{number} ↔ Wien Platz{number}: Bauarbeiten"
    return item


@pytest.mark.parametrize("value", EDGE_VALUES)
@pytest.mark.parametrize("field", FIELDS)
def test_build_with_an_edge_value_in_another_time_field_shows_every_item(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    collected = [_wl(1, now), _wl(2, now), _oebb(3, now), _oebb(4, now), _wl(5, now)]
    seen = (now - timedelta(minutes=30)).isoformat()
    stored = {f"guid-{number}": {"first_seen": seen, field: value} for number in range(1, 6)}

    run = _build(monkeypatch, tmp_path / "run", collected, stored)

    assert run.code == 0
    assert sorted(run.guids()) == sorted(stored)
    assert sorted(run.guids("feed.en.xml")) == sorted(stored)
    assert run.health["skipped_items"] == []


@pytest.mark.parametrize("field", FIELDS)
def test_state_time_of_every_field_follows_the_plausible_years(field: str) -> None:
    for value in EDGE_VALUES:
        assert _parse_state_time({field: value}, field) is None
    assert _parse_state_time({field: "2026-11-10T08:15:00+01:00"}, field) == datetime(2026, 11, 10, 7, 15, tzinfo=UTC)
    # A number is read as the text it is written as: a day.
    assert _parse_state_time({field: 20261110}, field) == datetime(2026, 11, 10, tzinfo=UTC)


def test_loading_reads_a_number_as_the_text_it_is_written_as(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stored = {"day": {"first_seen": 20261110}, "edge-day": {"first_seen": 99991231}, "other": {"first_seen": 5}}

    before = datetime.now(UTC)
    with caplog.at_level(logging.WARNING):
        loaded = _load(monkeypatch, tmp_path, stored, retention_days=0)

    assert loaded["day"] == {"first_seen": "2026-11-10T00:00:00+00:00"}
    for key in ("edge-day", "other"):
        assert before <= datetime.fromisoformat(loaded[key]["first_seen"]) <= datetime.now(UTC)
    assert _warnings(caplog, "first_seen") == [
        "State-Eintrag edge-day hat unparsebares first_seen: 99991231",
        "State-Eintrag other hat unparsebares first_seen: 5",
    ]


def test_loading_keeps_an_entry_without_first_seen_and_sets_it_to_now(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stored = {"guid-1": {"last_seen": "2026-11-10T11:00:00+00:00"}}

    before = datetime.now(UTC)
    with caplog.at_level(logging.WARNING):
        loaded = _load(monkeypatch, tmp_path, stored, retention_days=60)

    assert list(loaded) == ["guid-1"]
    assert loaded["guid-1"]["last_seen"] == "2026-11-10T11:00:00+00:00"
    assert before <= datetime.fromisoformat(loaded["guid-1"]["first_seen"]) <= datetime.now(UTC)
    assert _warnings(caplog, "first_seen") == ["State-Eintrag guid-1 hat unparsebares first_seen: None"]


@pytest.mark.parametrize(
    "value",
    [20261110, 20000101, 2026.1110, True, datetime(2026, 11, 10, 7, 15, tzinfo=UTC), date(2026, 11, 10), ["2026-11-10"]],
)
def test_rendering_step_takes_only_a_string_as_first_seen(value: object, caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, dict[str, Any]] = {"guid-1": {"first_seen": value}}

    with caplog.at_level(logging.WARNING):
        ident, first_seen = bf._update_item_state(_wl(1, NOW), NOW, state)

    assert ident == "guid-1"
    assert first_seen == NOW
    assert state == {"guid-1": {"first_seen": "2026-11-10T12:00:00+00:00"}}
    assert _warnings(caplog, "first_seen") == [f"first_seen Parsefehler: {value!r} – fallback to now"]
