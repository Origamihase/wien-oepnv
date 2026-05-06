"""Regression tests for Bug 11A (compound ``-Bahnhof`` truncation).

ÖBB descriptions and titles regularly mention ``Wien
Franz-Josefs-Bahnhof`` — a hyphen-compound proper noun where ``Bahnhof``
is part of the station name itself, not a generic suffix. The previous
``_BAHNHOF_TRAILING_RE`` matched ``Bahnhof`` at end-of-string regardless
of the preceding character, so normalisation produced the dangling
``Wien Franz-Josefs-`` form. Alias resolution in ``stations.json`` happened
to bridge the truncation today, but:

- Visible titles read ``Wien Franz-Josefs- ↔ Wien Heiligenstadt`` whenever
  the alias chain breaks (e.g. directory drift, missing entry).
- Dedup keys in ``_extract_routes`` used the truncated form, so the same
  route written ``Wien Franz-Josefs-Bahnhof`` and ``Wien Franz-Josefs``
  would deduplicate correctly only because both fall through to the same
  truncation — a fragile coincidence.

The fix anchors the trailing-suffix regex at end-of-string AND adds a
negative lookbehind that rejects a hyphen (or any Unicode dash) before
the suffix. ``Wien Hauptbahnhof`` and ``Wiener Neustadt Hauptbahnhof``
keep their existing trim because the suffix is space-separated.
"""

from __future__ import annotations

from src.providers.oebb import _normalize_endpoint_name


class TestCompoundBahnhofPreserved:
    def test_wien_franz_josefs_bahnhof_kept_intact(self) -> None:
        assert _normalize_endpoint_name("Wien Franz-Josefs-Bahnhof") == (
            "Wien Franz-Josefs-Bahnhof"
        )

    def test_franz_josefs_bahnhof_kept_intact(self) -> None:
        # Without the Wien prefix — same protection applies.
        assert _normalize_endpoint_name("Franz-Josefs-Bahnhof") == (
            "Franz-Josefs-Bahnhof"
        )

    def test_wien_hauptbahnhof_still_strips(self) -> None:
        # Standard suffix removal must continue to work.
        assert _normalize_endpoint_name("Wien Hauptbahnhof") == "Wien"

    def test_wiener_neustadt_hauptbahnhof_still_strips(self) -> None:
        assert _normalize_endpoint_name("Wiener Neustadt Hauptbahnhof") == (
            "Wiener Neustadt"
        )

    def test_wien_hbf_still_strips(self) -> None:
        assert _normalize_endpoint_name("Wien Hbf") == "Wien"

    def test_moedling_bf_still_strips(self) -> None:
        assert _normalize_endpoint_name("Mödling Bf") == "Mödling"

    def test_compound_with_endash_kept_intact(self) -> None:
        # Defence in depth: en-dash and em-dash compounds also stay intact.
        assert _normalize_endpoint_name("Foo–Bahnhof").endswith("Bahnhof")
        assert _normalize_endpoint_name("Foo—Bahnhof").endswith("Bahnhof")

    def test_st_poelten_hauptbahnhof_still_strips(self) -> None:
        # St. Pölten Hauptbahnhof — abbreviation-with-period must keep
        # behaving the same: trim "Hauptbahnhof" off, leave "St. Pölten".
        assert _normalize_endpoint_name("St. Pölten Hauptbahnhof") == "St. Pölten"
