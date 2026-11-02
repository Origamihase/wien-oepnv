"""Teil 4 der Tests aus tests/scripts/test_update_stammstrecke_status.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import update_stammstrecke_status as script  # noqa: E402

from ._support import (
    VIENNA_TZ,
    _make_pending,
    _trip,
)


def test_identity_key_is_invariant_under_name_format_drift() -> None:
    """H1: ``"S 2"`` and ``"S2"`` MUST produce the same identity key.

    Without this, a VAO format flip between cron ticks would split
    the same physical train into two ledger entries — the exact
    double-counting the dedup pipeline is supposed to prevent.
    """
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    assert script._identity_key("Meidling", "S 2", sched) == script._identity_key(
        "Meidling", "S2", sched
    )
    assert script._identity_key("Meidling", " s 2 ", sched) == script._identity_key(
        "Meidling", "S2", sched
    )


def test_identity_key_strips_pipe_to_prevent_separator_collision() -> None:
    """M1: a literal pipe in ``name`` cannot collide with a real key."""
    sched = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    poisoned = script._identity_key("Meidling", "S 1|Floridsdorf", sched)
    legitimate = script._identity_key(
        "Floridsdorf",
        "S 1",
        sched,
    )
    assert poisoned != legitimate


def test_collect_sbahn_leg_observations_canonicalises_name_at_extraction() -> None:
    """H1: identical physical trains (spaced + no-space) produce ONE state entry."""
    state: dict[str, script._PendingTrip] = {}

    # Tick 1: VAO emits the spaced format.
    trip_spaced = _trip(
        leg_name="S 2", delay_minutes=4, leg_origin_time="08:00:00"
    )
    obs_spaced = script._collect_sbahn_leg_observations([trip_spaced])
    script._observe_legs(
        state,
        obs_spaced,
        direction="Meidling",
        now=datetime(2026, 5, 9, 7, 30, tzinfo=VIENNA_TZ),
    )

    # Tick 2: VAO emits the no-space format for the SAME physical train
    # (same scheduled departure).
    trip_compact = _trip(
        leg_name="S2", delay_minutes=10, leg_origin_time="08:00:00"
    )
    obs_compact = script._collect_sbahn_leg_observations([trip_compact])
    script._observe_legs(
        state,
        obs_compact,
        direction="Meidling",
        now=datetime(2026, 5, 9, 7, 55, tzinfo=VIENNA_TZ),
    )

    # Both observations land under the same identity key — exactly one
    # entry survives, and the latest reading wins.
    assert len(state) == 1
    only = next(iter(state.values()))
    assert only.latest_delay_minutes == 10.0


def test_load_pending_trips_normalises_legacy_spaced_names(
    tmp_path: Path,
) -> None:
    """A ledger written before the H1 fix used ``"S 2"`` (with space).

    On load, the parser canonicalises the name to ``"S2"`` AND rebuilds
    the dict key via ``_identity_key`` so a subsequent observation of the
    same train (using the no-space form VAO now emits) hits the existing
    entry instead of creating a duplicate.
    """
    sched_iso = "2026-05-09T08:00:00+02:00"
    payload = {
        "Meidling|S 2|2026-05-09T08:00:00+02:00": {
            "direction": "Meidling",
            "name": "S 2",
            "scheduled": sched_iso,
            "latest_delay_minutes": 3.0,
            "last_seen_at": "2026-05-09T07:30:00+02:00",
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = script._load_pending_trips(path)
    assert len(state) == 1
    only = next(iter(state.values()))
    assert only.name == "S2"
    # The key must be the canonical form, not the old spaced form.
    sched = datetime.fromisoformat(sched_iso)
    assert script._identity_key("Meidling", "S2", sched) in state
    assert "Meidling|S 2|2026-05-09T08:00:00+02:00" not in state


def test_finalize_departed_splits_by_scheduled_year_at_year_boundary() -> None:
    """M2: trips scheduled in different years group into separate finalisations.

    A cron tick at 00:05 on Jan 1 should produce one CSV row for each
    scheduled year if it happens to finalise trains on both sides of
    the boundary. The grouping itself is in ``main()`` — here we
    verify that the finaliser returns full records the caller can
    group on.
    """
    now = datetime(2027, 1, 1, 0, 5, tzinfo=VIENNA_TZ)
    last_2026 = _make_pending(
        direction="Meidling",
        scheduled=datetime(2026, 12, 31, 23, 55, tzinfo=VIENNA_TZ),
        latest_delay_minutes=2.0,
    )
    first_2027 = _make_pending(
        direction="Meidling",
        scheduled=datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    state = {"y2026": last_2026, "y2027": first_2027}
    finalised = script._finalize_departed(state, direction="Meidling", now=now)
    years = {trip.scheduled.year for trip in finalised}
    assert years == {2026, 2027}


def test_finalize_departed_registers_recently_finalised_keys() -> None:
    """M4: keys popped here land in *recently_finalised* with timestamp *now*."""
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    departed = _make_pending(
        direction="Meidling",
        name="S2",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=4.0,
    )
    key = script._identity_key(
        "Meidling",
        "S2",
        datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
    )
    state = {key: departed}
    recently_finalised: dict[str, datetime] = {}
    script._finalize_departed(
        state,
        direction="Meidling",
        now=now,
        recently_finalised=recently_finalised,
    )
    assert recently_finalised == {key: now}


def test_observe_legs_skips_keys_in_recently_finalised() -> None:
    """M4: a recently-finalised key cannot be re-inserted into pending state.

    Anomalous VAO re-emission (a finalised train re-appearing in the
    lookahead) is silently dropped instead of producing a second CSV
    row downstream.
    """
    sched = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    key = script._identity_key("Meidling", "S 2", sched)
    recently_finalised = {
        key: datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ),
    }
    state: dict[str, script._PendingTrip] = {}
    obs = script._SbahnLegObservation(
        name="S2",
        scheduled=sched,
        delay_minutes=7.0,
    )
    written = script._observe_legs(
        state,
        [obs],
        direction="Meidling",
        now=datetime(2026, 5, 9, 9, 5, tzinfo=VIENNA_TZ),
        recently_finalised=recently_finalised,
    )
    assert written == 0
    assert state == {}


def test_purge_finalised_entries_drops_only_old_keys() -> None:
    """M4 TTL: the recently-finalised ledger evicts entries older than cutoff."""
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    cutoff = now - timedelta(hours=6)
    finalised = {
        "fresh": now - timedelta(hours=2),
        "stale": now - timedelta(hours=12),
    }
    removed = script._purge_finalised_entries(finalised, cutoff=cutoff)
    assert removed == 1
    assert "fresh" in finalised
    assert "stale" not in finalised


def test_load_recently_finalised_returns_empty_when_missing(tmp_path: Path) -> None:
    """A missing file is fresh-start, not an error."""
    assert script._load_recently_finalised(tmp_path / "absent.json") == {}


def test_save_recently_finalised_round_trips_through_load(tmp_path: Path) -> None:
    """JSON persistence is lossless across save → load."""
    path = tmp_path / "finalised.json"
    state_in = {
        "Meidling|S2|2026-05-09T08:30:00+02:00": datetime(
            2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ
        )
    }
    assert script._save_recently_finalised(path, state_in) is True
    state_out = script._load_recently_finalised(path)
    assert set(state_out) == set(state_in)
    only_key = next(iter(state_in))
    assert state_out[only_key] == state_in[only_key]


def test_load_recently_finalised_recovers_from_corrupt_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt JSON in the companion ledger logs WARNING and falls back to empty."""
    path = tmp_path / "corrupt.json"
    path.write_text("{not even json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="update_stammstrecke_status"):
        result = script._load_recently_finalised(path)
    assert result == {}
    assert any("korrupt" in r.getMessage() for r in caplog.records)


def test_ledger_lock_is_a_context_manager_that_returns_to_caller(
    tmp_path: Path,
) -> None:
    """M3: the lock context manager yields exactly once and releases on exit.

    The actual mutual-exclusion is delegated to ``fcntl.flock`` on
    POSIX — covered by an integration smoke test, not unit assertion
    (file descriptors are notoriously hard to assert on across
    Python versions).
    """
    lock_path = tmp_path / "lock"
    entered = False
    with script._ledger_lock(lock_path):
        entered = True
        assert lock_path.exists(), "lock file is created on first acquire"
    assert entered is True


def test_collect_sbahn_leg_observations_preserves_negative_delays() -> None:
    """L3: VAO can report ``rtTime < time`` (early departure).

    Negative delays are legitimate signal — they bring the running
    mean down, which is what we want.  Locked here so a future
    sanitiser-rewrite doesn't accidentally clamp them at zero.
    """
    trip = {
        "LegList": {
            "Leg": [
                {
                    "type": "JNY",
                    "name": "S 1",
                    "category": "S",
                    "Origin": {
                        "name": "Wien Floridsdorf",
                        "date": "2026-05-09",
                        "time": "08:00:00",
                        # rtTime two minutes BEFORE time — early departure.
                        "rtTime": "07:58:00",
                    },
                    "Destination": {
                        "name": "Wien Meidling",
                        "date": "2026-05-09",
                        "time": "08:30:00",
                    },
                }
            ]
        }
    }
    observations = script._collect_sbahn_leg_observations([trip])
    assert len(observations) == 1
    assert observations[0].delay_minutes == -2.0


def test_main_suppresses_re_observation_of_finalised_train(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M4 end-to-end: a VAO re-emission of a finalised train doesn't
    produce a second CSV row.

    Anomalous-VAO scenario: the same physical S-Bahn appears in the
    lookahead of TICK A (gets observed and finalised because already
    departed) AND again in the lookahead of TICK B (because the VAO
    upstream forgot to update its train list). Without the
    recently-finalised gate, TICK B would re-observe and re-finalise
    the train → second CSV row. With the gate, the train is
    silently dropped on TICK B.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    csv_calls: list[dict[str, Any]] = []

    def fake_append(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        csv_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_append)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # The same train appears in every tick's lookahead — VAO bug
        # / lookahead-boundary anomaly. Origin time stays fixed.
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=10.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # Tick A: train has already departed at observation time; gets
    # observed AND finalised in the same tick.
    tick_a = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_a)
    assert script.main() == 0
    assert len(csv_calls) == 1

    # Tick B: anomalous re-emission. Without the suppression gate
    # this would produce a SECOND CSV row for the same physical
    # train.
    tick_b = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_b)
    assert script.main() == 0
    assert len(csv_calls) == 1, (
        "Recently-finalised gate must suppress the duplicate — "
        "without it, the train would be counted twice."
    )


# ---- Post-merge audit follow-up tests (HIGH/MEDIUM) ----------------------


def test_load_pending_trips_rebuilds_canonical_key_from_old_disk_key(
    tmp_path: Path,
) -> None:
    """HIGH 1: legacy disk key ``"Meidling|S 2|…"`` must be replaced by the
    canonical key ``"Meidling|S2|…"`` so that a subsequent observation of
    the same train (using the no-space form) hits the existing entry
    instead of inserting a duplicate.

    Before the fix, ``_load_pending_trips`` kept the raw disk key via
    ``state[str(key)] = trip``.  After the fix it calls ``_identity_key``
    so the stored key always matches the key any observer would compute.
    """
    sched_iso = "2026-05-09T08:00:00+02:00"
    payload = {
        "Meidling|S 2|2026-05-09T08:00:00+02:00": {
            "direction": "Meidling",
            "name": "S 2",
            "scheduled": sched_iso,
            "latest_delay_minutes": 3.0,
            "last_seen_at": "2026-05-09T07:30:00+02:00",
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = script._load_pending_trips(path)
    assert len(state) == 1
    sched = datetime.fromisoformat(sched_iso)
    expected_key = script._identity_key("Meidling", "S2", sched)
    assert expected_key in state, (
        f"Expected canonical key {expected_key!r} in state; got {list(state.keys())}"
    )
    # The old spaced key must NOT survive in the dict.
    assert "Meidling|S 2|2026-05-09T08:00:00+02:00" not in state


def test_canonical_line_name_handles_none_and_falsy_values() -> None:
    """MEDIUM 3: ``str(value or "")`` silently collapses falsy non-None
    values such as ``0`` (``str(0 or "")`` → ``""``).  The corrected
    form ``str(value) if value is not None else ""`` must return the
    string representation for any non-None input.
    """
    # None → empty string (no change from old behaviour).
    assert script._canonical_line_name(None) == ""
    # 0 is falsy but not None; old code would return ""; new must return "0".
    assert script._canonical_line_name(0) == "0"
    # False is falsy but not None.
    assert script._canonical_line_name(False) == "FALSE"
    # Empty string → empty string (unchanged).
    assert script._canonical_line_name("") == ""


def test_save_order_recently_finalised_before_pending_trips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MEDIUM 1: ``_save_recently_finalised`` must be called BEFORE
    ``_save_pending_trips`` in main().

    If the process crashes between the two writes the safer failure mode
    is:  recently_finalised durable + pending not yet removed  (train is
    re-observed but silently suppressed by the gate on the next tick)
    rather than:  pending removed + recently_finalised not yet written
    (train is gone from pending but the gate doesn't know → it can be
    double-finalised if VAO re-emits it).

    This test verifies the call order by recording which save function
    fires first.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    call_order: list[str] = []

    real_save_finalised = script._save_recently_finalised
    real_save_pending = script._save_pending_trips

    def spy_save_recently_finalised(path: Any, data: Any) -> bool:
        call_order.append("recently_finalised")
        return real_save_finalised(path, data)

    def spy_save_pending_trips(path: Any, state: Any) -> bool:
        call_order.append("pending_trips")
        return real_save_pending(path, state)

    monkeypatch.setattr(script, "_save_recently_finalised", spy_save_recently_finalised)
    monkeypatch.setattr(script, "_save_pending_trips", spy_save_pending_trips)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout, when
        return []

    monkeypatch.setattr(script, "_query_trips", fake_query)

    assert script.main() == 0
    assert call_order == ["recently_finalised", "pending_trips"], (
        f"Expected recently_finalised saved before pending_trips; got {call_order}"
    )


def test_format_drift_finalized_train_suppressed_on_respaced_reemission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """HIGH 1 + M4 integration: a train finalised as ``"S1"`` must NOT
    be double-finalised when the VAO re-emits it with the old
    ``"S 1"`` spacing.

    The recently-finalised gate stores canonical keys produced by
    ``_identity_key``.  ``_identity_key`` calls ``_canonical_line_name``
    which normalises ``"S 1"`` → ``"S1"``.  So the re-emitted ``"S 1"``
    form maps to the same key as the already-recorded ``"S1"`` entry and
    is silently suppressed.
    """
    state_path = tmp_path / "pending_trips.json"
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    csv_calls: list[dict[str, Any]] = []

    def fake_append(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        csv_calls.append({"direction": direction, "delay_minutes": delay_minutes})
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_append)

    # Tick A: VAO emits the train with canonical name "S1" (no space).
    # Train already past scheduled time → immediately finalised.
    def query_tick_a(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        return [
            _trip(
                leg_name="S1",
                delay_minutes=5.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    tick_a = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_a)
    monkeypatch.setattr(script, "_query_trips", query_tick_a)
    assert script.main() == 0
    assert len(csv_calls) == 1

    # Tick B: VAO re-emits the SAME train with the OLD spaced name "S 1".
    # The canonical gate must recognise it as already finalised.
    def query_tick_b(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=5.0,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:00:00",
            )
        ]

    tick_b = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick_b)
    monkeypatch.setattr(script, "_query_trips", query_tick_b)
    assert script.main() == 0
    assert len(csv_calls) == 1, (
        "Re-emission with spaced name 'S 1' must be suppressed because "
        "'S1' was already finalised in tick A."
    )
