"""Regression tests for Bug 26A (sentence split misfires on German abbreviations).

Real ÖBB descriptions contain German abbreviations that look like
sentence ends but aren't:

* ``Gerasdorf b. Wien Bahnhst`` — ``b.`` is the abbreviation for "bei"
  (near). The single letter before the period made the previous
  sentence-split regex (which only required one letter) fire — sentence
  boundaries landed mid-station-name.
* ``Bahnhst bzw. Gerasdorf`` — ``bzw.`` is the abbreviation for
  "beziehungsweise" (or). 3 letters before the period made the 2-letter
  rule fire too.
* ``Karlsplatz U. (Bereich)`` — single-letter U-Bahn marker.

The fix tightens the lookbehind to require at least FOUR letters
before the period. Real German content words ending sentences are
typically ≥4 letters (``fahren.``, ``möglich.``, ``Richtungen.``);
abbreviations are typically ≤3 letters.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


def _format(raw_desc: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": "stub",
            "description": raw_desc,
            "source": "ÖBB",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None
    )
    return formatted.desc_text_truncated


class TestSentenceSplitGermanAbbreviation:
    def test_gerasdorf_b_wien_not_split(self) -> None:
        # Cache item #5 reproduction.
        raw = (
            "Wegen Bauarbeiten können zwischen Wien Floridsdorf Bahnhof "
            "(U) und Wien Jedlersdorf Bahnhst, Wien Süßenbrunn Bahnhst "
            "bzw. Gerasdorf b. Wien Bahnhst von 10.08.2026 bis 06.09.2026 "
            "keine Nahverkehrszüge fahren."
        )
        out = _format(raw)
        # The "Gerasdorf b. Wien" must NOT be split — it's one station.
        # Either the full station name appears, or the truncation cuts
        # somewhere later. Either way "b." cannot end a "sentence" that
        # is then surfaced separately as the visible summary.
        assert "Gerasdorf b." in out or "Bahnhst bzw." in out
        # The original always-bad output ended with "Gerasdorf b." with
        # no continuation; we never want the visible summary to land on
        # ``b.`` followed only by the timeframe bracket.
        # Build the unwanted shape and assert absence:
        assert not out.startswith(
            "Wegen Bauarbeiten können zwischen Wien Floridsdorf Bahnhof "
            "(U) und Wien Jedlersdorf Bahnhst, Wien Süßenbrunn Bahnhst "
            "bzw. Gerasdorf b. ["
        )

    def test_bzw_not_a_sentence_boundary(self) -> None:
        raw = (
            "Linie 50A bzw. Linie 49A werden umgeleitet. Wir bitten um "
            "Verständnis."
        )
        out = _format(raw)
        # "bzw." should not split between Linie 50A and Linie 49A.
        assert "Linie 50A bzw. Linie 49A" in out

    def test_karlsplatz_u_not_a_sentence_boundary(self) -> None:
        raw = (
            "Linie 62: Unregelmäßige Intervalle in Richtung Oper, "
            "Karlsplatz U. Grund: Rettungseinsatz."
        )
        out = _format(raw)
        # The full text fits in 180 chars so both phrases survive — and
        # the "U." between "Karlsplatz" and "Grund" must not split.
        assert "Karlsplatz U." in out
        assert "Grund: Rettungseinsatz" in out

    def test_real_sentence_boundary_still_splits(self) -> None:
        # When the period IS a real sentence end (4+ letter word
        # before), the split still works — we don't lose the cause
        # clause.
        raw = (
            "Wegen Personen im Gleisbereich waren zwischen Wien Liesing "
            "und Wien Hetzendorf bis 17:19 Uhr keine Fahrten möglich. "
            "Reisende werden gebeten Alternativen zu nutzen die im Bereich "
            "der Innenstadt verfügbar sind und Restverspätungen abklingen."
        )
        out = _format(raw)
        # The first sentence should appear in the output (with possible
        # truncation past it).
        assert "möglich" in out

    def test_short_text_unchanged(self) -> None:
        raw = "Kurze Meldung. Keine Auswirkung."
        out = _format(raw)
        # Both sentences fit, both appear.
        assert "Kurze Meldung" in out
        assert "Keine Auswirkung" in out
