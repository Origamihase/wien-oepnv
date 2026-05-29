"""Regression tests for three WL-provider correctness fixes in ``wl_fetch``.

1. ``_iso`` must not raise on a truthy non-string timestamp (numeric epoch,
   list, dict). The previous ``s.replace(...)`` after only a falsy check
   raised ``AttributeError`` — uncaught by the ``isoparse`` guard — which
   propagated out of the unguarded ``fetch_events`` item loop and disabled
   the whole WL cache refresh for the cycle.
2. The finished/inactive status filter must match on WORD BOUNDARIES, not
   bare substrings: the active German statuses ``laufende`` (ongoing) and
   ``ausstehende`` (pending) — and the noun ``Wochenende`` — contain the
   substring ``ende`` and were wrongly dropped, discarding live disruptions.
3. ``_wl_identity`` must not collide for two genuinely distinct items that
   share a (possibly empty) line set AND lack a parseable date. The base
   ``L=…|D=…`` key is the authoritative dedup key (``_dedupe_items`` keys on
   ``_identity`` first), so a collision silently drops a valid disruption.
"""

from __future__ import annotations

from datetime import datetime, UTC

from src.providers.wl_fetch import (
    _best_ts,
    _is_inactive_status,
    _iso,
    _wl_identity,
)


# ---- 1. _iso / _best_ts robustness against non-string timestamps --------


def test_iso_returns_none_for_truthy_non_string_values() -> None:
    """A numeric epoch / list / dict must yield ``None``, never raise."""
    assert _iso(1700000000) is None
    assert _iso(1700000000.0) is None
    assert _iso([2026, 1, 1]) is None
    assert _iso({"start": "2026-01-01"}) is None
    assert _iso(True) is None


def test_iso_still_parses_valid_strings() -> None:
    """The fix must not regress normal string parsing (incl. trailing Z)."""
    parsed = _iso("2026-01-01T00:00:00Z")
    assert parsed == datetime(2026, 1, 1, tzinfo=UTC)
    assert _iso("") is None
    assert _iso(None) is None


def test_best_ts_survives_numeric_start_and_falls_through() -> None:
    """A numeric ``time.start`` must not abort; a later valid field wins.

    Pre-fix ``_best_ts({"time": {"start": 1700000000}, ...})`` raised
    ``AttributeError`` on the very first candidate, aborting ``fetch_events``.
    """
    ts = _best_ts(
        {"time": {"start": 1700000000}, "updated": "2026-01-01T00:00:00Z"}
    )
    assert ts == datetime(2026, 1, 1, tzinfo=UTC)
    # All candidates non-string → None, still no raise.
    assert _best_ts({"time": {"start": [1, 2]}, "updated": 42}) is None


# ---- 2. status filter: word boundaries, not bare substrings -------------


def test_active_statuses_with_ende_substring_are_not_inactive() -> None:
    """``laufende`` / ``ausstehende`` / ``Wochenende`` must NOT be dropped."""
    assert _is_inactive_status("laufende") is False
    assert _is_inactive_status("ausstehende") is False
    assert _is_inactive_status("Wochenende") is False
    assert _is_inactive_status("aktiv", "laufend") is False
    # Across multiple fields (status / attrs.status / attrs.state).
    assert _is_inactive_status("", "laufende", "") is False


def test_genuinely_inactive_statuses_are_still_dropped() -> None:
    """The real finished/inactive keywords must still match (any field)."""
    for s in (
        "finished",
        "inactive",
        "inaktiv",
        "done",
        "closed",
        "nicht aktiv",
        "ended",
        "Ende",
        "abgeschlossen",
        "beendet",
        "geschlossen",
    ):
        assert _is_inactive_status(s) is True, s
    assert _is_inactive_status("", "", "beendet") is True
    assert _is_inactive_status(None, 0, "FINISHED") is True  # case-insensitive


# ---- 3. _wl_identity: no collision for weak (line-less / date-less) keys -


def test_weak_identity_discriminates_distinct_topics() -> None:
    """Two distinct line-less, date-less items must get distinct identities."""
    a = _wl_identity("hinweis", [], None, "umleitung_innere_stadt")
    b = _wl_identity("hinweis", [], None, "ersatzverkehr_donaustadt")
    assert a != b
    assert a.endswith("|TK=umleitung_innere_stadt")
    # Same line set (U6) but no date — still must not collide.
    c = _wl_identity("störung", [("U6", "U6")], None, "aufzug")
    d = _wl_identity("störung", [("U6", "U6")], None, "weichenstoerung")
    assert c != d
    assert "|D=None|TK=" in c


def test_strong_identity_is_unchanged_so_first_seen_does_not_churn() -> None:
    """Lines + date present → byte-identical to the pre-fix format (no TK)."""
    ident = _wl_identity(
        "störung", [("U6", "U6")], datetime(2026, 1, 1, tzinfo=UTC), "anything"
    )
    assert ident == "wl|störung|L=U6|D=2026-01-01"
    assert "TK=" not in ident


def test_identical_weak_items_still_dedupe() -> None:
    """Same topic + same (empty) lines + same date-status → same identity.

    The discriminator must not over-split: genuine duplicates (which share a
    topic_key) must still collapse so ``_dedupe_items`` keeps exactly one.
    """
    a = _wl_identity("hinweis", [], None, "same_topic")
    b = _wl_identity("hinweis", [], None, "same_topic")
    assert a == b
