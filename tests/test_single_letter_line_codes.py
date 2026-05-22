"""Regression test for Bug 34A (single-letter WL line codes mis-classified).

User feedback: a WL meldung surfaced with a stacked, duplicated line
prefix::

    Title: D: D: Demonstration
    Desc:  Linie D: Unregelmäßige Intervalle in beiden Richtungen.
           Grund: Demonstration. [Am 22.05.2026]

User asked: "Hier wird die Linie doppelt angegeben. D: D:"

Root cause
==========
WL tram line ``D`` (Wien Hauptbahnhof — Nußdorf) is a real letters-
only line — no digit. The follow-up audit that hardened
``_extract_prefix_lines`` against false-positives like
``Achtung: Sperre`` introduced ``_STRICT_LINE_TOKEN_RE`` requiring at
least one digit (``^[A-Z]{0,4}\\d{1,3}[A-Z]?$``). The strict gate
correctly rejects ``ACHTUNG`` / ``HINWEIS`` / ``INFORMATION`` (all
multi-letter, no digit) but ALSO rejects ``D`` (single letter, no
digit). As a result:

1. WL fetch path: ``_ensure_line_prefix("D: Demonstration", ["D"])``
   tries to strip the existing ``D:`` prefix but the strict gate
   rejects it; the code then PREPENDS ``D:`` from ``relatedLines``
   on top of the un-stripped title → ``D: D: Demonstration``.
2. Cache-read path: ``_post_filter_wl`` sees the stacked title but
   can't rebuild it because ``_extract_prefix_lines`` returns
   ``lines=[]`` (strict gate rejects ``D``).
3. Description-strip path: ``_strip_wl_description_line_prefix``
   uses the same digit-requiring shape so ``Linie D: …`` keeps the
   redundant ``Linie D:`` prefix in the user-visible summary.

Fix
===
Extend both the strict-token regex (``src/providers/wl_lines.py``)
and the description-prefix line-token shape (``src/build_feed.py``)
to accept a single bare uppercase letter in addition to the existing
digit-bearing pattern. Multi-letter words without digits (``ACHTUNG``,
``HINWEIS``, ``INFORMATION``, ``LINIE``) still fail both shapes so
the original false-positive guard from
``test_wl_lines_strict_prefix_gate.py`` continues to hold.

End-to-end result for the user's exact cache item::

    Before:
        Title: D: D: Demonstration
        Desc:  Linie D: Unregelmäßige Intervalle …

    After:
        Title: D: Demonstration
        Desc:  Unregelmäßige Intervalle …  [Am 22.05.2026]
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from src import build_feed
from src.build_feed import (
    _post_filter_wl,
    _strip_wl_description_line_prefix,
)
from src.feed_types import FeedItem
from src.providers.wl_lines import _ensure_line_prefix, _extract_prefix_lines


class TestSingleLetterLineExtraction:
    def test_tram_d_extracted(self) -> None:
        body, lines = _extract_prefix_lines("D: Demonstration")
        assert lines == ["D"]
        assert body == "Demonstration"

    def test_stacked_tram_d_collapses(self) -> None:
        # The user's exact bug: stacked D: prefix collapses to a single
        # canonical D: on cache re-parse.
        body, lines = _extract_prefix_lines("D: D: Demonstration")
        assert lines == ["D"]
        assert body == "Demonstration"

    def test_other_single_letter_codes(self) -> None:
        # Pin behaviour for other plausible single-letter WL lines.
        # ``J`` / ``O`` are historical Vienna tram services. Whether
        # WL ever ships them is upstream's decision; the parser must
        # not crash and must recognise them as line codes.
        for letter in ("J", "O", "A"):
            body, lines = _extract_prefix_lines(f"{letter}: Hinweis")
            assert lines == [letter], (
                f"{letter} not recognised as line code"
            )
            assert body == "Hinweis"

    def test_mixed_single_letter_and_digit_line(self) -> None:
        # ``D/U6: …`` — bare letter and digit-bearing code together.
        body, lines = _extract_prefix_lines("D/U6: Stadt sperrt")
        assert lines == ["D", "U6"]
        assert body == "Stadt sperrt"


class TestGenericWordPrefixesStillRejected:
    """The Round 33-follow-up false-positive guards must not regress."""

    def test_achtung_prefix_rejected(self) -> None:
        body, lines = _extract_prefix_lines("Achtung: Sperre wegen Bauarbeiten")
        assert lines == []
        assert body == "Achtung: Sperre wegen Bauarbeiten"

    def test_hinweis_prefix_rejected(self) -> None:
        body, lines = _extract_prefix_lines("Hinweis: Verspätung erwartet")
        assert lines == []
        assert body == "Hinweis: Verspätung erwartet"

    def test_information_prefix_rejected(self) -> None:
        body, lines = _extract_prefix_lines("Information: Umleitung der Linie")
        assert lines == []
        assert body == "Information: Umleitung der Linie"

    def test_linie_word_prefix_rejected(self) -> None:
        body, lines = _extract_prefix_lines("Linie: 40 wird umgeleitet")
        assert lines == []
        assert body == "Linie: 40 wird umgeleitet"

    def test_time_prefix_rejected(self) -> None:
        body, lines = _extract_prefix_lines("17:30 Uhr Verspätung")
        assert lines == []
        assert body == "17:30 Uhr Verspätung"


class TestEnsureLinePrefixDoesNotStackSingleLetter:
    """The fetch-time stacking root cause must be closed."""

    def test_d_relatedlines_does_not_stack_on_existing_d_prefix(self) -> None:
        # Real WL OGD scenario: title already has ``D:``, relatedLines
        # also ships ``["D"]``. Result must be a single ``D:`` prefix,
        # not ``D: D:``.
        result = _ensure_line_prefix("D: Demonstration", ["D"])
        assert result == "D: Demonstration"

    def test_d_relatedlines_promotes_unprefixed_body(self) -> None:
        # WL ships ``title="Demonstration"`` (no prefix) and
        # ``relatedLines=["D"]``. The renderer adds the prefix once.
        result = _ensure_line_prefix("Demonstration", ["D"])
        assert result == "D: Demonstration"

    def test_d_already_canonical_round_trips(self) -> None:
        # No change needed when both sides agree on a single ``D:``.
        result = _ensure_line_prefix("D: Demonstration", [])
        assert result == "D: Demonstration"


class TestPostFilterWlCollapsesStackedSingleLetter:
    """Defence-in-depth: cache-read time rebuild on a stacked title."""

    def test_d_d_demonstration_cache_collapsed(self) -> None:
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Störung",
            "title": "D: D: Demonstration",
            "description": "Linie D: Unregelmäßige Intervalle.",
            "guid": "aaa",
        }]
        out = _post_filter_wl(items)
        assert len(out) == 1
        assert out[0]["title"] == "D: Demonstration"
        assert out[0]["description"] == "Unregelmäßige Intervalle."

    def test_d_d_does_not_drop_the_meldung(self) -> None:
        # Round 31's "no line prefix → drop" guard must continue to
        # PASS the rebuilt ``D:`` form. Pre-fix, ``_WL_LINE_PREFIX_RE``
        # accepts ``D:`` so this already works; the test pins it.
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Störung",
            "title": "D: D: Demonstration",
            "description": "Linie D: Unregelmäßige Intervalle.",
            "guid": "aaa",
        }]
        out = _post_filter_wl(items)
        assert len(out) == 1, (
            "Stacked single-letter title was dropped instead of rebuilt"
        )


class TestDescriptionLinePrefixStripForSingleLetter:
    def test_linie_d_word_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "Linie D: Unregelmäßige Intervalle in beiden Richtungen."
        ) == "Unregelmäßige Intervalle in beiden Richtungen."

    def test_compact_d_prefix_stripped(self) -> None:
        assert _strip_wl_description_line_prefix(
            "D: Foo bar baz"
        ) == "Foo bar baz"

    def test_existing_digit_line_codes_still_stripped(self) -> None:
        # Round 33's compact-form behaviour must not regress.
        cases = [
            ("Linie 40: Nach einer Fahrtbehinderung.", "Nach einer Fahrtbehinderung."),
            ("Linien 40/41: Umleitung", "Umleitung"),
            ("40+41: Betrieb ab Gersthof", "Betrieb ab Gersthof"),
            ("U6: Verspätung", "Verspätung"),
        ]
        for desc, expected in cases:
            assert _strip_wl_description_line_prefix(desc) == expected

    def test_generic_word_descriptions_still_kept(self) -> None:
        for desc in [
            "Verspätung wegen Schaden",
            "17:30 Uhr Beginn",
            "Achtung: Sperre wegen Bauarbeiten",
            "Information: Umleitung der Linie",
            "Strecke: Heiligenstadt — Floridsdorf",
        ]:
            assert _strip_wl_description_line_prefix(desc) == desc, (
                f"Falsely stripped: {desc!r}"
            )


class TestEndToEndUserBugCleanFeedItem:
    """The user's exact reproduction must render cleanly through the pipeline."""

    def test_user_reported_demonstration_d_cleaned(self) -> None:
        items: list[dict[str, Any]] = [{
            "source": "Wiener Linien",
            "category": "Störung",
            "title": "D: D: Demonstration",
            "description": (
                "Linie D: Unregelmäßige Intervalle in beiden Richtungen. "
                "Grund: Demonstration."
            ),
            "link": "https://www.wienerlinien.at/ogd_realtime",
            "guid": (
                "20afbfca21c790691b2b60903b6da68364ee59e3bc83d2b8ea61832b2baa30d0"
            ),
            "starts_at": "2026-05-22T16:22:00+02:00",
            "ends_at": "2026-05-22T23:55:00+02:00",
            "pubDate": "2026-05-22T16:22:00+02:00",
        }]

        # 1. Post-filter cleans cached title + description.
        out = _post_filter_wl(items)
        assert len(out) == 1
        item = out[0]
        # The stacked prefix collapses to a single ``D:``.
        assert item["title"] == "D: Demonstration"
        # The redundant ``Linie D:`` is removed from the description.
        assert not item["description"].startswith("Linie D:")
        assert not item["description"].startswith("D:")
        assert "Unregelmäßige Intervalle" in item["description"]

        # 2. Final rendering preserves the cleanup.
        feed_item = cast(FeedItem, item)
        starts_at = datetime.fromisoformat(str(item["starts_at"]))
        ends_at = datetime.fromisoformat(str(item["ends_at"]))
        formatted = build_feed._format_item_content(
            feed_item, ident="demo-d", starts_at=starts_at, ends_at=ends_at
        )
        # Rendered title shows the canonical single ``D:`` prefix.
        assert formatted.title_out.startswith("D:")
        assert "D: D:" not in formatted.title_out
        # Rendered description carries no redundant line prefix.
        assert "Linie D:" not in formatted.desc_text_truncated
