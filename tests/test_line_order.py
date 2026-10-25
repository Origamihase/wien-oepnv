"""Every line prefix of a title follows WL's own line order.

On 2026-10-25 the feed showed "1/2/71/D: Veranstaltung" (an incident with
its display tickers joined in, sorted by number) next to "1/2/D/71:
Nationalfeiertag" (a notice in the order WL sent ``relatedLines``). Since
then every prefix takes the ``SortingHelp`` order of WL's open line data
(``data/wienerlinien-ogd-linien.csv``), whoever builds it (the fuzzy merge:
``tests/test_feed_merge.py``).
"""

from __future__ import annotations

from src.build_feed import _union_prefix
from src.feed_types import FeedItem
from src.providers.wl_lines import _ensure_line_prefix, line_order_key


def test_a_notice_takes_wl_order_not_the_order_wl_sent() -> None:
    assert _ensure_line_prefix("D, 1, 2, 71: Nationalfeiertag", ["1", "2", "D", "71"]) == (
        "1/2/71/D: Nationalfeiertag"
    )
    assert _ensure_line_prefix("42/9: Falschparker", ["42", "9"]) == "9/42: Falschparker"
    assert _ensure_line_prefix("Strampfergasse", ["56B", "56A", "N62"]) == "56A/56B/N62: Strampfergasse"


def test_a_joined_incident_takes_the_same_order() -> None:
    members: list[FeedItem] = [
        {"title": "1: Veranstaltung", "description": "", "link": "", "source": "Wiener Linien"},
        {"title": "D: Veranstaltung", "description": "", "link": "", "source": "Wiener Linien"},
        {"title": "71: Veranstaltung", "description": "", "link": "", "source": "Wiener Linien"},
        {"title": "2: Veranstaltung", "description": "", "link": "", "source": "Wiener Linien"},
    ]
    assert _union_prefix(members) == "1/2/71/D: "


def test_trams_before_buses_and_unknown_lines_by_their_shape() -> None:
    lines = ["74A", "LB", "U6", "REX 51", "N66", "3", "1", "D", "99C", "U5"]
    assert sorted(lines, key=line_order_key) == ["U5", "U6", "1", "3", "D", "LB", "74A", "99C", "N66", "REX 51"]
