"""Regression tests for Bug M1 (VOR↔ÖBB merge keeps stale pubDate).

When a VOR record meets an ÖBB record describing the same disruption,
``deduplicate_fuzzy`` takes one of two specialised branches that
preserve VOR master data and only append the ÖBB description text. The
branches did *not* propagate the newer ``pubDate``, so a fresh ÖBB
report arriving after the VOR one left the merged item with the older
VOR timestamp — pushing the item too far down in the recency-ordered
feed.

The fix introduces ``_promote_newer_dates`` and calls it in both
specialised branches, mirroring the date-promotion behaviour the
standard merge already had.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from src.feed.merge import deduplicate_fuzzy


class TestVorOebbPubDatePromotion:
    def test_oebb_newer_pubdate_promoted_into_vor_master(self) -> None:
        # VOR is master (existing); OEBB report (item) is newer.
        old = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        new = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
        items: list[dict[str, Any]] = [
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "VOR text.",
                "guid": "vor_g1",
                "provider": "vor",
                "source": "vor",
                "pubDate": old,
            },
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "OEBB text with extra detail.",
                "guid": "oebb_g1",
                "provider": "oebb",
                "source": "oebb",
                "pubDate": new,
            },
        ]
        merged = deduplicate_fuzzy(items)
        assert len(merged) == 1
        # VOR remains the master record (its guid wins).
        assert merged[0]["guid"] == "vor_g1"
        # …but the pubDate was promoted to the newer OEBB report.
        assert merged[0]["pubDate"] == new

    def test_vor_newer_pubdate_promoted_into_oebb_master(self) -> None:
        # OEBB is master (existing); VOR report (item) is newer. The
        # branch replaces with VOR but must promote dates from the prior
        # OEBB record only when *they* are newer (defence in depth).
        old = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        new = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
        items: list[dict[str, Any]] = [
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "OEBB text first.",
                "guid": "oebb_g1",
                "provider": "oebb",
                "source": "oebb",
                "pubDate": old,
            },
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "VOR text fresh.",
                "guid": "vor_g1",
                "provider": "vor",
                "source": "vor",
                "pubDate": new,
            },
        ]
        merged = deduplicate_fuzzy(items)
        assert len(merged) == 1
        # VOR replaces — so VOR's newer date stays.
        assert merged[0]["pubDate"] == new
        assert merged[0]["guid"] == "vor_g1"

    def test_oebb_existing_keeps_newer_oebb_date_when_vor_arrives_older(
        self,
    ) -> None:
        # OEBB existing has the newer date; VOR arrives older. The Case 2
        # branch replaces OEBB with VOR but must not lose the newer
        # OEBB date.
        new = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
        old = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        items: list[dict[str, Any]] = [
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "OEBB text fresh.",
                "guid": "oebb_g1",
                "provider": "oebb",
                "source": "oebb",
                "pubDate": new,
            },
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "VOR text older.",
                "guid": "vor_g1",
                "provider": "vor",
                "source": "vor",
                "pubDate": old,
            },
        ]
        merged = deduplicate_fuzzy(items)
        assert len(merged) == 1
        # VOR replaces (master) but the merged pubDate remains the newer
        # OEBB one — feed ordering must reflect the latest report.
        assert merged[0]["pubDate"] == new

    def test_missing_pubdate_in_one_side_does_not_break(self) -> None:
        # Defence in depth: one side has no pubDate at all.
        new = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
        items: list[dict[str, Any]] = [
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "VOR text.",
                "guid": "vor_g1",
                "provider": "vor",
                "source": "vor",
                # no pubDate
            },
            {
                "title": "S1: Weichenstörung Wien Praterstern",
                "description": "OEBB text.",
                "guid": "oebb_g1",
                "provider": "oebb",
                "source": "oebb",
                "pubDate": new,
            },
        ]
        merged = deduplicate_fuzzy(items)
        assert len(merged) == 1
        # VOR master with the OEBB-side pubDate filled in.
        assert merged[0]["pubDate"] == new
        assert merged[0]["guid"] == "vor_g1"
