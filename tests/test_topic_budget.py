"""No single reason-and-day may take every slot of the feed.

Published 2026-09-19: one demonstration on the Ring, one Wiener-Linien
ticker per affected line, and — sorted newest-first — all ten slots of
the feed went to that one event. The 25/26/27 replacement service, every
ÖBB notice and the two construction-site items directly below the cap
were invisible. Over 300 published revisions the shape recurred on two
of eight days, each time with eight same-reason tickers in the top ten.

``_apply_topic_budget`` runs after the sort and before the cap: beyond
the third item of one reason word and day, the rest move behind the
field, in their original order. Nothing is dropped — a larger
``MAX_ITEMS`` still shows them, later. Items without a reason word or a
date are never touched. ``MAX_ITEMS_PER_TOPIC`` sets the budget; 0 turns
the pass off.
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

from src.build_feed import _apply_topic_budget, _topic_budget_key
from src.feed import config as feed_config
from src.feed_types import FeedItem


def _item(title: str, *, starts_at: str = "2026-09-19T13:23:00+02:00", guid: str | None = None) -> FeedItem:
    return cast(
        FeedItem,
        {
            "title": title,
            "description": "",
            "guid": guid or title,
            "starts_at": starts_at,
            "pubDate": starts_at,
        },
    )


# ---------------- the key ----------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("1: Demonstration Betrieb ab Hintere Zollamtsstraße", ("demonstration", "2026-09-19")),
        ("2A: Demonstration Kein Betrieb", ("demonstration", "2026-09-19")),
        # The reason after a trailing ``wegen`` counts too.
        ("71: Fahrtbehinderung wegen Demonstration", ("demonstration", "2026-09-19")),
        ("40/41/9/42: Veranstaltung Linien 40 und 41 Umleitung", ("veranstaltung", "2026-09-19")),
        # No reason word: never budgeted.
        ("2: Züge halten in der Mühlfeldgasse", None),
        ("1: Züge halten bei Linie O", None),
        ("Wien Hauptbahnhof ↔ Gramatneusiedl", None),
    ],
)
def test_topic_budget_key(title: str, expected: tuple[str, str] | None) -> None:
    assert _topic_budget_key(_item(title)) == expected


def test_the_day_is_the_vienna_date_of_the_start() -> None:
    # 23:30 UTC on the 18th is already the 19th in Vienna.
    key = _topic_budget_key(_item("1: Demonstration Kein Betrieb", starts_at="2026-09-18T23:30:00+00:00"))
    assert key == ("demonstration", "2026-09-19")


def test_without_any_date_there_is_no_key() -> None:
    item = _item("1: Demonstration Kein Betrieb")
    item["starts_at"] = None
    item["pubDate"] = None
    assert _topic_budget_key(item) is None


# ---------------- the pass ----------------


_DEMO = [
    _item(f"{line}: Demonstration Betrieb ab Ort {i}", guid=f"demo{i}")
    for i, line in enumerate(["1", "2", "2A", "3A", "4A", "31", "66A", "74A", "D"])
]
_OTHERS = [
    _item("25/26/27: Gleisbauarbeiten", starts_at="2026-09-14T04:30:00+02:00", guid="o1"),
    _item("2: Züge halten in der Mühlfeldgasse", guid="o2"),
    _item("S 45: Wien Hütteldorf ↔ Wien Handelskai", starts_at="2026-09-19T08:00:00+02:00", guid="o3"),
]


def test_the_published_day() -> None:
    """Nine demonstration tickers ahead of three other items: three stay in front."""
    out = _apply_topic_budget([*_DEMO, *_OTHERS], 3)

    assert [i["guid"] for i in out] == [
        "demo0",
        "demo1",
        "demo2",
        "o1",
        "o2",
        "o3",
        "demo3",
        "demo4",
        "demo5",
        "demo6",
        "demo7",
        "demo8",
    ]


def test_nothing_is_dropped_and_the_order_within_groups_holds() -> None:
    out = _apply_topic_budget([*_DEMO, *_OTHERS], 3)

    assert sorted(i["guid"] for i in out) == sorted(i["guid"] for i in [*_DEMO, *_OTHERS])
    demo_order = [i["guid"] for i in out if i["guid"].startswith("demo")]
    assert demo_order == [i["guid"] for i in _DEMO]


def test_items_without_a_key_are_never_deferred() -> None:
    items = [_item(f"{n}: Züge halten bei Linie O", guid=f"n{n}") for n in range(8)]
    assert _apply_topic_budget(items, 3) == items


def test_different_days_have_separate_budgets() -> None:
    today = [_item(f"{n}: Bauarbeiten Umleitung", guid=f"t{n}") for n in range(4)]
    yesterday = [_item(f"{n}: Bauarbeiten Umleitung", starts_at="2026-09-18T08:00:00+02:00", guid=f"y{n}") for n in range(4)]
    out = _apply_topic_budget([*today, *yesterday], 3)

    assert [i["guid"] for i in out] == ["t0", "t1", "t2", "y0", "y1", "y2", "t3", "y3"]


def test_different_reasons_have_separate_budgets() -> None:
    demo = [_item(f"{n}: Demonstration Kein Betrieb", guid=f"d{n}") for n in range(4)]
    park = [_item(f"{n}: Falschparker", guid=f"f{n}") for n in range(4)]
    out = _apply_topic_budget([*demo, *park], 3)

    assert [i["guid"] for i in out] == ["d0", "d1", "d2", "f0", "f1", "f2", "d3", "f3"]


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_of_zero_turns_the_pass_off(limit: int) -> None:
    items = [*_DEMO, *_OTHERS]
    assert _apply_topic_budget(items, limit) == items


def test_a_budget_larger_than_any_group_changes_nothing() -> None:
    items = [*_DEMO, *_OTHERS]
    assert _apply_topic_budget(items, 20) == items


# ---------------- configuration ----------------


def test_the_budget_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_ITEMS_PER_TOPIC", "2")
    feed_config.refresh_from_env()
    assert feed_config.MAX_ITEMS_PER_TOPIC == 2
    assert feed_config.build_settings().max_items_per_topic == 2

    monkeypatch.setenv("MAX_ITEMS_PER_TOPIC", "-5")
    feed_config.refresh_from_env()
    assert feed_config.MAX_ITEMS_PER_TOPIC == 0

    monkeypatch.delenv("MAX_ITEMS_PER_TOPIC")
    feed_config.refresh_from_env()
    assert feed_config.MAX_ITEMS_PER_TOPIC == 3


# ---------------- the pass must actually run ----------------


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_the_budget_is_wired_into_the_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Through ``main()``: nine tickers of one event, three other items, a cap of ten.

    Without the pass the three others sit on places 10–12 and only one of
    them is published; with it, all three are, and the event keeps three.
    """
    bf = _import_build_feed(monkeypatch)
    now = datetime.now(UTC)

    def stamp(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).isoformat()

    def wl_item(title: str, guid: str, age_min: int) -> dict[str, Any]:
        return {
            "source": "Wiener Linien",
            "category": "Störung",
            "title": title,
            "description": f"Linie {title.split(':', 1)[0]}: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "guid": guid,
            "link": "",
            "pubDate": stamp(age_min),
            "starts_at": stamp(age_min),
            "ends_at": (now + timedelta(hours=6)).isoformat(),
        }

    tickers = [
        wl_item(f"{line}: Demonstration Betrieb ab Ort {i}", f"demo{i}", i)
        for i, line in enumerate(["1", "2", "2A", "3A", "4A", "31", "66A", "74A", "D"])
    ]
    others = [
        wl_item("25: Ersatzbus ab Josef-Baumann-Gasse", "o1", 60),
        wl_item("12A: Betrieb ab Johnstraße U", "o2", 61),
        wl_item("N49: Betrieb ab Schweglerstraße", "o3", 62),
    ]

    def fake_read_cache(provider: str) -> list[dict[str, Any]]:
        return [*tickers, *others] if provider == "wl" else []

    monkeypatch.setattr(bf, "read_cache", fake_read_cache)
    out_file = tmp_path / "feed.xml"
    monkeypatch.setattr(bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bf.feed_config, "MAX_ITEMS", 10)
    monkeypatch.setattr(bf.feed_config, "MAX_ITEMS_PER_TOPIC", 3)
    monkeypatch.setattr(bf, "_save_state", lambda state: None)
    monkeypatch.setattr(bf, "_load_state", lambda: {})
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)

    assert bf.main() == 0

    channel = ET.parse(out_file).getroot().find("channel")
    assert channel is not None
    titles = [it.findtext("title") or "" for it in channel.findall("item")]
    assert len(titles) == 10
    # Three of the event in front, then the three others, then — because
    # slots are left — the deferred tickers in their original order.
    assert all("Demonstration" in t for t in titles[:3]), titles
    assert titles[3:6] == [
        "25: Ersatzbus ab Josef-Baumann-Gasse",
        "12A: Betrieb ab Johnstraße U",
        "N49: Betrieb ab Schweglerstraße",
    ], titles
    # Short titles since 2026-09-25: the consequence ("Betrieb ab Ort …")
    # moves into the description, each line keeps its own title. The
    # ordering contract of this test is unaffected by that.
    deferred = ((3, "3A"), (4, "4A"), (5, "31"), (6, "66A"))
    assert titles[6:] == [f"{line}: Demonstration" for _, line in deferred], titles
