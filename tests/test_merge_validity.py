"""``deduplicate_fuzzy`` keeps the validity of both merged items.

It used to keep only the survivor's ``starts_at`` / ``ends_at``. On
2026-10-01 the WL notice ``D: Gleisbauarbeiten Althanstraße`` absorbed the
disruption ``D: Gleisbauarbeiten`` of the same works; had the notice ended
first, the merged item would have left the feed while the disruption still
ran.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.feed.merge import deduplicate_fuzzy


def _d(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _wl(category: str, title: str, identity: str, start: str, end: str | None) -> dict[str, Any]:
    return {
        "source": "Wiener Linien",
        "category": category,
        "title": title,
        "description": f"Text {identity}",
        "_identity": identity,
        "guid": identity,
        "starts_at": _d(start),
        "ends_at": _d(end) if end else None,
    }


def _notice(end: str | None = "2026-10-02T03:00+02:00") -> dict[str, Any]:
    return _wl("Hinweis", "D: Gleisbauarbeiten Althanstraße", "wl|hinweis|L=d", "2026-09-15T00:00+02:00", end)


def _disruption(end: str | None = "2026-11-07T01:00+01:00") -> dict[str, Any]:
    return _wl("Störung", "D: Gleisbauarbeiten", "wl|störung|L=d", "2026-09-28T04:30+02:00", end)


def test_merged_item_runs_until_the_later_end() -> None:
    merged = deduplicate_fuzzy([_disruption(), _notice()])

    assert len(merged) == 1
    assert merged[0]["category"] == "Hinweis"  # the survivor stays the survivor
    assert merged[0]["starts_at"] == _d("2026-09-15T00:00+02:00")
    assert merged[0]["ends_at"] == _d("2026-11-07T01:00+01:00")


def test_merged_item_starts_at_the_earlier_start() -> None:
    notice = _notice()
    notice["starts_at"] = _d("2026-09-30T00:00+02:00")

    merged = deduplicate_fuzzy([_disruption(), notice])

    assert merged[0]["starts_at"] == _d("2026-09-28T04:30+02:00")


def test_open_end_of_either_keeps_the_merged_item_open() -> None:
    assert deduplicate_fuzzy([_disruption(end=None), _notice()])[0]["ends_at"] is None
    assert deduplicate_fuzzy([_disruption(), _notice(end=None)])[0]["ends_at"] is None


def test_unorderable_datetimes_keep_the_survivor_span() -> None:
    disruption = _disruption()
    disruption["starts_at"] = datetime(2026, 9, 1)  # naive
    disruption["ends_at"] = datetime(2026, 12, 1)  # naive

    merged = deduplicate_fuzzy([disruption, _notice()])

    assert merged[0]["starts_at"] == _d("2026-09-15T00:00+02:00")
    assert merged[0]["ends_at"] == _d("2026-10-02T03:00+02:00")


def test_unorderable_end_keeps_the_whole_survivor_span() -> None:
    disruption = _disruption()
    disruption["starts_at"] = _d("2026-09-01T00:00+02:00")
    disruption["ends_at"] = datetime(2026, 12, 1)  # naive

    merged = deduplicate_fuzzy([disruption, _notice()])

    assert merged[0]["starts_at"] == _d("2026-09-15T00:00+02:00")
    assert merged[0]["ends_at"] == _d("2026-10-02T03:00+02:00")
