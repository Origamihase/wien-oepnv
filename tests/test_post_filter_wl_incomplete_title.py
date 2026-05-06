"""Regression test for Bug 28A (WL items with mid-sentence-truncated title).

Real WL source data sometimes serves an item whose description is
genuinely incomplete — the trailing location is missing::

    T: "41E: Ersatzbus 41E hält gegenüber"
    D: "Gleisbauarbeiten\\nErsatzbus 41E\\nhält gegenüber"

The text reads "stops opposite [nothing]" — meaningless to the user.
The WL API for this item simply did not include the location, so the
information cannot be recovered downstream.

The fix drops such items in ``_post_filter_wl`` based on a regex
that matches German prepositions/connectors at the end of the title
body (``bei``, ``gegenüber``, ``an``, ``in``, ``vor``, ``nach``,
``zu``, ``über``, ``am``, ``im``, ``zur``, ``zum``). Items whose
preposition has an object after it (``halten gegenüber 93``,
``halten bei Währinger Str 200``) stay in the feed.
"""

from __future__ import annotations

from typing import Any

from src.build_feed import _post_filter_wl


class TestIncompleteTitleDropped:
    def test_haelt_gegenueber_alone_dropped(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "41E: Ersatzbus 41E hält gegenüber"}
        ]
        out = _post_filter_wl(items)
        assert out == []

    def test_haelt_gegenueber_with_object_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "44A: Busse halten Alszeile gegenüber 93"}
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_halten_bei_alone_dropped(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "10A: Busse halten bei"}
        ]
        out = _post_filter_wl(items)
        assert out == []

    def test_halten_bei_with_object_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "41E: Ersatzbus 41E halten bei Währinger Str 200"}
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_normal_title_kept(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "U6: Verspätung wegen Schadhaftem Fahrzeug"}
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1

    def test_title_ending_in_an_dropped(self) -> None:
        items: list[dict[str, Any]] = [
            {"title": "10A: Bus hält an"}
        ]
        out = _post_filter_wl(items)
        assert out == []

    def test_existing_newline_normalisation_still_works(self) -> None:
        # Round 13's whitespace fix must continue to work alongside.
        items: list[dict[str, Any]] = [
            {"title": "41E/10A: Ersatzbus 41E\nhält beim 10A"}
        ]
        out = _post_filter_wl(items)
        assert len(out) == 1
        assert out[0]["title"] == "41E/10A: Ersatzbus 41E hält beim 10A"
