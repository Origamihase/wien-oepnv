"""Tests for ``scripts/update_stammstrecke_status.py``.

The pyhafas client is mocked end-to-end so the test suite never reaches
the live ÖBB HAFAS endpoint. Each test exercises a single, isolated
branch of the script's decision tree: import-time failure, transport
error, circuit-breaker open, median-below-threshold, median-above-
threshold, no S-Bahn legs found — for each of the **two** Stammstrecke
directions independently.
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_status as script  # noqa: E402
from src.utils.circuit_breaker import CircuitBreaker  # noqa: E402


VIENNA_TZ = ZoneInfo("Europe/Vienna")


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect cache writes to a per-test directory."""

    out = tmp_path / "cache" / "stammstrecke" / "events.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", out)
    yield out


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the module-level breaker so tests don't share state.

    Uses the production breaker config (10 / 3600 s) so the threshold-
    based behavioural tests reflect what runs in CI.
    """

    monkeypatch.setattr(
        script,
        "_BREAKER",
        CircuitBreaker(
            "stammstrecke-hafas-test",
            failure_threshold=script.BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=script.BREAKER_RECOVERY_TIMEOUT,
        ),
    )
    yield


@pytest.fixture(autouse=True)
def _stable_now(monkeypatch: pytest.MonkeyPatch) -> Iterator[datetime]:
    """Pin ``_now_vienna`` so tests get deterministic timestamps."""

    pinned = datetime(2026, 5, 9, 8, 30, 0, tzinfo=VIENNA_TZ)
    monkeypatch.setattr(script, "_now_vienna", lambda: pinned)
    yield pinned


# ---- Helpers ---------------------------------------------------------------


def _leg(*, name: str, delay_minutes: float | None, cancelled: bool = False) -> Any:
    """Return a duck-typed leg mock matching pyhafas FPTF Leg."""

    delay = timedelta(minutes=delay_minutes) if delay_minutes is not None else None
    return SimpleNamespace(name=name, departure_delay=delay, cancelled=cancelled)


def _journey(*, legs: list[Any]) -> Any:
    return SimpleNamespace(legs=legs)


def _read_output(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert isinstance(payload, list)
    return payload


def _make_directional_client(
    floridsdorf_to_meidling: list[Any] | Exception,
    meidling_to_floridsdorf: list[Any] | Exception,
) -> Any:
    """Build a fake HafasClient that returns per-direction mock data.

    Each direction can also be an :class:`Exception` instance to
    simulate a per-direction transport error without affecting the
    other direction's outcome.
    """

    def journeys(**kwargs: Any) -> list[Any]:
        origin = kwargs.get("origin")
        destination = kwargs.get("destination")
        if (
            origin == script.FLORIDSDORF_STATION_ID
            and destination == script.MEIDLING_STATION_ID
        ):
            payload = floridsdorf_to_meidling
        elif (
            origin == script.MEIDLING_STATION_ID
            and destination == script.FLORIDSDORF_STATION_ID
        ):
            payload = meidling_to_floridsdorf
        else:  # pragma: no cover - defensive: unexpected origin/dest pair
            raise AssertionError(
                f"Unexpected origin/destination pair: {origin!r} → {destination!r}"
            )
        if isinstance(payload, Exception):
            raise payload
        return payload

    return SimpleNamespace(profile=SimpleNamespace(), journeys=journeys)


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(script, "_build_client", lambda: client)


# ---- Helper-level unit tests -----------------------------------------------


def test_is_sbahn_leg_matches_canonical_labels() -> None:
    assert script._is_sbahn_leg(_leg(name="S 1", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="S 7", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="S 80", delay_minutes=0))
    assert script._is_sbahn_leg(_leg(name="s 2", delay_minutes=0))


def test_is_sbahn_leg_rejects_non_sbahn() -> None:
    assert not script._is_sbahn_leg(_leg(name="REX 7", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="IC 533", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="Railjet 162", delay_minutes=0))
    assert not script._is_sbahn_leg(_leg(name="", delay_minutes=0))


def test_is_sbahn_leg_handles_missing_or_non_string_name() -> None:
    bad = SimpleNamespace(departure_delay=timedelta(minutes=5), cancelled=False)
    assert not script._is_sbahn_leg(bad)
    bad_int = SimpleNamespace(name=42, departure_delay=None, cancelled=False)
    assert not script._is_sbahn_leg(bad_int)


def test_collect_delays_includes_only_sbahn_with_delay() -> None:
    journeys = [
        _journey(legs=[_leg(name="S 1", delay_minutes=4)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=10)]),
        # Non-S-Bahn — must be ignored.
        _journey(legs=[_leg(name="REX 7", delay_minutes=20)]),
        # S-Bahn but cancelled — ignored (no signal).
        _journey(legs=[_leg(name="S 3", delay_minutes=15, cancelled=True)]),
        # S-Bahn but no delay value — ignored.
        _journey(legs=[_leg(name="S 80", delay_minutes=None)]),
    ]
    delays = script._collect_sbahn_delays_minutes(journeys)
    assert delays == [4.0, 10.0]


def test_collect_delays_handles_missing_legs_attribute() -> None:
    """A misbehaving HAFAS peer might emit journeys without a ``legs`` attr."""

    journeys = [SimpleNamespace()]  # no legs attribute at all
    assert script._collect_sbahn_delays_minutes(journeys) == []


def test_collect_delays_handles_legs_set_to_none() -> None:
    journeys = [SimpleNamespace(legs=None)]
    assert script._collect_sbahn_delays_minutes(journeys) == []


def test_format_minutes_strips_trailing_zero() -> None:
    assert script._format_minutes(12.0) == "12"
    assert script._format_minutes(12.5) == "12.5"
    assert script._format_minutes(11.04) == "11"  # rounds to 11.0
    assert script._format_minutes(11.05) == "11.1"


def test_directions_table_covers_both_targets() -> None:
    """Sanity: DIRECTIONS contains exactly the two Stammstrecke directions."""

    targets = {d.target_label for d in script.DIRECTIONS}
    prefixes = {d.identity_prefix for d in script.DIRECTIONS}
    assert targets == {"Meidling", "Floridsdorf"}
    assert prefixes == {
        "stammstrecke_delay_meidling",
        "stammstrecke_delay_floridsdorf",
    }


def test_breaker_config_aligns_with_10_per_hour_budget() -> None:
    """Pin the rate-limit-aligned breaker constants documented in the script."""

    assert script.BREAKER_FAILURE_THRESHOLD == 10
    assert script.BREAKER_RECOVERY_TIMEOUT == 3600.0


# ---- _build_event tests ----------------------------------------------------


def test_build_event_for_meidling_direction(_stable_now: datetime) -> None:
    direction = next(d for d in script.DIRECTIONS if d.target_label == "Meidling")
    event = script._build_event(
        direction=direction,
        median_delay_minutes=12.5,
        pub_date=_stable_now,
    )
    required_keys = {
        "source",
        "category",
        "title",
        "description",
        "link",
        "guid",
        "pubDate",
        "starts_at",
    }
    assert required_keys.issubset(event.keys())
    assert event["title"] == "S-Bahn Stammstrecke Verspätungen"
    assert event["source"] == "ÖBB"
    assert event["category"] == "Störung"
    assert (
        event["description"]
        == "Durchschnittliche Verspätung von 12.5 Minuten in Richtung Meidling"
    )
    # Timestamps must be ISO-8601 strings with offset (Europe/Vienna).
    assert event["pubDate"] == _stable_now.isoformat()
    assert event["starts_at"] == _stable_now.isoformat()
    assert event["pubDate"].endswith(("+02:00", "+01:00"))
    assert event["ends_at"] is None
    assert event["_identity"].startswith("stammstrecke_delay_meidling|")


def test_build_event_for_floridsdorf_direction(_stable_now: datetime) -> None:
    direction = next(d for d in script.DIRECTIONS if d.target_label == "Floridsdorf")
    event = script._build_event(
        direction=direction,
        median_delay_minutes=15.0,
        pub_date=_stable_now,
    )
    assert event["title"] == "S-Bahn Stammstrecke Verspätungen"
    # 15.0 must render as "15" (no trailing zero) per ``_format_minutes``.
    assert (
        event["description"]
        == "Durchschnittliche Verspätung von 15 Minuten in Richtung Floridsdorf"
    )
    assert event["_identity"].startswith("stammstrecke_delay_floridsdorf|")


def test_build_event_guids_differ_per_direction(_stable_now: datetime) -> None:
    """Each direction must produce a distinct GUID for the same timestamp.

    The user-facing contract is that feed readers treat the two
    direction events as separate notifications. That requires distinct
    ``guid`` values even when the underlying timestamps coincide.
    """

    meidling = next(d for d in script.DIRECTIONS if d.target_label == "Meidling")
    floridsdorf = next(d for d in script.DIRECTIONS if d.target_label == "Floridsdorf")
    event_a = script._build_event(
        direction=meidling, median_delay_minutes=12.5, pub_date=_stable_now
    )
    event_b = script._build_event(
        direction=floridsdorf, median_delay_minutes=12.5, pub_date=_stable_now
    )
    assert event_a["guid"] != event_b["guid"]
    assert event_a["_identity"] != event_b["_identity"]


def test_build_event_guid_is_deterministic(_stable_now: datetime) -> None:
    direction = script.DIRECTIONS[0]
    event_a = script._build_event(
        direction=direction, median_delay_minutes=12.5, pub_date=_stable_now
    )
    event_b = script._build_event(
        direction=direction, median_delay_minutes=12.5, pub_date=_stable_now
    )
    assert event_a["guid"] == event_b["guid"]


# ---- main() integration tests ---------------------------------------------


def test_main_writes_two_events_when_both_directions_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Both directions exceed the threshold → one event per direction."""

    fwd = [
        _journey(legs=[_leg(name="S 1", delay_minutes=11)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=10)]),
        _journey(legs=[_leg(name="S 3", delay_minutes=12)]),
    ]
    bwd = [
        _journey(legs=[_leg(name="S 7", delay_minutes=14)]),
        _journey(legs=[_leg(name="S 80", delay_minutes=15)]),
        _journey(legs=[_leg(name="S 1", delay_minutes=13)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    descriptions = {event["description"] for event in payload}
    assert (
        "Durchschnittliche Verspätung von 11 Minuten in Richtung Meidling"
        in descriptions
    )
    assert (
        "Durchschnittliche Verspätung von 14 Minuten in Richtung Floridsdorf"
        in descriptions
    )
    # Both events must have distinct GUIDs.
    guids = {event["guid"] for event in payload}
    assert len(guids) == 2


def test_main_writes_only_meidling_event_when_only_forward_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [
        _journey(legs=[_leg(name="S 1", delay_minutes=14)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=13)]),
    ]
    bwd = [
        _journey(legs=[_leg(name="S 7", delay_minutes=2)]),
        _journey(legs=[_leg(name="S 80", delay_minutes=4)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Meidling" in payload[0]["description"]
    assert payload[0]["_identity"].startswith("stammstrecke_delay_meidling|")


def test_main_writes_only_floridsdorf_event_when_only_backward_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [_journey(legs=[_leg(name="S 1", delay_minutes=2)])]
    bwd = [
        _journey(legs=[_leg(name="S 7", delay_minutes=11)]),
        _journey(legs=[_leg(name="S 80", delay_minutes=12)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Floridsdorf" in payload[0]["description"]
    assert payload[0]["_identity"].startswith("stammstrecke_delay_floridsdorf|")


def test_main_writes_empty_when_both_directions_below_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fwd = [_journey(legs=[_leg(name="S 1", delay_minutes=2)])]
    bwd = [_journey(legs=[_leg(name="S 7", delay_minutes=4)])]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_median_equals_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Median exactly equal to threshold must NOT trigger the event."""

    nine = [
        _journey(legs=[_leg(name="S 1", delay_minutes=9)]),
        _journey(legs=[_leg(name="S 2", delay_minutes=9)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(nine, nine))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_no_sbahn_legs(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Only non-S-Bahn legs in either direction → empty cache, exit 0."""

    fwd = [_journey(legs=[_leg(name="REX 7", delay_minutes=20)])]
    bwd = [_journey(legs=[_leg(name="IC 533", delay_minutes=15)])]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_partial_failure_keeps_other_direction_event(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Direction 1 raises but direction 2 succeeds with a high median.

    The event for the surviving direction must still be persisted —
    discarding direction 2's data because direction 1 had a transient
    error would degrade the feed for no reason.
    """

    fwd_error = RuntimeError("transient connection reset")
    bwd_high = [
        _journey(legs=[_leg(name="S 7", delay_minutes=12)]),
        _journey(legs=[_leg(name="S 80", delay_minutes=14)]),
    ]
    _patch_client(monkeypatch, _make_directional_client(fwd_error, bwd_high))

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    # Exit code 0 because at least one direction succeeded.
    assert script.main() == 0

    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    assert "in Richtung Floridsdorf" in payload[0]["description"]
    assert any("Richtung Meidling" in r.getMessage() for r in caplog.records)


def test_main_returns_1_when_all_directions_fail(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    err1 = RuntimeError("connection reset 1")
    err2 = RuntimeError("connection reset 2")
    _patch_client(monkeypatch, _make_directional_client(err1, err2))

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_on_import_error(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def raise_import_error() -> Any:
        raise ImportError("OEBBProfile not available in this pyhafas release")

    monkeypatch.setattr(script, "_build_client", raise_import_error)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []
    assert any("nicht verfügbar" in r.getMessage() for r in caplog.records)


def test_main_short_circuits_when_breaker_is_open(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-tripping the breaker forces main() onto its short-circuit path.

    When the breaker is OPEN, the first direction's call raises
    ``CircuitBreakerOpen``. main() must break out of the loop without
    invoking the upstream for the second direction either.
    """

    breaker = CircuitBreaker(
        "stammstrecke-hafas-pretrip",
        failure_threshold=2,
        recovery_timeout=600.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    monkeypatch.setattr(script, "_BREAKER", breaker)

    upstream_calls: list[tuple[str | None, str | None]] = []

    def must_not_be_called(**kwargs: Any) -> list[Any]:
        upstream_calls.append((kwargs.get("origin"), kwargs.get("destination")))
        return []

    fake_client = SimpleNamespace(
        profile=SimpleNamespace(), journeys=must_not_be_called
    )
    _patch_client(monkeypatch, fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    # Exit 0 — breaker-open is intentional short-circuiting, not a failure.
    assert script.main() == 0
    assert _read_output(_isolated_output) == []
    assert upstream_calls == []
    assert any("breaker" in r.getMessage().lower() for r in caplog.records)


def test_main_handles_non_list_payload(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both directions return a non-list value → both fail, exit 1."""

    def journeys(**kwargs: Any) -> Any:
        return "not-a-list"

    fake_client = SimpleNamespace(profile=SimpleNamespace(), journeys=journeys)
    _patch_client(monkeypatch, fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_main_emits_iso8601_with_vienna_offset(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_output: Path,
    _stable_now: datetime,
) -> None:
    """Verify the timezone contract: pubDate is Europe/Vienna, ISO 8601."""

    fwd = [_journey(legs=[_leg(name="S 1", delay_minutes=12)])]
    bwd = [_journey(legs=[_leg(name="S 7", delay_minutes=12)])]
    _patch_client(monkeypatch, _make_directional_client(fwd, bwd))

    assert script.main() == 0
    payload = _read_output(_isolated_output)
    assert len(payload) == 2
    for event in payload:
        assert event["pubDate"] == _stable_now.isoformat()
        # Either summer (+02:00) or winter (+01:00) — date is in May → +02:00.
        assert event["pubDate"].endswith("+02:00")
