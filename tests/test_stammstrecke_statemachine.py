"""Regression tests for the Stammstrecke statemachine PR (PR5).

Three findings in one themed PR:

1. HIGH — ``src/feed/stammstrecke.py::_episode_start`` 6h-window-drift:
   ``compute_stammstrecke_events`` reads observations with
   ``window=episode_lookback`` (6h); for an episode older than that, the
   oldest in-window above-threshold row IS the window edge, NOT the true
   start. As the window slides each build cycle the returned
   ``episode_start`` advances → ``iso_first_seen`` / GUID / ``_identity``
   drift → ``data/first_seen.json`` lookup misses every cycle →
   ``first_seen`` reset to ``now`` and the disruption republishes as
   brand-new (FIFO retirement on age never fires either).

   Fix: persist the per-direction episode start to
   ``cache/stammstrecke/episode_starts.json``; on subsequent cycles the
   persisted value pins the rendered identity. Cleared per-direction when
   the trigger gate no longer fires (the episode ended). The first cycle
   after deploy still uses the window-edge value (no historical state to
   recover); subsequent cycles are stable.

2. MED — ``scripts/update_stammstrecke_status.py::_finalize_departed``
   double-finalise after crash. Reachable LIVE via
   ``update_stammstrecke_hbf`` which imports and calls this helper. If a
   prior tick wrote ``recently_finalised.json`` but crashed before
   ``_save_pending_trips``, the next load finalises the entry a SECOND
   time, producing a duplicate CSV row. Fix: skip entries that are
   already in ``recently_finalised``.

3. LOW — ``scripts/update_stammstrecke_status.py::_leg_departure_delay_minutes``
   midnight-rollover. Without an explicit ``rtDate``, an ``rtTime`` that
   crosses midnight relative to ``schedule`` (sched 23:55, rtTime 00:05)
   produced ≈ −1430 minutes. Fix ports the heuristic already in the
   sibling ``update_stammstrecke_hbf._departure_delay_minutes``.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scripts.update_stammstrecke_status import (
    _PendingTrip,
    _finalize_departed,
    _leg_departure_delay_minutes,
)
from src.feed import stammstrecke as sm
from src.utils.stats import (
    STAMMSTRECKE_HEADER,
    read_recent_stammstrecke_observations,
    stats_path,
)

VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---------------------------------------------------------------------------
# 1. HIGH — persisted episode-start state machine
# ---------------------------------------------------------------------------


def _write_obs_ledger(
    tmp_dir: Path,
    *,
    year: int,
    rows: list[tuple[datetime, str, float]],
) -> None:
    """Seed a stammstrecke CSV with (timestamp, direction, delay) rows."""
    ledger = stats_path("stammstrecke", year, base_dir=tmp_dir)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STAMMSTRECKE_HEADER)
        writer.writeheader()
        for ts, direction, delay in rows:
            writer.writerow(
                {
                    "timestamp": ts.isoformat(),
                    "weekday": "Sa",
                    "hour": ts.hour,
                    "direction": direction,
                    "delay_minutes": f"{delay:.1f}",
                }
            )


def _patched_compute(
    *, now: datetime, stats_dir: Path, starts_path: Path
) -> list[dict[str, Any]]:
    """Run ``compute_stammstrecke_events`` reading observations from *stats_dir*."""

    def reader(*, now: datetime, window: timedelta) -> list[Any]:
        return read_recent_stammstrecke_observations(
            now=now, window=window, stats_dir=stats_dir
        )

    with patch.object(sm, "read_recent_stammstrecke_observations", reader):
        return sm.compute_stammstrecke_events(
            now=now, episode_starts_path=starts_path
        )


def test_episode_identity_stays_stable_when_lookback_window_slides_past_start(
    tmp_path: Path,
) -> None:
    """The HIGH bug: 9 h of continuous above-threshold observations, 3 build
    cycles 1 h apart. Pre-fix the GUID/first_seen drifted with the sliding
    lookback. Post-fix they MUST be byte-identical across the three cycles.
    """
    base = datetime(2026, 5, 30, 2, 0, tzinfo=VIENNA_TZ)
    rows = [
        (base + timedelta(minutes=30 * i), "Praterstern", 10.0) for i in range(18)
    ]
    _write_obs_ledger(tmp_path, year=2026, rows=rows)
    starts_path = tmp_path / "episode_starts.json"

    cycles = []
    for hour in (9, 10, 11):
        cycles.append(
            _patched_compute(
                now=datetime(2026, 5, 30, hour, 0, tzinfo=VIENNA_TZ),
                stats_dir=tmp_path,
                starts_path=starts_path,
            )
        )

    assert all(len(c) == 1 for c in cycles), "trigger gate must fire each cycle"
    guids = [c[0]["guid"] for c in cycles]
    first_seens = [c[0]["first_seen"] for c in cycles]
    assert guids[0] == guids[1] == guids[2], f"GUID drifted: {guids}"
    assert first_seens[0] == first_seens[1] == first_seens[2], (
        f"first_seen drifted: {first_seens}"
    )

    # And the persisted ledger has captured the start so the NEXT process
    # restart still finds it.
    assert starts_path.exists()
    loaded = sm._load_episode_starts(starts_path)
    assert "Praterstern" in loaded


def test_persisted_start_is_cleared_when_episode_ends(tmp_path: Path) -> None:
    """Trigger gate stops firing → the persisted entry must be wiped so the
    NEXT episode (after EPISODE_GAP_TOLERANCE) gets a fresh first_seen."""
    # Two old above-threshold rows, then NOTHING in the feed window.
    base = datetime(2026, 5, 30, 2, 0, tzinfo=VIENNA_TZ)
    _write_obs_ledger(
        tmp_path,
        year=2026,
        rows=[
            (base, "Praterstern", 10.0),
            (base + timedelta(minutes=30), "Praterstern", 10.0),
        ],
    )
    starts_path = tmp_path / "episode_starts.json"

    # Cycle 1 at 03:00: both rows are in the 1h feed window, gate fires.
    cycle1 = _patched_compute(
        now=datetime(2026, 5, 30, 3, 0, tzinfo=VIENNA_TZ),
        stats_dir=tmp_path,
        starts_path=starts_path,
    )
    assert len(cycle1) == 1
    assert "Praterstern" in sm._load_episode_starts(starts_path)

    # Cycle 2 at 05:00: rows fall outside the 1h feed window, gate fails →
    # persisted entry must be cleared.
    cycle2 = _patched_compute(
        now=datetime(2026, 5, 30, 5, 0, tzinfo=VIENNA_TZ),
        stats_dir=tmp_path,
        starts_path=starts_path,
    )
    assert cycle2 == []
    assert "Praterstern" not in sm._load_episode_starts(starts_path)


def test_load_save_round_trip_preserves_timezone(tmp_path: Path) -> None:
    """A persisted aware datetime must round-trip without losing its offset."""
    path = tmp_path / "episode_starts.json"
    original = {"Praterstern": datetime(2026, 5, 28, 21, 0, tzinfo=VIENNA_TZ)}
    sm._save_episode_starts(path, original)
    loaded = sm._load_episode_starts(path)
    assert loaded["Praterstern"] == original["Praterstern"]
    assert loaded["Praterstern"].tzinfo is not None


def test_load_returns_empty_for_missing_or_corrupt_file(tmp_path: Path) -> None:
    """Missing / unparseable / wrong-shape input must yield ``{}`` (safe restart)."""
    missing = tmp_path / "nope.json"
    assert sm._load_episode_starts(missing) == {}
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert sm._load_episode_starts(corrupt) == {}
    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text('[1, 2, 3]')
    assert sm._load_episode_starts(wrong_shape) == {}


# ---------------------------------------------------------------------------
# 2. MED — _finalize_departed double-finalise guard
# ---------------------------------------------------------------------------


def test_finalize_departed_skips_entry_already_in_recently_finalised() -> None:
    """A previously-finalised entry must NOT be re-finalised (the crash-
    between-saves trap)."""
    now = datetime(2026, 5, 30, 10, 5, tzinfo=VIENNA_TZ)
    sched = datetime(2026, 5, 30, 10, 0, tzinfo=VIENNA_TZ)
    key = "Meidling|S2|2026-05-30T10:00:00+02:00"
    state = {key: _PendingTrip("Meidling", "S2", sched, 3.0, sched)}
    recently_finalised = {key: sched}  # ledger from a prior crashed tick

    result = _finalize_departed(
        state,
        direction="Meidling",
        now=now,
        recently_finalised=recently_finalised,
    )
    assert result == [], (
        "Entry was finalised twice — would produce a duplicate CSV row."
    )


def test_finalize_departed_still_finalises_fresh_trips() -> None:
    """The new guard must not over-suppress: a fresh trip still finalises."""
    now = datetime(2026, 5, 30, 10, 5, tzinfo=VIENNA_TZ)
    sched = datetime(2026, 5, 30, 10, 0, tzinfo=VIENNA_TZ)
    key = "Meidling|S2|2026-05-30T10:00:00+02:00"
    state = {key: _PendingTrip("Meidling", "S2", sched, 3.0, sched)}
    recently_finalised: dict[str, datetime] = {}

    result = _finalize_departed(
        state,
        direction="Meidling",
        now=now,
        recently_finalised=recently_finalised,
    )
    assert len(result) == 1
    # And the ledger was updated.
    assert key in recently_finalised


def test_finalize_departed_no_ledger_argument_is_unchanged() -> None:
    """When called without a ``recently_finalised`` arg, behaviour is unchanged."""
    now = datetime(2026, 5, 30, 10, 5, tzinfo=VIENNA_TZ)
    sched = datetime(2026, 5, 30, 10, 0, tzinfo=VIENNA_TZ)
    key = "Meidling|S2|2026-05-30T10:00:00+02:00"
    state = {key: _PendingTrip("Meidling", "S2", sched, 3.0, sched)}
    result = _finalize_departed(state, direction="Meidling", now=now)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# 3. LOW — _leg_departure_delay_minutes midnight rollover
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leg, expected_minutes",
    [
        # Without an explicit rtDate, an rtTime that crosses midnight is
        # the bug — pre-fix yielded ≈ -1430.0 (same-day arithmetic).
        (
            {"Origin": {"date": "2026-01-01", "time": "23:55", "rtTime": "00:05"}},
            10.0,
        ),
        # Earlier-than-scheduled by a few minutes, NO explicit rtDate:
        # legitimate negative delay, must NOT trigger the heuristic.
        (
            {"Origin": {"date": "2026-01-01", "time": "12:00", "rtTime": "11:55"}},
            -5.0,
        ),
        # Explicit rtDate matches sched_date → authoritative, even a -23h59
        # "delay" must be recorded verbatim (operator data, not a wrap).
        (
            {
                "Origin": {
                    "date": "2026-01-01",
                    "time": "23:55",
                    "rtDate": "2026-01-01",
                    "rtTime": "00:05",
                }
            },
            -23 * 60 - 50.0,
        ),
        # On-time same-day, sanity baseline.
        (
            {"Origin": {"date": "2026-01-01", "time": "12:00", "rtTime": "12:00"}},
            0.0,
        ),
    ],
)
def test_leg_departure_delay_minutes_midnight_heuristic(
    leg: dict[str, Any], expected_minutes: float
) -> None:
    actual = _leg_departure_delay_minutes(leg)
    assert actual == pytest.approx(expected_minutes)
