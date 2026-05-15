"""Trigger semantics for :mod:`src.feed.stammstrecke`.

The Stammstrecke feed event is fired when at least **two** CSV rows in
a direction's last-hour window carry a sample-mean delay strictly
greater than :data:`DELAY_THRESHOLD_MINUTES` (9 minutes). Each CSV row
is itself the mean of multiple trains finalised by the same cron tick
(typically 10-15 since the 2026-05-15 ``/departureBoard`` + track-filter
migration), so the trigger requires sustained widespread delay, not a
single outlier train.

This module pins:

1. The canonical happy-path: 2 ``Praterstern`` rows above threshold →
   one event with the new ``stammstrecke_delay_praterstern`` GUID
   prefix.
2. The mixed-source legacy compatibility: a legacy ``"Floridsdorf"``
   row from before the 2026-05-15 rename participates in the same
   direction's trigger via the ``DIRECTIONS_BY_LABEL`` alias resolver.
3. The threshold gate: a single row above 9 min must NOT fire (avoid
   single-tick outliers from triggering the feed entry).
4. The empty / sub-threshold cases: no rows or all rows ≤ 9 min must
   produce zero events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.feed import stammstrecke as feed_module
from src.feed.stammstrecke import (
    DELAY_THRESHOLD_MINUTES,
    FEED_WINDOW,
    compute_stammstrecke_events,
)
from src.utils.stats import StammstreckeObservation


VIENNA_TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 5, 15, 16, 30, 0, tzinfo=VIENNA_TZ)


def _obs(
    *,
    when: datetime,
    direction: str,
    delay: float,
) -> StammstreckeObservation:
    return StammstreckeObservation(
        timestamp=when, direction=direction, delay_minutes=delay
    )


def _run(observations: list[StammstreckeObservation]) -> list[dict[str, Any]]:
    with patch.object(
        feed_module,
        "read_recent_stammstrecke_observations",
        return_value=observations,
    ):
        return compute_stammstrecke_events(now=NOW)


# ---- Happy path: canonical Praterstern label fires the trigger ----------


def test_two_praterstern_rows_above_threshold_fire_event() -> None:
    """Two ``"Praterstern"``-direction rows > 9 min within the window → 1 event."""

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Praterstern", delay=10.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=11.5),
    ]
    events = _run(obs)
    assert len(events) == 1
    event = events[0]
    assert "in Richtung Praterstern" in event["description"]
    assert event["_identity"].startswith("stammstrecke_delay_praterstern|")


def test_two_meidling_rows_above_threshold_fire_event() -> None:
    """Two ``"Meidling"``-direction rows > 9 min within the window → 1 event.

    Symmetry pin for the southbound direction. The trigger pipeline
    is direction-agnostic (the same ``_episode_start``,
    ``_build_event``, ``_observe_legs`` codepath serves both buckets),
    but a dedicated test guards against an asymmetric regression
    landing on only one direction — e.g., a future ``DIRECTIONS``
    tuple reorder that inadvertently shadowed Meidling, or a typo
    in the ``DIRECTIONS_BY_LABEL`` lookup that handled Praterstern
    but mis-routed Meidling.
    """

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Meidling", delay=10.5),
        _obs(when=NOW - timedelta(minutes=5), direction="Meidling", delay=12.0),
    ]
    events = _run(obs)
    assert len(events) == 1
    event = events[0]
    assert "in Richtung Meidling" in event["description"]
    assert event["_identity"].startswith("stammstrecke_delay_meidling|")
    # Meidling has no legacy alias (only the northbound label was
    # renamed in 2026-05-15), so the identity_prefix is the original
    # 2026-05-09 value.
    assert "stammstrecke_delay_praterstern" not in event["_identity"]


# ---- Backwards compat: legacy "Floridsdorf" rows fold into Praterstern --


def test_legacy_floridsdorf_rows_fold_into_praterstern_trigger() -> None:
    """Two legacy ``"Floridsdorf"`` rows still trigger via DIRECTIONS_BY_LABEL alias.

    Regression test for the 2026-05-15 rename: backup-restored or hand-
    edited CSV rows that carry the pre-rename direction value must
    still participate in the trigger evaluation. The canonical
    direction label resolver in :func:`compute_stammstrecke_events`
    canonicalises ``"Floridsdorf"`` → ``"Praterstern"`` via
    :data:`DIRECTIONS_BY_LABEL` so the loop's ``direction.target_label``
    lookup finds the observations.
    """

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Floridsdorf", delay=12.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Floridsdorf", delay=15.0),
    ]
    events = _run(obs)
    assert len(events) == 1
    event = events[0]
    # Description renders the *canonical* label even when input rows
    # used the legacy value — the rename is operator-visible.
    assert "in Richtung Praterstern" in event["description"]
    assert event["_identity"].startswith("stammstrecke_delay_praterstern|")


def test_mixed_legacy_and_canonical_rows_count_together() -> None:
    """One legacy + one canonical row jointly satisfy the 2-row threshold."""

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Floridsdorf", delay=10.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=10.0),
    ]
    events = _run(obs)
    assert len(events) == 1
    assert "in Richtung Praterstern" in events[0]["description"]


# ---- Threshold gate: single high row must NOT fire ----------------------


def test_single_row_above_threshold_does_not_fire() -> None:
    """A single sample-mean above 9 min is treated as outlier — no event.

    Per the module docstring's "defensive: a single high outlier cannot
    push the direction past the threshold on its own" invariant.
    """

    obs = [
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=20.0),
    ]
    events = _run(obs)
    assert events == []


def test_all_rows_at_or_below_threshold_do_not_fire() -> None:
    """Strictly-greater-than gate: rows with delay == 9.0 do NOT fire."""

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Praterstern", delay=9.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=9.0),
    ]
    events = _run(obs)
    assert events == []


def test_rows_outside_feed_window_are_ignored() -> None:
    """Observations older than the 1-hour feed window don't contribute."""

    just_outside = NOW - FEED_WINDOW - timedelta(seconds=1)
    obs = [
        _obs(when=just_outside, direction="Praterstern", delay=20.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=20.0),
    ]
    events = _run(obs)
    # Only 1 row is inside the feed window → trigger requires 2 →
    # no event.
    assert events == []


def test_empty_observations_yield_no_events() -> None:
    """No CSV rows → no events (defensive fast-exit)."""

    events = _run([])
    assert events == []


# ---- Direction isolation: north and south fire independently -----------


def test_two_directions_with_concurrent_disruption_emit_two_events() -> None:
    """Both Meidling and Praterstern over threshold → two events, one per
    direction, in registry order (Meidling first, Praterstern second)."""

    obs = [
        _obs(when=NOW - timedelta(minutes=30), direction="Meidling", delay=10.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Meidling", delay=10.0),
        _obs(when=NOW - timedelta(minutes=30), direction="Praterstern", delay=11.0),
        _obs(when=NOW - timedelta(minutes=5), direction="Praterstern", delay=11.0),
    ]
    events = _run(obs)
    assert len(events) == 2
    # Registry order: Meidling first, Praterstern second.
    assert "in Richtung Meidling" in events[0]["description"]
    assert "in Richtung Praterstern" in events[1]["description"]


def test_threshold_constant_is_9_minutes() -> None:
    """Pin the threshold constant so a typo / drift trips the test."""

    assert DELAY_THRESHOLD_MINUTES == 9.0
    assert FEED_WINDOW == timedelta(hours=1)
