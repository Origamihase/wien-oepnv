"""Regression test for Bug 32A (trailing WL directional marker '>').

User feedback: a WL Hinweis surfaced with a dangling ``>`` in the
description::

    T: "2: Veranstaltung Betrieb ab Schwedenplatz"
    D: "Betrieb ab Schwedenplatz > [Am 16.05.2026]"

The ``>`` is WL's ASCII "service onwards" arrow. With a destination
after it (``Betrieb ab Schwedenplatz > Praterstern``) it carries
meaning. Standalone at the end of the summary it reads like a
broken HTML tag glyph or a truncation artifact next to the
``[Am 16.05.2026]`` timeframe bracket.

The fix strips trailing ``>``/``<`` (with optional surrounding
whitespace) from the summary in ``_format_item_content``. Marker
characters mid-text are preserved — only the trailing form (which
has no semantic referent) is removed.

The strip happens BEFORE the title-body duplicate check (Bug 27A)
so a summary like ``Betrieb ab Schwedenplatz >`` can match against
a title body of ``Betrieb ab Schwedenplatz`` and be dropped
entirely when redundant.

Cache items affected (current snapshot): WL Störung #30
(``Ring, Volkstheater >``), #32 (``Schwedenplatz >``), #42
(``Thaliastraße >`` — additionally triggers Round 27 dedup after
the strip).
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestTrailingDirectionalMarkerStripped:
    def test_schwedenplatz_marker_stripped(self) -> None:
        # User's exact reproduction.
        title = "2: Veranstaltung Betrieb ab Schwedenplatz"
        desc = "Veranstaltung\nBetrieb ab Schwedenplatz >"
        _, out = _format(title, desc)
        # The dangling ">" must NOT appear in the rendered description.
        assert ">" not in out.replace("[", "")
        # And the timeframe is still present.
        assert "[Am" in out or "[Seit" in out

    def test_volkstheater_marker_stripped(self) -> None:
        title = "2: Veranstaltung Betrieb ab Ring, Volkstheater"
        desc = "Veranstaltung\nBetrieb ab Ring, Volkstheater >"
        _, out = _format(title, desc)
        assert "Volkstheater >" not in out
        assert "Volkstheater" in out

    def test_thaliastrasse_marker_strip_enables_dedup(self) -> None:
        # When stripping the trailing ">" makes the summary match the
        # title body verbatim, Round 27 dedup kicks in and drops the
        # summary entirely — description becomes just the timeframe.
        title = "46: Betrieb ab Thaliastraße"
        desc = "Gleisbauarbeiten\nBetrieb ab Thaliastraße >"
        _, out = _format(title, desc)
        # "Betrieb ab Thaliastraße" must NOT appear in description —
        # it's now recognised as a duplicate of the title body.
        assert "Betrieb ab Thaliastraße" not in out
        # Only the timeframe survives.
        assert out.strip().startswith("[")


class TestMidTextDirectionalMarkerPreserved:
    def test_arrow_with_destination_kept(self) -> None:
        # ``A > B`` is a legitimate WL directional clause — preserve.
        title = "U6: Verspätung"
        desc = "Betrieb ab Schwedenplatz > Praterstern wegen Bauarbeiten."
        _, out = _format(title, desc)
        assert "Schwedenplatz > Praterstern" in out

    def test_arrow_in_middle_unchanged(self) -> None:
        # An arrow inside a sentence stays put — only trailing forms
        # are noise.
        title = "U6: Test"
        desc = "Linie U6 > Floridsdorf umgeleitet."
        _, out = _format(title, desc)
        assert "U6 > Floridsdorf" in out


class TestNormalTitlesUntouched:
    def test_no_marker_no_change(self) -> None:
        title = "U6: Verspätung wegen Schadhaftem Fahrzeug"
        desc = "Linie U6: Unregelmäßige Intervalle in beiden Richtungen."
        _, out = _format(title, desc)
        assert "Unregelmäßige Intervalle" in out
        assert "<" not in out and ">" not in out

    def test_multiple_trailing_markers_all_stripped(self) -> None:
        # Defence: ``>>>`` and ``> > >`` patterns also collapse.
        title = "2: Test Betrieb ab Foo"
        desc = "Test\nBetrieb ab Foo >>>"
        _, out = _format(title, desc)
        assert ">" not in out.replace("[", "").replace("]", "")

    def test_trailing_less_than_marker_stripped(self) -> None:
        # WL also occasionally uses ``<`` for opposite direction.
        title = "2: Test Betrieb ab Foo"
        desc = "Test\nBetrieb ab Foo <"
        _, out = _format(title, desc)
        assert "Foo <" not in out
