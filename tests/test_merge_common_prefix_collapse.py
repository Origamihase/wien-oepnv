"""Regression test for Bug 36A (verbose ``&``-joined merged titles).

User audit feedback: the feed surfaced two visibly-broken titles
created by ``deduplicate_fuzzy``'s legacy ``ex_name & name`` join::

    11A: Veranstaltung am 09.06.2026 & Veranstaltung am 03.06.2026
       & Veranstaltung am 11.06.2026 & Veranstaltung am 20.06.2026

    43: Benutzen Sie die Linie 43A - Dornbacher Straße 85
       & Benutzen Sie die Linie 43A - Alszeile 93

Both are real WL Hinweis items republished by WL once per affected
date/location. The legacy join repeated the entire body verbatim,
producing 96–122-character walls of text.

Fix
===
``src/feed/merge.py:_collapse_common_prefix`` detects substantial
shared word-aligned prefixes between ``ex_name`` and ``name`` and
keeps the prefix once while joining the differing suffixes with
``, ``. When ALL suffixes are calendar dates (``DD.MM.YYYY``) they
are sorted chronologically so the user sees events in time order
rather than cache-insertion order.

Guards
------
* ``min_prefix=10`` — short overlaps (``Sperre`` between two unrelated
  bodies) don't trigger.
* ``max_suffix=60`` — long sentence suffixes fall back to the
  explicit ``&`` join (which is more readable for full sentences
  than a stray comma).
* ``↔`` skip — ÖBB chain routes (``A ↔ B ↔ C``) use ``↔`` as a
  chain joiner, not a separator, so the collapse must not touch
  them.
"""

from __future__ import annotations

from typing import Any

from src.feed.merge import _collapse_common_prefix


class TestCommonPrefixCollapseUserCases:
    def test_four_veranstaltung_dates_chained_collapse(self) -> None:
        # Simulate the chained 4-way merge that ``deduplicate_fuzzy``
        # performs item-by-item on the user-visible 11A case.
        state = "Veranstaltung am 09.06.2026"
        for new in (
            "Veranstaltung am 03.06.2026",
            "Veranstaltung am 11.06.2026",
            "Veranstaltung am 20.06.2026",
        ):
            collapsed = _collapse_common_prefix(state, new)
            assert collapsed is not None, (
                f"Collapse declined on {state!r} + {new!r}"
            )
            state = collapsed
        # Dates are sorted chronologically.
        assert state == (
            "Veranstaltung am 03.06.2026, 09.06.2026, "
            "11.06.2026, 20.06.2026"
        )

    def test_43_benutzen_sie_locations_collapse(self) -> None:
        # The 43-line case: same "Benutzen Sie die Linie 43A - "
        # prefix, different location suffixes.
        result = _collapse_common_prefix(
            "Benutzen Sie die Linie 43A - Dornbacher Straße 85",
            "Benutzen Sie die Linie 43A - Alszeile 93",
        )
        # Non-date suffixes preserve cache-insertion order.
        assert result == (
            "Benutzen Sie die Linie 43A - Dornbacher Straße 85, "
            "Alszeile 93"
        )

    def test_dedup_when_new_suffix_already_present(self) -> None:
        # A republished item carrying the same date that the existing
        # list already includes must not duplicate.
        result = _collapse_common_prefix(
            "Veranstaltung am 03.06.2026, 09.06.2026",
            "Veranstaltung am 03.06.2026",
        )
        assert result == "Veranstaltung am 03.06.2026, 09.06.2026"


class TestCommonPrefixCollapseGuards:
    def test_short_common_prefix_declines(self) -> None:
        # ``Sperre `` is only 7 chars — below the 10-char minimum.
        result = _collapse_common_prefix("Sperre Foo", "Sperre Bar")
        assert result is None

    def test_no_common_prefix_declines(self) -> None:
        result = _collapse_common_prefix("Falschparker", "Sperre Foo")
        assert result is None

    def test_oebb_chain_route_skipped(self) -> None:
        # ÖBB routes use ``↔`` as a chain joiner — never collapse.
        result = _collapse_common_prefix(
            "Wien Hbf ↔ Wien Mitte",
            "Wien Mitte ↔ Flughafen Wien",
        )
        assert result is None

    def test_long_suffix_declines(self) -> None:
        # A 70-char suffix exceeds the 60-char cap.
        prefix = "Gleisbauarbeiten "
        long_suffix = "x" * 70
        result = _collapse_common_prefix(
            prefix + "kurz",
            prefix + long_suffix,
        )
        assert result is None

    def test_word_boundary_required(self) -> None:
        # ``Veranstaltun`` is mid-word; must not be a valid prefix
        # for ``Veranstaltung`` vs ``Veranstaltungen``.
        result = _collapse_common_prefix(
            "Veranstaltungen Test",  # different word continuation
            "Veranstaltung der Linie",
        )
        # Common chars are ``Veranstaltung`` (13) but no space after;
        # backtrack to last word boundary finds nothing → decline.
        assert result is None

    def test_empty_inputs_decline(self) -> None:
        assert _collapse_common_prefix("", "Foo bar baz") is None
        assert _collapse_common_prefix("Foo bar baz", "") is None
        assert _collapse_common_prefix("", "") is None


class TestCommonPrefixCollapseDateSorting:
    def test_dates_sorted_chronologically(self) -> None:
        # Out-of-order dates collapse into ascending order.
        result = _collapse_common_prefix(
            "Veranstaltung am 31.12.2026",
            "Veranstaltung am 01.01.2026",
        )
        assert result == "Veranstaltung am 01.01.2026, 31.12.2026"

    def test_dates_across_years_sorted(self) -> None:
        result = _collapse_common_prefix(
            "Veranstaltung am 15.05.2026",
            "Veranstaltung am 10.01.2025",
        )
        assert result == "Veranstaltung am 10.01.2025, 15.05.2026"

    def test_non_date_suffixes_keep_insertion_order(self) -> None:
        # When suffixes aren't dates, preserve cache-insertion order
        # (the older item already in ex_name comes first).
        result = _collapse_common_prefix(
            "Sperre Hauptstraße bei Foo",
            "Sperre Hauptstraße bei Bar",
        )
        # Prefix ``Sperre Hauptstraße bei `` is 23 chars, passes min.
        assert result == "Sperre Hauptstraße bei Foo, Bar"

    def test_mixed_date_and_text_falls_back_to_insertion_order(self) -> None:
        # If even one part isn't a recognisable date, don't reorder
        # any of them — the user's mental model of "list order" is
        # only safe to override when the data is uniformly dates.
        result = _collapse_common_prefix(
            "Veranstaltung am 09.06.2026",
            "Veranstaltung am ungeklärtem Datum",
        )
        # The "ungeklärtem Datum" is too long for the suffix cap? No
        # — only 18 chars. Below 60. Should collapse with insertion
        # order preserved.
        assert result == (
            "Veranstaltung am 09.06.2026, ungeklärtem Datum"
        )


class TestEndToEndDeduplicateFuzzy:
    """The collapse must work through the full ``deduplicate_fuzzy`` pipeline."""

    def test_user_reported_11a_veranstaltung_pipeline(self) -> None:
        from datetime import datetime, timezone

        from src.feed.merge import deduplicate_fuzzy

        def _make(date_str: str, guid: str) -> dict[str, Any]:
            return {
                "source": "Wiener Linien",
                "category": "Hinweis",
                "title": f"11A: Veranstaltung am {date_str}",
                "description": (
                    "Wegen einer Veranstaltung im Ernst-Happel-Stadion "
                    "kommt es zu folgenden Verkehrsmaßnahmen."
                ),
                "link": "",
                "guid": guid,
                "starts_at": "2026-05-21T00:00:00+02:00",
                "ends_at": "2026-06-21T01:00:00+02:00",
                "pubDate": "2026-05-21T00:00:00+02:00",
            }

        items = [
            _make("09.06.2026", "a"),
            _make("03.06.2026", "b"),
            _make("11.06.2026", "c"),
            _make("20.06.2026", "d"),
        ]
        merged = deduplicate_fuzzy(items)
        # All four collapse to one item.
        assert len(merged) == 1
        # Title shows sorted dates without ``&`` repetition.
        title = merged[0]["title"]
        assert "&" not in title, f"Legacy & join survived: {title!r}"
        assert "Veranstaltung am" in title
        # All four dates present, sorted.
        assert title.index("03.06.2026") < title.index("09.06.2026")
        assert title.index("09.06.2026") < title.index("11.06.2026")
        assert title.index("11.06.2026") < title.index("20.06.2026")

    def test_unrelated_items_dont_merge(self) -> None:
        # The collapse only runs on items that ``deduplicate_fuzzy``
        # already decided to merge — the helper itself can't cause
        # unrelated items to merge.
        from src.feed.merge import deduplicate_fuzzy

        items = [
            {
                "source": "Wiener Linien", "category": "Hinweis",
                "title": "11A: Veranstaltung am 09.06.2026",
                "description": "Body A",
                "link": "", "guid": "a",
                "starts_at": "2026-05-21T00:00:00+02:00",
                "ends_at": "2026-06-10T01:00:00+02:00",
                "pubDate": "2026-05-21T00:00:00+02:00",
            },
            {
                "source": "Wiener Linien", "category": "Hinweis",
                "title": "U6: Verspätung",
                "description": "Body completely unrelated",
                "link": "", "guid": "b",
                "starts_at": "2026-05-21T00:00:00+02:00",
                "ends_at": "2026-06-10T01:00:00+02:00",
                "pubDate": "2026-05-21T00:00:00+02:00",
            },
        ]
        merged = deduplicate_fuzzy(items)
        assert len(merged) == 2, (
            "Unrelated items merged spuriously"
        )
