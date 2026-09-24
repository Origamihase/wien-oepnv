"""One ÖBB route, several construction phases: one slot, the earliest phase.

The ÖBB cache carries three items with the title ``Wien Hauptbahnhof ↔
Gramatneusiedl`` (03.–05.10., 31.10.–30.11., 05.–07.12.2026; three GUIDs,
three different texts). All three survive ``_dedupe_items`` and
``deduplicate_fuzzy`` and the topic budget (a route title carries no reason
word). So far the flood of newer Wiener-Linien items kept them below the ten
slots — not the code. On a quiet day the same line would stand three times
on the displays, and two other disruptions would lose their slot.

``_defer_repeated_route_titles`` keeps the item whose window starts first in
its own place and moves the other phases behind the field. The items are not
merged: the phases are different measures, one text for all would misstate
two of them. Nothing is dropped.

Mutations checked against this file (each one caught, by the test named):

* the lead is the first member in sorted order instead of the earliest
  window → ``test_the_earliest_window_keeps_the_slot``.
* the ÖBB source check is dropped →
  ``test_other_sources_are_left_alone``.
* the pass is not wired into ``main()`` →
  ``test_the_pass_is_wired_into_the_build``.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree as ET

import pytest

from src.build_feed import _defer_repeated_route_titles
from src.feed_types import FeedItem

_ROUTE = "Wien Hauptbahnhof ↔ Gramatneusiedl"


def _item(
    title: str,
    guid: str,
    starts_at: str | None,
    *,
    source: str = "ÖBB",
) -> FeedItem:
    return cast(
        FeedItem,
        {"source": source, "title": title, "guid": guid, "starts_at": starts_at, "description": ""},
    )


# The captured cache order after the newest-first sort (first_seen 10.09.,
# 24.08., 08.07.): the December phase leads, the October one trails.
_CAPTURED = [
    _item(_ROUTE, "dec", "2026-12-05T00:00:00+01:00"),
    _item("REX 6: Wien Meidling ↔ Ebreichsdorf", "rex", "2026-10-01T00:00:00+02:00"),
    _item(_ROUTE, "nov", "2026-10-31T00:00:00+01:00"),
    _item("S 45: Wien Hütteldorf ↔ Wien Handelskai", "s45", "2026-11-01T01:10:00+01:00"),
    _item(_ROUTE, "oct", "2026-10-03T00:00:00+02:00"),
]


def _guids(items: list[FeedItem]) -> list[str]:
    return [str(i["guid"]) for i in items]


def test_the_earliest_window_keeps_the_slot() -> None:
    out = _defer_repeated_route_titles(list(_CAPTURED))
    # The October phase stays where it is; December and November move behind
    # the field in their original order.
    assert _guids(out) == ["rex", "s45", "oct", "dec", "nov"]


def test_nothing_is_dropped() -> None:
    out = _defer_repeated_route_titles(list(_CAPTURED))
    assert sorted(_guids(out)) == sorted(_guids(_CAPTURED))


def test_a_single_route_item_is_left_alone() -> None:
    items = [_item(_ROUTE, "a", "2026-10-03T00:00:00+02:00"), _item("S 1: Wien Leopoldau ↔ Deutsch Wagram", "b", None)]
    assert _defer_repeated_route_titles(items) == items


def test_whitespace_and_case_do_not_split_a_route() -> None:
    items = [
        _item("Wien Hauptbahnhof ↔  Gramatneusiedl", "late", "2026-12-05T00:00:00+01:00"),
        _item("wien hauptbahnhof ↔ gramatneusiedl", "early", "2026-10-03T00:00:00+02:00"),
    ]
    assert _guids(_defer_repeated_route_titles(items)) == ["early", "late"]


def test_a_missing_start_counts_as_latest() -> None:
    items = [
        _item(_ROUTE, "undated", None),
        _item(_ROUTE, "dated", "2026-12-05T00:00:00+01:00"),
    ]
    assert _guids(_defer_repeated_route_titles(items)) == ["dated", "undated"]


def test_a_tie_keeps_the_sorted_order() -> None:
    items = [
        _item(_ROUTE, "first", "2026-10-03T00:00:00+02:00"),
        _item(_ROUTE, "second", "2026-10-03T00:00:00+02:00"),
    ]
    assert _guids(_defer_repeated_route_titles(items)) == ["first", "second"]


def test_datetime_values_work_like_strings() -> None:
    items = [
        _item(_ROUTE, "late", None),
        _item(_ROUTE, "early", None),
    ]
    items[0]["starts_at"] = datetime(2026, 12, 5, tzinfo=UTC)
    items[1]["starts_at"] = datetime(2026, 10, 3, tzinfo=UTC)
    assert _guids(_defer_repeated_route_titles(items)) == ["early", "late"]


@pytest.mark.parametrize("source", ["Wiener Linien", "Stadt Wien – Baustellen", ""])
def test_other_sources_are_left_alone(source: str) -> None:
    # A Wiener-Linien title names line and place; two identical ones are a
    # matter for the dedupe passes, not for this one.
    items = [
        _item("13A: Fahrtbehinderung wegen Rettungseinsatz", "late", "2026-12-05T00:00:00+01:00", source=source),
        _item("13A: Fahrtbehinderung wegen Rettungseinsatz", "early", "2026-10-03T00:00:00+02:00", source=source),
    ]
    assert _defer_repeated_route_titles(items) == items


# ---------------- the pass must actually run ----------------


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_the_pass_is_wired_into_the_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Through ``main()``: nine Wiener-Linien items, the three phases, a cap of ten.

    Without the pass the three phases (published most recently) take three
    of the ten slots and two Wiener-Linien items are cut; with it, the
    October phase holds one slot and all nine others are published.
    """
    bf = _import_build_feed(monkeypatch)
    now = datetime.now(UTC)

    def stamp(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).isoformat()

    def wl_item(n: int) -> dict[str, Any]:
        return {
            "source": "Wiener Linien",
            "category": "Störung",
            "title": f"{n}A: Umleitung Ort {n}",
            "description": f"Linie {n}A: Umleitung wegen Bauarbeiten.",
            "guid": f"wl{n}",
            "link": "",
            "pubDate": stamp(60 + n),
            "starts_at": stamp(60 + n),
            "ends_at": (now + timedelta(days=30)).isoformat(),
        }

    def phase(guid: str, pub_age_min: int, start_days: int, end_days: int) -> dict[str, Any]:
        return {
            "source": "ÖBB",
            "category": "Störung",
            "title": _ROUTE,
            "description": f"Wegen Bauarbeiten Phase {guid}.",
            "guid": guid,
            "link": "",
            "pubDate": stamp(pub_age_min),
            "starts_at": (now + timedelta(days=start_days)).isoformat(),
            "ends_at": (now + timedelta(days=end_days)).isoformat(),
        }

    phases = [
        phase("phase-dec", 1, 70, 72),
        phase("phase-nov", 2, 36, 66),
        phase("phase-oct", 3, 9, 11),
    ]
    wl = [wl_item(n) for n in range(1, 10)]

    def fake_read_cache(provider: str) -> list[dict[str, Any]]:
        if provider == "wl":
            return wl
        if provider == "oebb":
            return phases
        return []

    monkeypatch.setattr(bf, "read_cache", fake_read_cache)
    out_file = tmp_path / "feed.xml"
    monkeypatch.setattr(bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bf.feed_config, "MAX_ITEMS", 10)
    monkeypatch.setattr(bf, "_save_state", lambda state: None)
    monkeypatch.setattr(bf, "_load_state", lambda: {})
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)

    assert bf.main() == 0

    channel = ET.parse(out_file).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    titles = [it.findtext("title") or "" for it in items]
    guids = [it.findtext("guid") or "" for it in items]
    assert len(titles) == 10
    assert titles.count(_ROUTE) == 1, titles
    assert "phase-oct" in guids, guids
    assert all(f"wl{n}" in guids for n in range(1, 10)), guids
