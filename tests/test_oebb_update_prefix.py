"""ÖBB's "Update N (date time)" headline prefix is removed from the title.

Published 2026-09-25 in ``docs/feed.xml`` (item 3)::

    Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten

The prefix is ~30 characters a display reader cannot use. It also kept the
category-prefix loop in ``_clean_title_keep_places`` from running: the first
colon sits inside "09:59", which the loop never splits (clock-time guard),
so "St.Pölten" kept its missing space. Four such titles reached the feed
since 2026-08-01; three of them were all-clears ("Aufhebung …"), whose label
must survive — without it the title reads like a running disruption.

Mutations checked against this file (each one caught, by the test named):

* the prefix is not stripped in ``_clean_title_keep_places`` →
  ``test_the_live_titles``.
* the all-clear guard is dropped from the category loop →
  ``test_the_live_titles`` (the three "Aufhebung" cases).
* the cache path in ``_post_filter_oebb`` does not clean prefixed titles →
  ``test_the_cached_title_is_repaired_at_build_time``.
* the prefix regex loses its ``Update N (`` anchor (any leading
  parenthesis stripped) → ``test_other_titles_are_untouched``.
"""

from __future__ import annotations

import pytest

from src.build_feed import _post_filter_oebb
from src.feed_types import FeedItem
from src.providers.oebb import _clean_title_keep_places, _derive_guid, _strip_update_prefix


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The four titles published since 2026-08-01, verbatim.
        ("Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten", "St. Pölten Hauptbahnhof"),
        (
            "Update 1 (19.09.2026 07:43) Aufhebung Verkehrseinschränkung: Wien Handelskai",
            "Aufhebung Verkehrseinschränkung: Wien Handelskai",
        ),
        (
            "Update 4 (16.09.2026 07:55) Aufhebung Verkehrseinschränkung: Wien Floridsdorf",
            "Aufhebung Verkehrseinschränkung: Wien Floridsdorf",
        ),
        (
            "Update 2 (12.09.2026 23:15) Aufhebung Streckenunterbrechung: Wien Meidling",
            "Aufhebung Streckenunterbrechung: Wien Meidling",
        ),
    ],
)
def test_the_live_titles(raw: str, expected: str) -> None:
    assert _clean_title_keep_places(raw) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Update 12 (5.9.2026) Streckenunterbrechung: Wien Meidling",
        "update 3 (25.09.2026, 09:59) - Verkehrseinschränkung: Wien Meidling",
    ],
)
def test_prefix_variants_are_stripped(title: str) -> None:
    assert not _strip_update_prefix(title).lower().startswith("update")


@pytest.mark.parametrize(
    "title",
    [
        "REX 7: Wien Hauptbahnhof ↔ Flughafen Wien",
        "Wien Meidling ↔ Wien Liesing",
        "(S 50) Wien Westbahnhof",
        # Only ÖBB's own "Update N (…)" headline goes, not a bare dated aside.
        "(25.09.2026 09:59) Wien Meidling",
        "Updatearbeiten: Wien Meidling",
    ],
)
def test_other_titles_are_untouched(title: str) -> None:
    assert _strip_update_prefix(title) == title


def test_an_all_clear_without_prefix_keeps_its_label() -> None:
    assert _clean_title_keep_places("Aufhebung Verkehrseinschränkung: Wien Handelskai") == (
        "Aufhebung Verkehrseinschränkung: Wien Handelskai"
    )


def test_a_plain_category_prefix_is_still_dropped() -> None:
    assert _clean_title_keep_places("Verkehrseinschränkung: Wien Meidling") == "Wien Meidling"


def test_the_guid_still_follows_the_raw_title() -> None:
    raw = "Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten"
    assert _derive_guid("", raw, "https://example.invalid/x") != _derive_guid(
        "", _clean_title_keep_places(raw), "https://example.invalid/x"
    )


def _cached_item(title: str) -> FeedItem:
    return {
        "source": "ÖBB",
        "category": "Störung",
        "title": title,
        # Verbatim from cache/oebb_c40d21/events.json, 2026-09-25.
        "description": (
            "25.09.2026<br/><br/>Wegen einer Weichenstörung sind in <b>St.Pölten Hbf</b>"
            "<b> Zugfahrten</b> bis voraussichtlich <b>11:00 Uhr nur eingeschränkt </b>"
            "möglich. Planen Sie bis zu<b> 20 Minuten </b>mehr Reisezeit ein."
        ),
        "guid": "g",
        "link": "https://www.oebb.at/",
    }


def test_the_cached_title_is_repaired_at_build_time() -> None:
    cached = "Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten"
    out = _post_filter_oebb([_cached_item(cached)])
    assert [i["title"] for i in out] == ["St. Pölten Hauptbahnhof"]


def test_the_cached_item_is_not_mutated() -> None:
    item = _cached_item("Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten")
    _post_filter_oebb([item])
    assert item["title"].startswith("Update 4")
