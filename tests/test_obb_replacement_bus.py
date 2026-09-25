"""WL's "ÖBB-Ersatzbus für <80" is the S80, not tram line 1.

Live since 2026-09-12, daily, with ``relatedLines`` "1"::

    T: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80
    D: [Am 25.09.2026]

Operator, 2026-09-25: tram lines 1 and 80 do not serve Hütteldorf station;
no tram stops there. The "<" is the S-Bahn logo of WL's display boards.
Decision: ``S80: ÖBB-Ersatzbus`` over ``Bhf. Hütteldorf``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

LIVE = {
    "source": "Wiener Linien",
    "category": "Störung",
    "title": "1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80",
    "description": "Bhf. Hütteldorf\nÖBB-Ersatzbus für <80",
    "link": "https://www.wienerlinien.at/ogd_realtime",
    "guid": "a2b77bf3b53be1ac11a83fab5eb09687b824df82ac6a4b9d37215889acd1d1db",
}


def _wl(title: str, description: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {**LIVE, "title": title}
    if description is None:
        item.pop("description")
    else:
        item["description"] = description
    return item


def test_the_live_item_after_the_post_filter() -> None:
    (item,) = bf._post_filter_wl([dict(LIVE)])
    assert (item["title"], item["description"]) == ("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf")
    assert item["guid"] == LIVE["guid"]  # identity and first_seen stay


def test_the_live_item_as_published() -> None:
    (item,) = bf._post_filter_wl([dict(LIVE)])
    start = datetime(2026, 9, 25, 3, 0, 28, tzinfo=UTC)
    end = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)
    formatted = bf._format_item_content(cast(FeedItem, item), ident="t", starts_at=start, ends_at=end)
    assert formatted.title_out == "S80: ÖBB-Ersatzbus"
    assert formatted.desc_text_truncated == "Bhf. Hütteldorf [Am 25.09.2026]"


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        ("45: Bhf. Ottakring ÖBB-Ersatzbus für S45", "x", "S45: ÖBB-Ersatzbus"),
        ("1: Floridsdorf ÖBB-Ersatzbus für S 1", "x", "S1: ÖBB-Ersatzbus"),
        ("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf", "S80: ÖBB-Ersatzbus"),
    ],
)
def test_the_s_bahn_line_the_text_names(title: str, description: str, expected: str) -> None:
    assert bf._attribute_obb_replacement_bus(_wl(title, description))["title"] == expected


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("12: Bhf. X ÖBB-Ersatzbus für <12", "ÖBB-Ersatzbus für <12"),  # 12 is no S-Bahn line
        ("27: Ersatzbus ab Floridsdorf", "Ersatzbus ab Floridsdorf"),  # a WL replacement bus
        ("U6: Rettungseinsatz", "Unregelmäßige Intervalle."),
    ],
)
def test_other_items_are_untouched(title: str, description: str) -> None:
    item = _wl(title, description)
    assert bf._attribute_obb_replacement_bus(item) is item


def test_more_text_in_the_description_stays() -> None:
    item = _wl(
        "1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80",
        "Bhf. Hütteldorf\nÖBB-Ersatzbus für <80\nAbfahrt vom Busbahnhof",
    )
    assert bf._attribute_obb_replacement_bus(item)["description"] == "Bhf. Hütteldorf Abfahrt vom Busbahnhof"


def test_without_description_the_place_comes_from_the_title() -> None:
    item = _wl("1: Bhf. Hütteldorf ÖBB-Ersatzbus für <80", None)
    fixed = bf._attribute_obb_replacement_bus(item)
    assert (fixed["title"], fixed["description"]) == ("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf")
