"""Regression tests for the round-15 stations data-integrity bundle.

Pins two defence-in-depth fixes:

1. ``src/utils/stations_validation.py::_format_identifier`` accepts ``str``
   ``bst_id`` values. The canonical writer in
   ``scripts/update_station_directory.py`` serialises ``bst_id`` as
   ``str(self.bst_id)``, so the previous ``isinstance(bst_id, int)``-only
   branch was DEAD code for every real entry — silently omitting
   ``bst_id`` from the identifier the auto-quarantine path uses for
   distinctness. The same file's sibling readers
   (``_find_cross_station_id_conflicts``,
   ``_find_identity_field_conflicts``) already accept ``str | int``.
   Latent hazard: a future merge yielding two ``bst_code``-less entries
   distinguished only by ``bst_id`` would collapse to one identifier
   and re-trigger the documented mass-quarantine failure mode (the
   sibling comment block — "Post-PR #1446 cron tick a23a2a7 confirmed
   1759 quarantined WL entries because all shared ``source:wl``").

2. ``src/places/merge.py::_sorted_stations`` coerces a present-but-``null``
   ``_google_place_id`` to ``""`` before tuple comparison.
   ``dict.get(key, default)`` only returns the default when the key is
   ABSENT, so an operator-edited / legacy / tampered ``data/stations.json``
   carrying ``"_google_place_id": null`` returned ``None`` from ``.get``.
   A pair of same-normalized-name entries where one has ``null`` and the
   other a string id then crashed ``sorted`` with
   ``TypeError: '<' not supported between instances of 'NoneType' and 'str'``,
   aborting the merge.
"""
from __future__ import annotations

from typing import cast

import pytest

from src.places.merge import StationEntry, _sorted_stations
from src.utils.stations_validation import _format_identifier


# ---------------------------------------------------------------------------
# Fix 1 — _format_identifier accepts str bst_id
# ---------------------------------------------------------------------------


def test_format_identifier_includes_string_bst_id() -> None:
    """Canonical writer emits ``bst_id`` as ``str``; the identifier must
    include it. Pre-fix the ``isinstance(bst_id, int)``-only branch dropped
    string ``bst_id`` values silently, weakening the auto-quarantine
    distinctness used by ``_partition_stations``."""
    identifier = _format_identifier(
        {"bst_id": "100", "bst_code": "X", "source": "wl"}
    )
    assert "bst:100" in identifier


def test_format_identifier_still_accepts_int_bst_id() -> None:
    """Backwards-compat guard: an integer ``bst_id`` (legacy / hand-edited)
    must still flow into the identifier."""
    identifier = _format_identifier(
        {"bst_id": 100, "bst_code": "X", "source": "wl"}
    )
    assert "bst:100" in identifier


def test_format_identifier_strips_string_bst_id_whitespace() -> None:
    """Mirror the strip semantics already used by the sibling ``bst_code``
    / ``wl_diva`` / ``source`` branches in the same function."""
    identifier = _format_identifier(
        {"bst_id": "  100  ", "bst_code": "X", "source": "wl"}
    )
    assert "bst:100" in identifier


def test_format_identifier_distinguishes_two_entries_by_bst_id_alone() -> None:
    """The behavioural invariant the comment block documents: two entries
    differing only by ``bst_id`` MUST produce distinct identifiers, so the
    auto-quarantine path cannot collapse them and mass-remove a clean
    station with the flagged one. Pre-fix the string ``bst_id`` was
    dropped and the two entries collapsed to one identifier — the exact
    failure mode the 1759-WL-quarantine comment was written to prevent."""
    a = _format_identifier({"bst_id": "100", "source": "oebb"})
    b = _format_identifier({"bst_id": "200", "source": "oebb"})
    assert a != b


@pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
def test_format_identifier_ignores_empty_string_bst_id(empty: str) -> None:
    """An empty / whitespace-only ``bst_id`` is treated the same as the
    sibling ``bst_code`` empty case — skipped, no ``bst:`` segment."""
    identifier = _format_identifier(
        {"bst_id": empty, "bst_code": "X", "source": "wl"}
    )
    assert "bst:" not in identifier
    assert "code:X" in identifier


# ---------------------------------------------------------------------------
# Fix 2 — _sorted_stations tolerates null _google_place_id
# ---------------------------------------------------------------------------


def _entry(name: str, place_id: object) -> StationEntry:
    """Minimal StationEntry dict shape used by ``_sorted_stations``.

    The bug under test is that on-disk JSON can carry a ``null``
    ``_google_place_id`` despite the TypedDict declaring it as ``str``.
    ``cast`` is therefore necessary to construct the runtime shape the
    bug needs.
    """
    return cast(
        StationEntry,
        {
            "name": name,
            "_google_place_id": place_id,
            "latitude": 48.2,
            "longitude": 16.37,
            "in_vienna": True,
            "pendler": False,
            "aliases": [],
            "source": "google_places",
        },
    )


def test_sorted_stations_handles_null_google_place_id() -> None:
    """Two same-normalized-name entries where one carries
    ``_google_place_id: null`` (present, but null) must NOT crash the
    sort. Pre-fix ``.get("_google_place_id", "")`` returned ``None`` for
    the null entry, and the tuple comparison raised
    ``TypeError: '<' not supported between instances of 'NoneType' and 'str'``."""
    null_entry = _entry("Wien Hauptbahnhof", None)
    real_entry = _entry("Wien Hauptbahnhof", "ChIJabc123")

    out = _sorted_stations([null_entry, real_entry])

    assert len(out) == 2
    # Tuple sort is now string-vs-string, so the null entry (sort key "")
    # consistently precedes the real-id entry. The exact order doesn't
    # matter for the bug — what matters is no TypeError.
    assert {entry["_google_place_id"] for entry in out} == {None, "ChIJabc123"}


def test_sorted_stations_still_orders_real_ids_deterministically() -> None:
    """Regression guard: with two real string ids and the same normalized
    name, the sort still produces a deterministic order."""
    a = _entry("Wien Hauptbahnhof", "ChIJaaa")
    b = _entry("Wien Hauptbahnhof", "ChIJbbb")
    out = _sorted_stations([b, a])
    assert [entry["_google_place_id"] for entry in out] == ["ChIJaaa", "ChIJbbb"]


def test_sorted_stations_handles_missing_google_place_id_key() -> None:
    """A completely absent ``_google_place_id`` key (the ``.get(..., "")``
    default-branch) must also still sort cleanly — guards against
    over-correcting the null fix."""
    missing = _entry("X", "ChIJX")
    del missing["_google_place_id"]
    real = _entry("X", "ChIJX2")
    out = _sorted_stations([missing, real])
    assert len(out) == 2
