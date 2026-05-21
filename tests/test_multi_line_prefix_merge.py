"""Regression test for Bug 33A (stacked WL line prefix and ``+`` separator).

User feedback: a WL feed item surfaced with a stacked and confusing
line prefix that obscured which line(s) were actually affected::

    Title: 40: 40+41: Betrieb ab Gersthof & Falschparker Betrieb ab Gersthof
    Description: 40+41: Betrieb ab Gersthof < Linie 40:
                 Nach einer Fahrtbehinderung kommt es zu
                 unterschiedlichen Intervallen.

User asked: "Ist hier die Linie 40 gemeint oder sind die Linien 40
und 41 betroffen?". Three independent bugs combined into the mess:

1. ``_ensure_line_prefix`` (src/providers/wl_lines.py) treated
   WL's own ``40+41:`` prefix as part of the body — the slash-only
   ``LINE_PREFIX_STRIP_RE`` didn't recognise the ``+`` separator —
   and prepended ``relatedLines=['40']`` on top, yielding
   ``40: 40+41: …``.
2. ``_LINE_PREFIX_RE`` / ``_parse_title`` in src/feed/merge.py also
   only split on ``/``, so ``40+41:`` was bagged as a single token
   ``40+41``. Cross-item dedup couldn't see the line overlap and the
   merge happened anyway (via topic+token similarity) but with a
   stacked title and verbatim description join.
3. The merged description concatenated a trailing ``<`` directional
   marker from item 1 with the ``Linie 40:`` prefix of item 2 →
   ``Betrieb ab Gersthof < Linie 40: …``, which reads as a stray
   glyph.

This module covers the end-to-end fix:

* ``_extract_prefix_lines`` unions stacked / multi-separator prefixes
  into a canonical set.
* ``_ensure_line_prefix`` re-emits ``L1/L2: …`` from the union.
* ``_post_filter_wl`` rebuilds cached titles where the cache still
  carries the old stacked form (defence-in-depth).
* ``_LINE_PREFIX_RE`` / ``_parse_title`` recognise ``+`` so the
  cross-line overlap drives the merge cleanly.
* ``_trim_trailing_directional`` strips ``<`` / ``>`` from each side
  before joining concatenated descriptions.
* ``_strip_wl_description_line_prefix`` drops a leading ``Linie 40:``
  / ``40+41:`` from the description (redundant with the title).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from src import build_feed
from src.build_feed import (
    _post_filter_wl,
    _strip_wl_description_line_prefix,
)
from src.feed.merge import _parse_title, deduplicate_fuzzy
from src.feed_types import FeedItem
from src.providers.wl_lines import (
    _ensure_line_prefix,
    _extract_prefix_lines,
)


def _wl_item(
    title: str,
    description: str,
    *,
    starts_at: str = "2026-05-21T20:48:34+02:00",
    ends_at: str = "2026-05-21T23:55:00+02:00",
    pub_date: str = "2026-05-21T20:33:00+02:00",
    guid: str = "test-id",
) -> dict[str, Any]:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "",
        "guid": guid,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "pubDate": pub_date,
    }


class TestExtractPrefixLinesHandlesStackedAndPlusSeparator:
    def test_stacked_prefix_unioned(self) -> None:
        body, lines = _extract_prefix_lines("40: 40+41: Betrieb ab Gersthof")
        assert body == "Betrieb ab Gersthof"
        assert set(lines) == {"40", "41"}

    def test_plus_separator_recognised(self) -> None:
        body, lines = _extract_prefix_lines("40+41: Betrieb ab Gersthof")
        assert body == "Betrieb ab Gersthof"
        assert set(lines) == {"40", "41"}

    def test_slash_separator_unchanged(self) -> None:
        body, lines = _extract_prefix_lines("U6/U4: Verspätung")
        assert body == "Verspätung"
        # Order preserved from the title — no numeric/lexical re-sort.
        assert lines == ["U6", "U4"]

    def test_comma_separator_recognised(self) -> None:
        body, lines = _extract_prefix_lines("9, 40, 41: Umleitung")
        assert body == "Umleitung"
        assert lines == ["9", "40", "41"]

    def test_rufbus_prefix_recognised(self) -> None:
        body, lines = _extract_prefix_lines("Rufbus N20: Betriebshinweis")
        assert body == "Betriebshinweis"
        assert lines == ["N20"]

    def test_no_prefix_returns_empty(self) -> None:
        body, lines = _extract_prefix_lines("Betrieb ab Gersthof")
        assert body == "Betrieb ab Gersthof"
        assert lines == []

    def test_order_preserved_for_slash_form(self) -> None:
        # Real WL cache item: must round-trip in WL's own order, not
        # numerically sorted (sorted form would be ``10A/41E``).
        body, lines = _extract_prefix_lines("41E/10A: Ersatzbus")
        assert lines == ["41E", "10A"]


class TestEnsureLinePrefixUnionsExistingPrefix:
    def test_existing_prefix_lines_merged_with_supplied(self) -> None:
        # WL's title carries ``40+41:`` but relatedLines API only has ['40'].
        # Without the union the rebuild silently drops 41.
        result = _ensure_line_prefix(
            "40+41: Betrieb ab Gersthof", ["40"]
        )
        assert result == "40/41: Betrieb ab Gersthof"

    def test_stacked_prefix_rebuilt_canonical(self) -> None:
        result = _ensure_line_prefix(
            "40: 40+41: Betrieb ab Gersthof", ["40"]
        )
        assert result == "40/41: Betrieb ab Gersthof"

    def test_existing_prefix_order_preserved(self) -> None:
        # Real WL cache item: ``41E/10A: Ersatzbus`` must round-trip
        # unchanged — the prefix order tracks WL's own rendering.
        result = _ensure_line_prefix("41E/10A: Ersatzbus", [])
        assert result == "41E/10A: Ersatzbus"

    def test_plus_collapses_to_slash_in_title_order(self) -> None:
        # ``9+40+41:`` collapses to ``9/40/41:`` (order from title).
        result = _ensure_line_prefix("9+40+41: Umleitung", [])
        assert result == "9/40/41: Umleitung"

    def test_existing_prefix_only_kept_when_no_supplied_lines(self) -> None:
        # Edge case: WL didn't supply relatedLines, but title has prefix.
        result = _ensure_line_prefix("U6: Verspätung", [])
        assert result == "U6: Verspätung"

    def test_no_prefix_no_lines_passes_through(self) -> None:
        result = _ensure_line_prefix("No prefix text", [])
        assert result == "No prefix text"


class TestParseTitleRecognisesPlusSeparator:
    def test_plus_form_yields_both_lines(self) -> None:
        lines, body = _parse_title("40+41: Betrieb ab Gersthof")
        assert set(lines) == {"40", "41"}
        assert body == "Betrieb ab Gersthof"

    def test_slash_form_still_works(self) -> None:
        lines, body = _parse_title("40/41: Betrieb ab Gersthof")
        assert set(lines) == {"40", "41"}
        assert body == "Betrieb ab Gersthof"

    def test_canonical_form_after_fix(self) -> None:
        # After _post_filter_wl rebuilds to canonical /, _parse_title
        # extracts both line tokens correctly.
        lines, body = _parse_title("40/41: Falschparker Betrieb ab Gersthof")
        assert set(lines) == {"40", "41"}


class TestPostFilterRebuiltsStackedTitles:
    def test_gersthof_stacked_title_rebuilt(self) -> None:
        items = [_wl_item("40: 40+41: Betrieb ab Gersthof", "Foo bar")]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "40/41: Betrieb ab Gersthof"

    def test_plus_only_title_canonicalised(self) -> None:
        items = [_wl_item("40+41: Betrieb ab Gersthof", "Foo bar")]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "40/41: Betrieb ab Gersthof"

    def test_already_canonical_title_untouched(self) -> None:
        items = [_wl_item("U6: Verspätung", "Foo bar")]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "U6: Verspätung"

    def test_single_line_title_untouched(self) -> None:
        items = [_wl_item("40: Falschparker Betrieb ab Gersthof", "Foo")]
        out = _post_filter_wl(items)
        assert out[0]["title"] == "40: Falschparker Betrieb ab Gersthof"


class TestPostFilterStripsDescriptionLinePrefix:
    def test_compact_plus_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "40+41: Betrieb ab Gersthof"
        ) == "Betrieb ab Gersthof"

    def test_linie_word_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "Linie 40: Nach einer Fahrtbehinderung."
        ) == "Nach einer Fahrtbehinderung."

    def test_linien_plural_word_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "Linien 9/40/41: Umleitung"
        ) == "Umleitung"

    def test_u_bahn_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "Linie U6: Verspätung wegen Fahrzeug."
        ) == "Verspätung wegen Fahrzeug."

    def test_no_prefix_unchanged(self) -> None:
        assert _strip_wl_description_line_prefix(
            "Verspätung wegen Schaden"
        ) == "Verspätung wegen Schaden"

    def test_time_marker_not_a_prefix(self) -> None:
        # ``17:30 Uhr Beginn`` looks vaguely line-like but has no
        # whitespace after the colon — the strict pattern excludes it.
        assert _strip_wl_description_line_prefix(
            "17:30 Uhr Beginn"
        ) == "17:30 Uhr Beginn"

    def test_generic_word_prefix_not_stripped(self) -> None:
        # ``Achtung:`` / ``Information:`` aren't line codes.
        for text in [
            "Achtung: Sperre wegen Bauarbeiten",
            "Information: Umleitung der Linie",
            "Strecke: Heiligenstadt — Floridsdorf",
        ]:
            assert _strip_wl_description_line_prefix(text) == text

    def test_empty_string_passes_through(self) -> None:
        assert _strip_wl_description_line_prefix("") == ""


class TestEndToEndGersthofMessageReadableAfterFix:
    """The original user-visible meldung must become readable."""

    def test_gersthof_full_pipeline(self) -> None:
        # Two cache items reproducing the user's complaint.
        items = [
            _wl_item(
                "40: 40+41: Betrieb ab Gersthof",
                "40+41: Betrieb\nab Gersthof <",
                starts_at="2026-05-20T08:06:12+02:00",
                ends_at="2026-05-21T23:59:59+02:00",
                pub_date="2026-05-20T08:06:12+02:00",
                guid="aaa",
            ),
            _wl_item(
                "40: Falschparker Betrieb ab Gersthof",
                "Linie 40: Nach einer Fahrtbehinderung kommt es zu "
                "unterschiedlichen Intervallen.",
                guid="bbb",
            ),
        ]

        filtered = _post_filter_wl(items)
        merged = deduplicate_fuzzy(filtered)

        # Exactly one merged item — both Gersthof entries collapse.
        assert len(merged) == 1
        item = merged[0]

        # Title: canonical line prefix with both lines, sorted.
        assert item["title"].startswith("40/41:")
        # No stacked prefix residue.
        assert "40: 40+41" not in item["title"]
        assert "40+41:" not in item["title"]

        # Description: no trailing ``<``, no line-prefix residue.
        desc = item["description"]
        assert "<" not in desc
        assert "40+41:" not in desc
        assert "Linie 40:" not in desc

        # The actual content survives.
        assert "Betrieb" in desc
        assert "Nach einer Fahrtbehinderung" in desc

    def test_rendered_title_and_description_clean(self) -> None:
        items = [
            _wl_item(
                "40: 40+41: Betrieb ab Gersthof",
                "40+41: Betrieb\nab Gersthof <",
                starts_at="2026-05-20T08:06:12+02:00",
                ends_at="2026-05-21T23:59:59+02:00",
                pub_date="2026-05-20T08:06:12+02:00",
                guid="aaa",
            ),
            _wl_item(
                "40: Falschparker Betrieb ab Gersthof",
                "Linie 40: Nach einer Fahrtbehinderung kommt es zu "
                "unterschiedlichen Intervallen.",
                guid="bbb",
            ),
        ]
        filtered = _post_filter_wl(items)
        merged = deduplicate_fuzzy(filtered)
        assert len(merged) == 1
        item = merged[0]
        feed_item = cast(FeedItem, item)
        starts_at = datetime.fromisoformat(str(item["starts_at"]))
        ends_at = datetime.fromisoformat(str(item["ends_at"]))
        formatted = build_feed._format_item_content(
            feed_item, ident="gersthof", starts_at=starts_at, ends_at=ends_at
        )
        # Final user-visible title: ``40/41: …`` only.
        assert formatted.title_out.startswith("40/41:")
        # No stacked / non-canonical line block in the rendered title.
        assert "40+41" not in formatted.title_out
        assert "40: 40" not in formatted.title_out
        # Description is free of redundant line attributions.
        assert "Linie 40:" not in formatted.desc_text_truncated
        assert "40+41:" not in formatted.desc_text_truncated


class TestCrossSourceMergeStillWorks:
    """The ``+`` separator support must not break ``/`` slashes for ÖBB merge."""

    def test_oebb_s50_slash_form_still_parsed(self) -> None:
        lines, body = _parse_title("S 50: Wien Westbahnhof ↔ Wien Hütteldorf")
        assert "S50" in lines
        assert "Wien Westbahnhof" in body

    def test_wl_u6_unchanged(self) -> None:
        lines, body = _parse_title("U6: Verspätung")
        assert lines == {"U6"}
        assert body == "Verspätung"


class TestEdgeCases:
    def test_three_line_plus_prefix(self) -> None:
        body, lines = _extract_prefix_lines("9+40+41: Umleitung")
        assert body == "Umleitung"
        assert lines == ["9", "40", "41"]

    def test_mixed_separators_in_stacked_prefix(self) -> None:
        # Combination of ``+`` and ``,`` across nested prefixes.
        body, lines = _extract_prefix_lines("40: 40+41, 42: Umleitung")
        assert "40" in lines
        assert "41" in lines
        assert "42" in lines

    def test_overlong_title_truncated_safely(self) -> None:
        # Stress: 600-char input doesn't blow up — function truncates to 500.
        # The truncation happens before prefix matching so a hostile input
        # that wraps the ``:`` past byte 500 simply yields no lines.
        long = "40+41: " + "x" * 600
        body, lines = _extract_prefix_lines(long)
        assert lines == ["40", "41"]
        # body is whatever survived after truncation; the function must
        # not crash and must return a string.
        assert isinstance(body, str)
