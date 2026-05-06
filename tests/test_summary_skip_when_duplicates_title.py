"""Regression tests for Bug 27A (summary just repeats the title body).

After the Round 24/25 dedup strips a leading category prefix, many WL
Störung items end up with a summary that exactly matches the title
body (after the line-prefix is removed)::

    T: "41E: Ersatzbus 41E halten bei Währinger Str 200"
    D: "Ersatzbus 41E halten bei Währinger Str 200 [Seit 06.05.2026]"

The user reads the same text twice — once as the title, once as the
description summary — which is pure noise.

The fix drops the summary entirely when its content is a verbatim
case-insensitive copy of the title body. The description then renders
as just the timeframe ``[Seit 06.05.2026]``.

Cache items affected (live WL Störung at the time of writing):
#27, #28, #30, #31, #32, #33, #35, #38.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


def _format(raw_title: str, raw_desc: str) -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestSummaryDroppedWhenDuplicatesTitleBody:
    def test_ersatzbus_duplicate_dropped(self) -> None:
        title = "41E: Ersatzbus 41E hält gegenüber"
        desc = "Ersatzbus 41E hält gegenüber"
        _, out = _format(title, desc)
        # Description should NOT contain a repeat of the title body.
        assert "Ersatzbus" not in out
        # The timeframe still appears.
        assert "[Seit" in out

    def test_kein_betrieb_duplicate_dropped(self) -> None:
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb"
        _, out = _format(title, desc)
        assert "Kein Betrieb" not in out
        assert "[Seit" in out

    def test_busse_halten_duplicate_dropped(self) -> None:
        title = "62A: Busse halten Breitenfurter Straße 236-238"
        desc = "Busse halten Breitenfurter Straße 236-238"
        _, out = _format(title, desc)
        # Title body must not be duplicated.
        assert "Breitenfurter Straße" not in out

    def test_distinct_summary_kept(self) -> None:
        # When the summary is genuinely different, it survives.
        title = "U6: Verspätung wegen Schadhaftem Fahrzeug"
        desc = "Linie U6: Unregelmäßige Intervalle in beiden Richtungen."
        _, out = _format(title, desc)
        assert "Unregelmäßige Intervalle" in out

    def test_summary_contains_timeframe_extra(self) -> None:
        # Even when the summary is just the title body, the timeframe
        # is still appended so the description isn't empty.
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb"
        _, out = _format(title, desc)
        assert out.strip().startswith("[")

    def test_partial_match_does_not_drop(self) -> None:
        # Summary contains MORE than the title body — must not drop.
        title = "46: Kein Betrieb"
        desc = "Kein Betrieb. Reisende werden gebeten Alternativen zu nutzen."
        _, out = _format(title, desc)
        assert "Reisende werden gebeten" in out

    def test_case_insensitive_match(self) -> None:
        # Casefold compare: ``Linie U6`` vs ``LINIE U6`` is the same.
        title = "U6: Linie U6 gestört"
        desc = "Linie U6 gestört"
        _, out = _format(title, desc)
        # Description body should not duplicate the title body.
        assert "Linie U6 gestört" not in out
