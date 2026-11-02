"""Teil 3 der Tests aus tests/scripts/test_update_stammstrecke_status.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import update_stammstrecke_status as script  # noqa: E402

from ._support import (
    VIENNA_TZ,
    _make_pending,
    _trip,
)


# ---- Cancellation capture --------------------------------------------------


def test_collect_sbahn_leg_observations_captures_cancelled_legs() -> None:
    """Cancelled legs are surfaced as observations with ``cancelled=True``.

    Pre-2026-05-15 the collector silently dropped cancelled legs (they
    have no usable delay signal, so the delay-only pipeline could not
    consume them). The cancellation-tracking rework routes them through
    the same pending-trip ledger so the finalise pass can split them
    out into ``data/stats/ausfaelle_<YYYY>.csv``.
    """

    trips = [
        _trip(leg_name="S 1", delay_minutes=4, leg_origin_time="08:00:00"),
        _trip(leg_name="S 2", cancelled=True, leg_origin_time="08:15:00"),
    ]
    observations = script._collect_sbahn_leg_observations(trips)
    assert len(observations) == 2
    by_name = {obs.name: obs for obs in observations}
    assert by_name["S1"].cancelled is False
    assert by_name["S1"].delay_minutes == 4.0
    assert by_name["S2"].cancelled is True
    # Cancelled observations carry a placeholder ``0.0`` so the dataclass
    # parses; the finalise pass MUST NOT fold this value into a delay
    # mean. Pinning the value here protects against a future refactor
    # surfacing a non-zero placeholder into the delay ledger.
    assert by_name["S2"].delay_minutes == 0.0


def test_collect_sbahn_leg_observations_captures_origin_cancelled() -> None:
    """The ``Origin.cancelled`` flag also triggers a cancellation observation.

    VAO splits the cancellation flag across two possible locations:
    the leg itself (``leg.cancelled``) and the leg's origin
    (``leg.Origin.cancelled``). Both shapes are observed in
    production payloads; both must reach the cancellation ledger.
    """

    trip = _trip(leg_name="S 1", delay_minutes=4)
    # Surgically mark only the Origin as cancelled (clear the leg-level
    # flag the helper sets when ``cancelled=True``).
    trip["LegList"]["Leg"][0]["Origin"]["cancelled"] = True
    observations = script._collect_sbahn_leg_observations([trip])
    assert len(observations) == 1
    assert observations[0].cancelled is True


def test_leg_is_cancelled_pins_strict_boolean_contract() -> None:
    """The cancellation flag MUST be a strict Python ``True``.

    Refusing fuzzy spellings (``"true"`` / ``1``) defends against a
    poisoned cache file forging cancellations to flood the ledger.
    """

    assert script._leg_is_cancelled({"cancelled": True}) is True
    assert script._leg_is_cancelled({"Origin": {"cancelled": True}}) is True
    # Fuzzy strings / truthy numbers MUST NOT count as cancellations.
    assert script._leg_is_cancelled({"cancelled": "true"}) is False
    assert script._leg_is_cancelled({"cancelled": 1}) is False
    assert script._leg_is_cancelled({}) is False


def test_pending_trip_json_round_trip_preserves_cancelled() -> None:
    """The pending-trip ledger round-trips the ``cancelled`` flag.

    A cron tick that observes a cancellation, persists the ledger,
    crashes, and resumes on the next tick MUST re-load the
    cancellation flag — otherwise the train would silently demote to
    a "regular delay observation" and end up in the wrong CSV at
    finalisation time.
    """

    trip = script._PendingTrip(
        direction="Meidling",
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=0.0,
        last_seen_at=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        cancelled=True,
    )
    payload = script._trip_to_json(trip)
    assert payload["cancelled"] is True
    parsed = script._trip_from_json(payload)
    assert parsed is not None
    assert parsed.cancelled is True


def test_pending_trip_json_legacy_entry_loads_as_not_cancelled() -> None:
    """Backwards-compat: a ledger entry without the ``cancelled`` key
    loads as a regular (non-cancelled) observation.

    The repo carries cached ``pending_trips.json`` files written before
    the cancellation-tracking schema; on first start after the upgrade
    the loader MUST accept those entries verbatim rather than discard
    them. Discarding would force every in-flight train back to "fresh
    observation" status, breaking the latest-wins delay-reading
    contract.
    """

    legacy_payload = {
        "direction": "Meidling",
        "name": "S1",
        "scheduled": "2026-05-09T08:30:00+02:00",
        "latest_delay_minutes": 3.5,
        "last_seen_at": "2026-05-09T08:00:00+02:00",
        # NO ``cancelled`` key — pre-2026-05-15 ledger shape.
    }
    parsed = script._trip_from_json(legacy_payload)
    assert parsed is not None
    assert parsed.cancelled is False
    assert parsed.latest_delay_minutes == 3.5


def test_observe_legs_inserts_new_trip_with_now_as_last_seen() -> None:
    """A fresh observation must land in state with ``last_seen_at = now``."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    state: dict[str, script._PendingTrip] = {}
    obs = script._SbahnLegObservation(
        name="S 1",
        scheduled=datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ),
        delay_minutes=4.0,
    )
    written = script._observe_legs(
        state, [obs], direction="Meidling", now=now
    )
    assert written == 1
    assert len(state) == 1
    only_entry = next(iter(state.values()))
    assert only_entry.direction == "Meidling"
    assert only_entry.name == "S 1"
    assert only_entry.latest_delay_minutes == 4.0
    assert only_entry.last_seen_at == now


def test_observe_legs_overwrites_existing_with_latest_delay() -> None:
    """The same train re-observed must yield the *latest* delay value.

    Mirrors the user's scenario verbatim: at T=40min the train shows
    5 min late; at T=10min the train shows 15 min late. We expect
    the 15-min reading to survive in state.
    """
    state: dict[str, script._PendingTrip] = {}
    scheduled = datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ)

    # First observation 40 min before departure: 5 min delay.
    earlier = datetime(2026, 5, 9, 8, 5, tzinfo=VIENNA_TZ)
    script._observe_legs(
        state,
        [
            script._SbahnLegObservation(
                name="S 1", scheduled=scheduled, delay_minutes=5.0
            )
        ],
        direction="Meidling",
        now=earlier,
    )

    # Second observation 10 min before departure: 15 min delay.
    later = datetime(2026, 5, 9, 8, 35, tzinfo=VIENNA_TZ)
    script._observe_legs(
        state,
        [
            script._SbahnLegObservation(
                name="S 1", scheduled=scheduled, delay_minutes=15.0
            )
        ],
        direction="Meidling",
        now=later,
    )

    assert len(state) == 1
    only_entry = next(iter(state.values()))
    assert only_entry.latest_delay_minutes == 15.0
    assert only_entry.last_seen_at == later


def test_finalize_departed_returns_only_past_scheduled_trips() -> None:
    """Trips with ``scheduled > now`` stay in state; ``scheduled <= now`` finalise."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    past = _make_pending(
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    future = _make_pending(
        name="S80",
        scheduled=datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=2.0,
    )
    state = {"past-key": past, "future-key": future}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    # Finaliser returns full ``_PendingTrip`` records so the caller
    # can scope the CSV row timestamp to the actual scheduled
    # departure (M2 — cross-year boundary).
    assert [trip.latest_delay_minutes for trip in finalised] == [5.0]
    # Only the past entry was popped.
    assert "past-key" not in state
    assert "future-key" in state


def test_finalize_departed_filters_by_direction() -> None:
    """A departed train for the *other* direction is left untouched."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    meidling_trip = _make_pending(
        direction="Meidling",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=5.0,
    )
    floridsdorf_trip = _make_pending(
        direction="Floridsdorf",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=10.0,
    )
    state = {"m": meidling_trip, "f": floridsdorf_trip}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    assert [trip.latest_delay_minutes for trip in finalised] == [5.0]
    assert "m" not in state
    assert "f" in state


def test_finalize_departed_returns_delays_in_scheduled_order() -> None:
    """Order of returned trips must follow scheduled time ascending.

    A deterministic order keeps the resulting CSV row's mean
    reproducible across runs and platforms.
    """
    now = datetime(2026, 5, 9, 9, 0, tzinfo=VIENNA_TZ)
    later = _make_pending(
        name="S80",
        scheduled=datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ),
        latest_delay_minutes=10.0,
    )
    earlier = _make_pending(
        name="S1",
        scheduled=datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ),
        latest_delay_minutes=4.0,
    )
    # Insert in reverse-scheduled order so dict iteration order would
    # produce the wrong sequence without explicit sort.
    state = {"later-key": later, "earlier-key": earlier}
    finalised = script._finalize_departed(
        state, direction="Meidling", now=now
    )
    assert [trip.latest_delay_minutes for trip in finalised] == [4.0, 10.0]


def test_finalize_departed_empty_state_returns_empty_list() -> None:
    """Empty state must yield an empty finalisation list, never raise."""
    now = datetime(2026, 5, 9, 8, 30, tzinfo=VIENNA_TZ)
    state: dict[str, script._PendingTrip] = {}
    assert script._finalize_departed(state, direction="Meidling", now=now) == []


# ---- End-to-end: observe across two ticks, finalise the latest reading ----


def test_main_end_to_end_finalises_with_latest_delay_across_two_ticks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The user's exact scenario, end-to-end.

    Cron tick 1 (08:00): the lookahead reports train ``S 1`` scheduled
    at 08:45 with a 5-min delay. The train has not departed yet, so
    no CSV row is written; the ledger keeps the 5-min reading.

    Cron tick 2 (08:35): the lookahead now reports the same train
    with a 15-min delay (the delay grew as the actual departure
    approached). The train is still in the future (scheduled 08:45 >
    now 08:35), so the ledger overwrites the entry to 15 min. Still
    no CSV row.

    Cron tick 3 (08:50): the train has departed. The ledger entry
    finalises; a CSV row is written with the *latest* delay of 15
    min — NOT the stale 5-min reading from tick 1.
    """
    # Redirect every persistence target into tmp_path so the test
    # never touches the developer's working copy.
    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    # Capture every ``append_stammstrecke_row`` call instead of writing
    # an actual CSV file — the test asserts on the captured records.
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

    # The /trip endpoint stays mocked; we paint the leg's realtime
    # delta differently across the three ticks.
    delay_by_tick: dict[datetime, float] = {}

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        # Only the Meidling direction's lookahead carries the test
        # train; the opposite direction returns no trips so finalisation
        # is exercised as direction-scoped.
        if direction.target_label != "Meidling":
            return []
        # The train has departed; the API no longer returns it.
        if when >= datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=delay_by_tick.get(when, 0.0),
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # --- Tick 1: 08:00, observed delay = 5 min ----------------------------
    tick1 = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick1)
    delay_by_tick[tick1] = 5.0
    assert script.main() == 0
    assert csv_calls == [], "Tick 1 must not write a CSV row (train still in future)"
    state_after_tick1 = script._load_pending_trips(state_path)
    assert len(state_after_tick1) == 1
    only = next(iter(state_after_tick1.values()))
    assert only.latest_delay_minutes == 5.0

    # --- Tick 2: 08:35, observed delay = 15 min ---------------------------
    tick2 = datetime(2026, 5, 9, 8, 35, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick2)
    delay_by_tick[tick2] = 15.0
    assert script.main() == 0
    assert csv_calls == [], "Tick 2 must not write a CSV row (train still in future)"
    state_after_tick2 = script._load_pending_trips(state_path)
    assert len(state_after_tick2) == 1
    only = next(iter(state_after_tick2.values()))
    assert only.latest_delay_minutes == 15.0, (
        "Latest observation must overwrite the earlier 5-min reading"
    )

    # --- Tick 3: 08:50, train has departed; finalise with 15 -------------
    tick3 = datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick3)
    assert script.main() == 0
    assert len(csv_calls) == 1
    persisted = csv_calls[0]
    assert persisted["direction"] == "Meidling"
    assert persisted["delay_minutes"] == 15.0, (
        "Finalisation must use the latest (15 min) reading, not the "
        "stale 5-min one"
    )
    # M2: the CSV row's timestamp anchors to the train's scheduled
    # departure (08:45), not the cron tick wall clock (08:50) — so
    # a cron tick straddling a year boundary still writes the row
    # into the correct calendar year of the actual departure.
    assert persisted["timestamp"] == datetime(
        2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ
    )
    # Ledger emptied after finalisation.
    state_after_tick3 = script._load_pending_trips(state_path)
    assert state_after_tick3 == {}
    # M4: the recently-finalised ledger holds exactly the train we
    # just committed so a stray VAO re-emission cannot resurrect it.
    finalised_after_tick3 = script._load_recently_finalised(finalised_path)
    assert len(finalised_after_tick3) == 1


def test_main_routes_cancelled_trains_to_ausfaelle_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cancelled S-Bahn train is finalised into the cancellation CSV.

    End-to-end: a cron tick observes a cancelled train scheduled in
    the future. The pending-trip ledger carries the ``cancelled=True``
    flag across ticks. A later tick (post-departure-time) finalises
    the train; the finalise pass routes it to ``append_ausfall_row``
    instead of ``append_stammstrecke_row`` so the delay CSV is NOT
    polluted with a placeholder zero-delay row and the cancellation
    CSV captures the discrete event.
    """

    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    stammstrecke_calls: list[dict[str, Any]] = []
    ausfaelle_calls: list[dict[str, Any]] = []

    def fake_stammstrecke(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        stammstrecke_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    def fake_ausfall(
        *,
        timestamp: datetime,
        direction: str,
        line: str,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        ausfaelle_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "line": line,
            }
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_stammstrecke)
    monkeypatch.setattr(script, "append_ausfall_row", fake_ausfall)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # Train is observed up to its scheduled departure; after that
        # VAO no longer returns it.
        if when >= datetime(2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                cancelled=True,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            )
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # --- Tick 1: observe the cancellation; not yet finalised ---------------
    tick1 = datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick1)
    assert script.main() == 0
    assert stammstrecke_calls == []
    assert ausfaelle_calls == []
    state_after_tick1 = script._load_pending_trips(state_path)
    assert len(state_after_tick1) == 1
    only = next(iter(state_after_tick1.values()))
    assert only.cancelled is True

    # --- Tick 2: post-departure-time; finalise to ausfaelle CSV ------------
    tick2 = datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: tick2)
    assert script.main() == 0
    # The cancelled train MUST land in the ausfaelle ledger, NOT the
    # delay ledger (which would otherwise be polluted with a placeholder
    # ``delay_minutes=0.0`` row).
    assert stammstrecke_calls == []
    assert len(ausfaelle_calls) == 1
    persisted = ausfaelle_calls[0]
    assert persisted["direction"] == "Meidling"
    assert persisted["line"] == "S1"
    assert persisted["timestamp"] == datetime(
        2026, 5, 9, 8, 45, tzinfo=VIENNA_TZ
    )


def test_main_mixed_finalisation_keeps_delay_and_cancellation_distinct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the same tick finalises both a delayed train and a
    cancelled train, each lands in its own CSV ledger.

    Anti-regression: a naive "always write one stammstrecke row per
    direction per tick (aggregating ALL trains)" would have folded
    the cancelled train's placeholder ``0.0`` into the delay mean —
    biasing it downward. The split MUST happen before the mean
    computation.
    """

    state_path = tmp_path / "pending_trips.json"
    monkeypatch.setattr(script, "PENDING_TRIPS_PATH", state_path)
    finalised_path = tmp_path / "recently_finalised.json"
    monkeypatch.setattr(script, "RECENTLY_FINALISED_PATH", finalised_path)
    monkeypatch.setattr(
        script, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending_trips.lock"
    )

    stammstrecke_calls: list[dict[str, Any]] = []
    ausfaelle_calls: list[dict[str, Any]] = []

    def fake_stammstrecke(
        *,
        timestamp: datetime,
        direction: str,
        delay_minutes: float,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        stammstrecke_calls.append(
            {
                "timestamp": timestamp,
                "direction": direction,
                "delay_minutes": delay_minutes,
            }
        )
        return True

    def fake_ausfall(
        *,
        timestamp: datetime,
        direction: str,
        line: str,
        stats_dir: Path | None = None,
    ) -> bool:
        del stats_dir
        ausfaelle_calls.append(
            {"timestamp": timestamp, "direction": direction, "line": line}
        )
        return True

    monkeypatch.setattr(script, "append_stammstrecke_row", fake_stammstrecke)
    monkeypatch.setattr(script, "append_ausfall_row", fake_ausfall)

    def fake_query(
        session: Any, direction: Any, *, when: datetime, timeout: int = 0
    ) -> list[dict[str, Any]]:
        del session, timeout
        if direction.target_label != "Meidling":
            return []
        # Past-departure tick → empty response (already-departed
        # trains no longer surface in the lookahead).
        if when >= datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ):
            return []
        return [
            _trip(
                leg_name="S 1",
                delay_minutes=6,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:30:00",
            ),
            _trip(
                leg_name="S 2",
                cancelled=True,
                leg_origin_date="2026-05-09",
                leg_origin_time="08:45:00",
            ),
        ]

    monkeypatch.setattr(script, "_query_trips", fake_query)

    # Tick 1 → both trains land in the pending ledger.
    monkeypatch.setattr(
        script, "_now_vienna", lambda: datetime(2026, 5, 9, 8, 0, tzinfo=VIENNA_TZ)
    )
    assert script.main() == 0

    # Tick 2 → both scheduled times are in the past; finalise.
    monkeypatch.setattr(
        script,
        "_now_vienna",
        lambda: datetime(2026, 5, 9, 8, 50, tzinfo=VIENNA_TZ),
    )
    assert script.main() == 0
    # Delay CSV holds exactly one row for the delayed S1 — the
    # cancelled S2's placeholder ``0.0`` MUST NOT contaminate the mean.
    assert len(stammstrecke_calls) == 1
    assert stammstrecke_calls[0]["delay_minutes"] == 6.0
    # Cancellation CSV holds exactly one row for the cancelled S2.
    assert len(ausfaelle_calls) == 1
    assert ausfaelle_calls[0]["line"] == "S2"


# ---- Hardening: H1 / M1 / M2 / M3 / M4 / L3 -------------------------------


def test_canonical_line_name_collapses_whitespace_and_strips_pipe() -> None:
    """H1 + M1 normalisation: VAO format drift + separator injection."""
    assert script._canonical_line_name("S 2") == "S2"
    assert script._canonical_line_name("S2") == "S2"
    assert script._canonical_line_name("  s 80  ") == "S80"
    # M1: a pipe inside ``name`` cannot escape into the identity key.
    assert script._canonical_line_name("S 1|fake") == "S1FAKE"
    assert script._canonical_line_name("") == ""
    assert script._canonical_line_name("   ") == ""
