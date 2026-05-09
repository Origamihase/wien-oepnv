"""Tests for ``scripts/update_stammstrecke_status.py``.

The pyhafas client is mocked end-to-end so the test suite never reaches
the live ÖBB HAFAS endpoint. Each test exercises a single, isolated
branch of the script's decision tree: import-time failure, transport
error, circuit-breaker open, median-below-threshold, median-above-
threshold, no S-Bahn legs found.
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


@pytest.fixture(autouse=True)
def _isolated_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect cache writes to a per-test directory."""

    out = tmp_path / "cache" / "stammstrecke" / "events.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", out)
    yield out


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the module-level breaker so tests don't share state."""

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


def test_build_event_matches_schema_required_fields(_stable_now: datetime) -> None:
    event = script._build_event(
        median_delay_minutes=12.5,
        sample_size=8,
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
    # Timestamps must be ISO-8601 strings with offset (Europe/Vienna).
    assert event["pubDate"] == _stable_now.isoformat()
    assert event["starts_at"] == _stable_now.isoformat()
    assert event["pubDate"].endswith(("+02:00", "+01:00"))
    assert event["ends_at"] is None
    # GUID must be deterministic for the same timestamp.
    again = script._build_event(
        median_delay_minutes=12.5, sample_size=8, pub_date=_stable_now
    )
    assert event["guid"] == again["guid"]


# ---- main() integration tests ----------------------------------------------


def test_main_writes_event_when_median_above_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path, _stable_now: datetime
) -> None:
    fake_client = SimpleNamespace(
        profile=SimpleNamespace(),
        journeys=lambda **kwargs: [
            _journey(legs=[_leg(name="S 1", delay_minutes=11)]),
            _journey(legs=[_leg(name="S 2", delay_minutes=10)]),
            _journey(legs=[_leg(name="S 3", delay_minutes=9)]),
            _journey(legs=[_leg(name="S 7", delay_minutes=12)]),
            _journey(legs=[_leg(name="S 80", delay_minutes=15)]),
        ],
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    exit_code = script.main()
    assert exit_code == 0

    payload = _read_output(_isolated_output)
    assert len(payload) == 1
    event = payload[0]
    assert event["title"] == "S-Bahn Stammstrecke Verspätungen"
    assert event["pubDate"] == _stable_now.isoformat()
    assert "11.0 Minuten" in event["description"]


def test_main_writes_empty_when_median_below_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fake_client = SimpleNamespace(
        profile=SimpleNamespace(),
        journeys=lambda **kwargs: [
            _journey(legs=[_leg(name="S 1", delay_minutes=2)]),
            _journey(legs=[_leg(name="S 2", delay_minutes=4)]),
            _journey(legs=[_leg(name="S 3", delay_minutes=9)]),
            _journey(legs=[_leg(name="S 7", delay_minutes=3)]),
        ],
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    exit_code = script.main()
    assert exit_code == 0

    payload = _read_output(_isolated_output)
    assert payload == []


def test_main_writes_empty_when_median_equals_threshold(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    """Median exactly equal to threshold must NOT trigger the event."""

    fake_client = SimpleNamespace(
        profile=SimpleNamespace(),
        journeys=lambda **kwargs: [
            _journey(legs=[_leg(name="S 1", delay_minutes=9)]),
            _journey(legs=[_leg(name="S 2", delay_minutes=9)]),
            _journey(legs=[_leg(name="S 3", delay_minutes=9)]),
        ],
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_when_no_sbahn_legs(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path
) -> None:
    fake_client = SimpleNamespace(
        profile=SimpleNamespace(),
        journeys=lambda **kwargs: [
            _journey(legs=[_leg(name="REX 7", delay_minutes=20)]),
            _journey(legs=[_leg(name="IC 533", delay_minutes=15)]),
        ],
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    assert script.main() == 0
    assert _read_output(_isolated_output) == []


def test_main_writes_empty_on_import_error(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def raise_import_error() -> Any:
        raise ImportError("OEBBProfile not available in this pyhafas release")

    monkeypatch.setattr(script, "_build_client", raise_import_error)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []
    assert any("nicht verfügbar" in r.getMessage() for r in caplog.records)


def test_main_writes_empty_on_query_failure(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(**kwargs: Any) -> list[Any]:
        raise RuntimeError("connection reset by peer")

    fake_client = SimpleNamespace(profile=SimpleNamespace(), journeys=boom)
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 1
    assert _read_output(_isolated_output) == []
    assert any("fehlgeschlagen" in r.getMessage() for r in caplog.records)


def test_main_short_circuits_when_breaker_is_open(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-tripping the breaker forces main() onto its short-circuit path."""

    breaker = CircuitBreaker(
        "stammstrecke-hafas-pretrip",
        failure_threshold=2,
        recovery_timeout=600.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    monkeypatch.setattr(script, "_BREAKER", breaker)

    upstream_called = False

    def must_not_be_called(**kwargs: Any) -> list[Any]:
        nonlocal upstream_called
        upstream_called = True
        return []

    fake_client = SimpleNamespace(
        profile=SimpleNamespace(), journeys=must_not_be_called
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 0
    assert _read_output(_isolated_output) == []
    assert upstream_called is False
    assert any("breaker" in r.getMessage().lower() for r in caplog.records)


def test_main_handles_non_list_payload(
    monkeypatch: pytest.MonkeyPatch, _isolated_output: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_client = SimpleNamespace(
        profile=SimpleNamespace(),
        journeys=lambda **kwargs: "not-a-list",
    )
    monkeypatch.setattr(script, "_build_client", lambda: fake_client)

    caplog.set_level(logging.WARNING, logger=script.LOGGER.name)
    assert script.main() == 1
    assert _read_output(_isolated_output) == []


def test_collect_delays_handles_missing_legs_attribute() -> None:
    """A misbehaving HAFAS peer might emit journeys without a ``legs`` attr."""

    journeys = [SimpleNamespace()]  # no legs attribute at all
    assert script._collect_sbahn_delays_minutes(journeys) == []


def test_collect_delays_handles_legs_set_to_none() -> None:
    journeys = [SimpleNamespace(legs=None)]
    assert script._collect_sbahn_delays_minutes(journeys) == []
