"""Regression test for Round 38: stacked sub-prefix and ``#`` junction marker.

Audit feedback (Round 38, "Findest du weitere Fehler oder mögliche
Optimierungen? ULTRATHINK") surfaced two distinct user-visible
quality issues in the post-Round-37 feed:

## Issue 1 — ``#`` junction marker mis-rendered as hashtag

WL uses ``#`` internally as a street-junction marker. After HTML-to-
text conversion the bare glyph survives into the rendered description,
where it looks like a stray hashtag::

    D: Umleitung in Richtung Ober St. Veit U ab Europaplatz # Mariahilfer Straße über …
    D: Wegen Gleisbauarbeiten im Bereich Gottschalkgasse # Simmeringer Hauptstraße …

All 46 current-cache occurrences carry whitespace on both sides
(``\\s+#\\s+``), so the swap to ``/`` is unambiguous and reads
naturally — mirroring WL's own ``40/41`` line-separator convention.

## Issue 2 — stacked sub-prefix with comma-list-then-Rufbus or
``(qualifier)`` parenthetical

Round 33's stacked-prefix collapse couldn't catch two real WL cache
items:

  * ``56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026``
    — the second prefix uses ``,`` (not ``und``) as the Rufbus
    connector, so ``LINES_COMPLEX_PREFIX_RE`` failed to match.
  * ``85A: 85A (Schulkurs): Straßenbauarbeiten`` — the second
    prefix carries a ``(Schulkurs)`` parenthetical qualifier
    (school-run bus variant) that the line-strip regex didn't
    expect, so the strict-token gate rejected the whole prefix.

Round 38 fixes both regex patterns and adds a paren-strip step
before the strict-token check. The qualifier (``Schulkurs``) is
preserved in the description body — only the title prefix
canonicalises.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, cast

from src import build_feed
from src.build_feed import _post_filter_wl
from src.feed_types import FeedItem
from src.providers.wl_lines import _extract_prefix_lines


def _format(raw_title: str, raw_desc: str) -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 22, 22, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestHashJunctionMarkerReplaced:
    def test_user_reported_europaplatz_junction(self) -> None:
        # Real cache N54 description.
        _, out = _format(
            "N54: Straßenbauarbeiten",
            "<p>Umleitung in Richtung Ober St. Veit U ab Europaplatz "
            "# Mariahilfer Straße über Wienzeile.</p>",
        )
        assert " # " not in out
        assert "Europaplatz / Mariahilfer Straße" in out

    def test_user_reported_gottschalkgasse_junction(self) -> None:
        # Real cache N6/N71 description.
        _, out = _format(
            "N6/N71: Umleitung wegen Gleisbauarbeiten",
            "<p>Wegen Gleisbauarbeiten im Bereich Gottschalkgasse "
            "# Simmeringer Hauptstraße kommt es zu einer Umleitung.</p>",
        )
        assert " # " not in out
        assert "Gottschalkgasse / Simmeringer Hauptstraße" in out

    def test_multiple_junctions_all_replaced(self) -> None:
        _, out = _format(
            "40: Test",
            "<p>ab A # B über C # D nach E.</p>",
        )
        assert " # " not in out
        assert "A / B" in out
        assert "C / D" in out

    def test_no_hashtag_in_normal_description(self) -> None:
        # Non-junction descriptions stay unchanged.
        _, out = _format(
            "U6: Verspätung",
            "<p>Wegen Schadhaftem Fahrzeug kommt es zu Verspätungen.</p>",
        )
        assert "/" not in out  # the cleanup didn't accidentally insert one
        assert "Verspätungen" in out

    def test_attached_hash_not_replaced(self) -> None:
        # Without spaces on both sides, the swap doesn't trigger
        # (defensive against unusual WL content).
        _, out = _format(
            "U6: Test",
            "<p>Ein Test #hashtag wird nicht ersetzt.</p>",
        )
        # The space-anchored swap is intentionally narrow.
        assert "#hashtag" in out


class TestStackedSubPrefixCollapsed:
    def test_user_reported_56a_maurer_kirtag(self) -> None:
        # Real cache item: ``56A, 60A, N60, Rufbus N61:`` as the
        # stacked sub-prefix uses ``,`` before ``Rufbus`` (not
        # ``und``).
        body, lines = _extract_prefix_lines(
            "56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026"
        )
        assert body == "Maurer Kirtag 2026"
        # All four line codes recovered; ``Rufbus N61`` cleaned to ``N61``.
        assert "56A" in lines
        assert "60A" in lines
        assert "N60" in lines
        assert "N61" in lines

    def test_user_reported_56a_post_filter_rebuilds_title(self) -> None:
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Hinweis",
            "title": "56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026",
            "description": "<p>Wegen Abhaltung des Maurer Kirtages.</p>",
            "guid": "aaa",
        }]
        out = _post_filter_wl(items)
        assert len(out) == 1
        # Canonical single-prefix title; line codes in original order.
        assert out[0]["title"] == "56A/60A/N60/N61: Maurer Kirtag 2026"

    def test_user_reported_85a_schulkurs(self) -> None:
        # Real cache item: ``85A (Schulkurs):`` carries a
        # parenthetical qualifier.
        body, lines = _extract_prefix_lines(
            "85A: 85A (Schulkurs): Straßenbauarbeiten"
        )
        assert body == "Straßenbauarbeiten"
        # The (Schulkurs) qualifier is stripped from the line code;
        # only ``85A`` survives as the canonical line.
        assert lines == ["85A"]

    def test_user_reported_85a_post_filter_rebuilds_title(self) -> None:
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Hinweis",
            "title": "85A: 85A (Schulkurs): Straßenbauarbeiten",
            "description": (
                "<p>Wegen Bauarbeiten im Bereich der Breitenleer Straße "
                "wird die Linie 85A (Schulkurs) umgeleitet.</p>"
            ),
            "guid": "bbb",
        }]
        out = _post_filter_wl(items)
        assert len(out) == 1
        # Canonical single-prefix title; (Schulkurs) preserved in
        # description but stripped from the title prefix.
        assert out[0]["title"] == "85A: Straßenbauarbeiten"
        # The body description still mentions 85A (Schulkurs).
        assert "Schulkurs" in out[0]["description"]


class TestMultiColonExistingBehaviorPreserved:
    """Round 33-37 functionality still applies."""

    def test_simple_plus_separator_still_collapses(self) -> None:
        body, lines = _extract_prefix_lines("40+41: Foo")
        assert lines == ["40", "41"]
        assert body == "Foo"

    def test_simple_slash_separator_still_collapses(self) -> None:
        body, lines = _extract_prefix_lines("U6/U4: Verspätung")
        assert lines == ["U6", "U4"]
        assert body == "Verspätung"

    def test_stacked_pure_digit_prefix_still_collapses(self) -> None:
        # Round 33 case.
        body, lines = _extract_prefix_lines("40: 40+41: Betrieb ab Gersthof")
        assert set(lines) == {"40", "41"}
        assert body == "Betrieb ab Gersthof"

    def test_strict_gate_still_rejects_generic_words(self) -> None:
        # Round 33-follow-up false-positive guards.
        for title in [
            "Achtung: Sperre wegen Bauarbeiten",
            "Information: Umleitung der Linie",
            "Hinweis: Verspätung erwartet",
        ]:
            body, lines = _extract_prefix_lines(title)
            assert lines == []
            assert body == title

    def test_strict_gate_still_rejects_time_prefix(self) -> None:
        body, lines = _extract_prefix_lines("17:30 Uhr Verspätung")
        assert lines == []
        assert body == "17:30 Uhr Verspätung"

    def test_d_single_letter_line_still_works(self) -> None:
        # Round 34 case.
        body, lines = _extract_prefix_lines("D: Demonstration")
        assert lines == ["D"]
        assert body == "Demonstration"


class TestParenthesisQualifierStripping:
    """The new paren-strip step on prefix-block tokens."""

    def test_single_token_with_schulkurs_qualifier(self) -> None:
        body, lines = _extract_prefix_lines("85A (Schulkurs): Test")
        assert lines == ["85A"]
        assert body == "Test"

    def test_qualifier_inside_comma_list(self) -> None:
        # Multiple tokens, one with a parenthetical.
        body, lines = _extract_prefix_lines(
            "40A (Schulkurs), 41A, 42A: Foo"
        )
        # Compound list — should still work via LINES_COMPLEX_PREFIX_RE.
        # All three line codes recovered; qualifier stripped.
        assert "40A" in lines
        assert "41A" in lines
        assert "42A" in lines
        assert body == "Foo"

    def test_qualifier_preserved_in_body(self) -> None:
        # When the qualifier is mid-text (not in prefix), it stays put.
        body, lines = _extract_prefix_lines(
            "85A: Linie 85A (Schulkurs) umgeleitet"
        )
        assert lines == ["85A"]
        # ``(Schulkurs)`` in body survives.
        assert "(Schulkurs)" in body


class TestEndToEndUserAuditItems:
    def test_56a_kirtag_renders_clean_title(self) -> None:
        title = "56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026"
        desc = (
            "<h2>Veranstaltung</h2>"
            "<p>Wegen Abhaltung des Maurer Kirtages am Maurer "
            "Hauptplatz kommt es zu Umleitungen bei den dort "
            "verkehrenden Buslinien.</p>"
        )
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Hinweis",
            "title": title,
            "description": desc,
            "guid": "kirtag",
        }]
        filtered = _post_filter_wl(items)
        assert len(filtered) == 1
        item = filtered[0]
        # Title is canonical single-prefix.
        assert item["title"] == "56A/60A/N60/N61: Maurer Kirtag 2026"
        # No stacked sub-prefix.
        assert "56A, 60A, N60, Rufbus N61" not in item["title"]
