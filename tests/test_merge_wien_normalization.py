"""Bug b6: the city qualifier "Wien" must not bridge two different routes
into one merged feed item, while the same incident reported with an
abbreviated vs. spelled-out Bahnhof name must still dedup across providers.

The fix pairs two changes in :mod:`src.feed.merge`: ``_normalize_name`` now
folds the Bahnhof-family abbreviations (Hbf/Bhf/Bf → …bahnhof) before
tokenising, and ``wien``/``vienna`` join ``_STOP_WORDS``. Together they keep
the distinctive station token shared across "Hbf"/"Hauptbahnhof" reports
while stripping the ubiquitous "Wien" that otherwise inflates the overlap of
two distinct routes.
"""

from typing import Any

from src.feed.merge import deduplicate_fuzzy


def test_bahnhof_abbreviation_merges_cross_provider() -> None:
    # The SAME incident reported as "Wien Hbf" (ÖBB) and "Wien
    # Hauptbahnhof" (WL) must still dedup once "wien" is a stop word — the
    # Hbf→Hauptbahnhof normalisation keeps the station token shared (without
    # it, the only meaningful tokens would be {hbf}→∅ vs {hauptbahnhof}).
    items: list[dict[str, Any]] = [
        {"guid": "a", "_identity": "oebb|a", "source": "ÖBB", "title": "S1: Wien Hbf"},
        {
            "guid": "b",
            "_identity": "wl|b",
            "source": "Wiener Linien",
            "title": "S1: Wien Hauptbahnhof",
        },
    ]
    assert len(deduplicate_fuzzy(items)) == 1


def test_wien_qualifier_does_not_bridge_distinct_routes() -> None:
    # Two DIFFERENT routes on the same line that share only the city
    # qualifier "Wien" must stay separate now that "wien" is a stop word.
    items: list[dict[str, Any]] = [
        {
            "guid": "a",
            "_identity": "x|a",
            "source": "ÖBB",
            "title": "S1: Wien Meidling ↔ Wien Liesing",
        },
        {
            "guid": "b",
            "_identity": "x|b",
            "source": "ÖBB",
            "title": "S1: Wien Meidling ↔ Wien Floridsdorf",
        },
    ]
    assert len(deduplicate_fuzzy(items)) == 2
