"""Stammstrecke health: red only when the fetch is broken (decision 2026-10-10).

Michael: "Der Check soll nur auf rot, wenn der Abruf nicht funktioniert. Wenn
die Technik funktioniert gehört er auch auf grün. Wenn wirklich keine Züge
fahren, gehört dies auf der Homepage gemeldet, aber der Check bleibt grün."

Before, a completely dead monitor turned nothing red: the direction check
never fails (#1939) and a corridor without ledger rows looks like a night.
These tests pin the split between the two cases:

* broken fetch (request fails, unreadable or incomplete answer, skipped poll,
  monitor no longer runs) -> ``Stammstrecke-Abruf`` red, website silent;
* working fetch without trains -> check green, website names the corridor.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import requests
from zoneinfo import ZoneInfo

from scripts import update_stammstrecke_hbf as monitor
from scripts import update_stammstrecke_status as legacy
from src.utils import stammstrecke_poll as poll_mod
from src.utils.stammstrecke_poll import (
    POLL_STALE_HOURS,
    RESULT_ERROR,
    RESULT_OK,
    PollStatus,
    answer_is_incomplete,
    assess_poll,
    load_poll_status,
    record_poll,
    save_poll_status,
)

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 10, 17, 0, tzinfo=VIENNA)


def _ok(when: datetime, trains: int = 5) -> PollStatus:
    return record_poll(None, when=when, result=RESULT_OK, departures=30, trains=trains)


@pytest.fixture()
def status_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "poll_status.json"
    monkeypatch.setattr(poll_mod, "POLL_STATUS_PATH", path)
    monkeypatch.setattr(monitor, "POLL_STATUS_PATH", path)
    return path


# --- the rule ---------------------------------------------------------------


def test_missing_status_is_broken() -> None:
    verdict = assess_poll(None, now=NOW)
    assert verdict.working is False
    assert "kein lesbarer Abrufstatus" in verdict.summary


def test_never_succeeded_is_broken() -> None:
    status = record_poll(None, when=NOW, result=RESULT_ERROR, error="HTTP 401")
    verdict = assess_poll(status, now=NOW)
    assert verdict.working is False
    assert "HTTP 401" in verdict.detail


def test_stale_success_is_broken() -> None:
    old = _ok(NOW - timedelta(hours=POLL_STALE_HOURS, minutes=1))
    status = record_poll(old, when=NOW, result=RESULT_ERROR, error="ConnectionError")
    verdict = assess_poll(status, now=NOW)
    assert verdict.working is False
    assert "kein erfolgreicher Abruf seit 3h 1m" in verdict.summary
    assert "ConnectionError" in verdict.detail


def test_single_failed_poll_after_a_recent_success_stays_green() -> None:
    status = record_poll(
        _ok(NOW - timedelta(minutes=30)), when=NOW, result=RESULT_ERROR, error="HTTP 503"
    )
    verdict = assess_poll(status, now=NOW)
    assert verdict.working is True
    assert "HTTP 503" in verdict.detail


def test_working_fetch_without_trains_is_green() -> None:
    """The core of the decision: no trains is not a technical fault."""
    with_trains = _ok(NOW - timedelta(hours=10))
    status = record_poll(with_trains, when=NOW, result=RESULT_OK, departures=29, trains=0)
    verdict = assess_poll(status, now=NOW)
    assert verdict.working is True
    assert "kein Stammstrecken-Zug auf der Tafel seit 10h 0m" in verdict.summary


def test_working_fetch_with_trains_is_green() -> None:
    verdict = assess_poll(_ok(NOW - timedelta(minutes=12), trains=9), now=NOW)
    assert verdict.working is True
    assert "9 Stammstrecken-Zug/Züge" in verdict.summary


def test_failed_poll_keeps_last_success_and_last_train() -> None:
    first = _ok(NOW - timedelta(hours=1), trains=4)
    second = record_poll(first, when=NOW, result=RESULT_ERROR, error="x")
    assert second.last_success == first.last_success
    assert second.last_train_seen == first.last_train_seen
    assert second.last_result == RESULT_ERROR


def test_incomplete_answer_rule_is_structural() -> None:
    assert answer_is_incomplete(departures=12, readable=0) is True
    assert answer_is_incomplete(departures=12, readable=1) is False
    # An empty board is a valid answer (night, closure), not a broken one.
    assert answer_is_incomplete(departures=0, readable=0) is False


def test_status_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "poll_status.json"
    status = record_poll(_ok(NOW - timedelta(hours=1)), when=NOW, result=RESULT_ERROR, error="E")
    assert save_poll_status(status, path) is True
    assert load_poll_status(path) == status


def test_garbled_status_file_reads_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "poll_status.json"
    path.write_text('{"last_result": "ok"}', encoding="utf-8")
    assert load_poll_status(path) is None
    path.write_text("not json", encoding="utf-8")
    assert load_poll_status(path) is None


# --- the monitor records every poll ------------------------------------------


def _departure(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "S 1",
        "date": "2026-10-10",
        "time": "17:10:00",
        "rtTime": "17:11:00",
        "track": "1",
        "direction": "Wien Meidling Bahnhof",
    }
    base.update(fields)
    return base


def _tick(monkeypatch: pytest.MonkeyPatch, query: Any) -> tuple[str, Any]:
    monkeypatch.setattr(monitor, "_charge_one_request", lambda _when: None)
    monkeypatch.setattr(monitor, "_query_departure_board", query)
    monitor._BREAKER.reset()
    tally = monitor._TickTally()
    result = monitor._process_tick(
        session=cast(requests.Session, object()), state={}, when=NOW, tally=tally
    )
    monitor._BREAKER.reset()
    return result, tally


def test_tick_http_error_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = requests.Response()
    response.status_code = 500

    def query(*_a: Any, **_kw: Any) -> list[Any]:
        raise requests.HTTPError("boom", response=response)

    result, tally = _tick(monkeypatch, query)
    assert result == "error"
    assert tally.error == "HTTP 500"


def test_tick_broken_answer_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def query(*_a: Any, **_kw: Any) -> list[Any]:
        raise ValueError("VAO /departureBoard returned unparseable JSON")

    result, tally = _tick(monkeypatch, query)
    assert result == "error"
    assert tally.error == "ValueError"


def test_tick_empty_board_is_a_working_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    result, tally = _tick(monkeypatch, lambda *_a, **_kw: [])
    assert result == "ok"
    assert (tally.departures, tally.trains) == (0, 0)


def test_tick_board_without_stammstrecke_trains_is_a_working_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """22.09.2026 21:00: 29 departures, none on platform 1/2 — fetch worked."""
    board = [_departure(name="RJ 540", track="7"), _departure(track="9")]
    result, tally = _tick(monkeypatch, lambda *_a, **_kw: board)
    assert result == "ok"
    assert (tally.departures, tally.trains) == (2, 0)


def test_tick_board_with_trains_counts_them(monkeypatch: pytest.MonkeyPatch) -> None:
    board = [_departure(), _departure(time="17:20:00", rtTime="17:20:00")]
    result, tally = _tick(monkeypatch, lambda *_a, **_kw: board)
    assert result == "ok"
    assert tally.trains == 2


def test_tick_counts_trains_without_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """10.10.2026 14:01: 27 departures, no ledger update — VAO sent no rtTime.
    The trains were on the board all the same."""
    board = [_departure(rtTime=None), _departure(name="RJ 540", track="7")]
    result, tally = _tick(monkeypatch, lambda *_a, **_kw: board)
    assert result == "ok"
    assert tally.trains == 1


def test_tick_board_without_readable_fields_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = [{"name": "S 1", "date": "2026-10-10", "time": "17:10:00"}, {"foo": 1}]
    result, tally = _tick(monkeypatch, lambda *_a, **_kw: board)
    assert result == "incomplete"
    assert tally.departures == 2
    assert "ohne lesbare Felder" in tally.error


def test_record_poll_outcome_writes_failures_too(status_path: Path) -> None:
    monitor._record_poll_outcome(
        NOW - timedelta(minutes=30), RESULT_OK, monitor._TickTally(departures=20, trains=3)
    )
    monitor._record_poll_outcome(NOW, "error", monitor._TickTally(error="HTTP 503"))
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["last_result"] == "error"
    assert payload["last_error"] == "HTTP 503"
    assert payload["last_success"] == (NOW - timedelta(minutes=30)).isoformat(timespec="seconds")
    assert payload["last_success_trains"] == 3


def test_monitor_main_records_a_failed_poll(
    status_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() writes the status even when the request fails."""
    monkeypatch.setattr(monitor, "PENDING_TRIPS_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(monitor, "RECENTLY_FINALISED_PATH", tmp_path / "final.json")
    monkeypatch.setattr(monitor, "PENDING_TRIPS_LOCK_PATH", tmp_path / "pending.lock")
    monkeypatch.setattr(monitor, "_build_session", lambda _stack: object())
    monkeypatch.setattr(monitor, "_now_vienna", lambda: NOW)
    monkeypatch.setattr(monitor, "_report_silent_directions", lambda _now: [])
    monkeypatch.setattr(monitor, "configure_logging", lambda: None)
    monkeypatch.setattr(monitor, "_charge_one_request", lambda _when: None)

    def query(*_a: Any, **_kw: Any) -> list[Any]:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(monitor, "_query_departure_board", query)
    monitor._BREAKER.reset()
    try:
        assert monitor.main() == 1
    finally:
        monitor._BREAKER.reset()
    status = load_poll_status(status_path)
    assert status is not None
    assert status.last_result == "error"
    assert status.last_error == "ConnectionError"
    assert status.last_success is None


# --- health check ------------------------------------------------------------


def test_health_check_red_when_fetch_broken(status_path: Path) -> None:
    from scripts.health_check import check_stammstrecke_abruf

    save_poll_status(
        record_poll(_ok(NOW - timedelta(hours=5)), when=NOW, result=RESULT_ERROR, error="HTTP 500"),
        status_path,
    )
    check = check_stammstrecke_abruf(NOW)
    assert check.ok is False
    assert check.summary.startswith("FEHLER")


def test_health_check_red_when_monitor_never_wrote(status_path: Path) -> None:
    from scripts.health_check import check_stammstrecke_abruf

    assert check_stammstrecke_abruf(NOW).ok is False


def test_health_check_green_without_trains(status_path: Path) -> None:
    from scripts.health_check import check_stammstrecke_abruf

    save_poll_status(_ok(NOW - timedelta(minutes=20), trains=0), status_path)
    check = check_stammstrecke_abruf(NOW)
    assert check.ok is True
    assert check.note is False
    assert "kein Stammstrecken-Zug" in check.summary


def test_health_check_registers_the_fetch_check() -> None:
    import inspect

    from scripts import health_check

    assert "check_stammstrecke_abruf(now)" in inspect.getsource(health_check.main)


def test_legacy_quota_exception_still_maps_to_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    def charge(_when: datetime) -> None:
        raise legacy._QuotaExceeded("limit")

    monkeypatch.setattr(monitor, "_charge_one_request", charge)
    tally = monitor._TickTally()
    result = monitor._process_tick(
        session=cast(requests.Session, object()), state={}, when=NOW, tally=tally
    )
    assert result == "quota_exceeded"


# --- website / README: name a dark corridor only behind a working fetch -------


def _dark_corridor_rows() -> list[Any]:
    from scripts.generate_markdown_stats import StammstreckeRow

    ts = NOW - timedelta(hours=9)
    return [
        StammstreckeRow(timestamp=ts, weekday="Sa", hour=ts.hour, direction=d, delay_minutes=0.0)
        for d in ("Meidling", "Praterstern")
    ]


def test_dark_corridor_is_named_while_fetch_works() -> None:
    from scripts.generate_markdown_stats import render_direction_coverage_note

    note = render_direction_coverage_note(_dark_corridor_rows(), now=NOW, fetch_working=True)
    assert "Aktuell keine Fahrten" in note


def test_dark_corridor_is_not_named_behind_a_broken_fetch() -> None:
    from scripts.generate_markdown_stats import render_direction_coverage_note

    assert render_direction_coverage_note(
        _dark_corridor_rows(), now=NOW, fetch_working=False
    ) == ""


def test_summary_ships_the_poll_evidence() -> None:
    from scripts.generate_markdown_stats import _direction_coverage, _poll_evidence

    evidence = _poll_evidence(_ok(NOW - timedelta(minutes=5)))
    coverage = _direction_coverage([], all_rows=[], window_days=30, poll=evidence)
    assert coverage["poll"] == {
        "last_success": (NOW - timedelta(minutes=5)).isoformat(timespec="seconds"),
        "stale_hours": POLL_STALE_HOURS,
    }
    assert _poll_evidence(None)["last_success"] is None


def test_site_reads_the_poll_gate_with_the_python_fallback() -> None:
    import re

    js = (Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js").read_text(
        encoding="utf-8"
    )
    assert re.search(r"coverage\s*&&\s*coverage\.poll\b", js)
    fallback = re.search(r"poll\.stale_hours,\s*(\d+)", js)
    assert fallback and int(fallback.group(1)) == int(POLL_STALE_HOURS)
    assert "fetchWorking &&" in js
