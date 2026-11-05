"""The Update cycle starts the daily KI-Wächter when it is due (``src/ki_waechter/dispatch.py``).

Until 2026-11-05 ``ki-waechter.yml`` hung off ``workflow_run`` of the Update
cycle and its gate job started a runner on every tick to say "not now".
"""

from __future__ import annotations

import base64
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import ki_waechter as kw
from src.ki_waechter import dispatch as d

from ._support import REPO, _run_gate

VIENNA = kw.VIENNA


def _at(text: str) -> datetime:
    """A Vienna wall-clock time as an aware datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=VIENNA)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _run(number: int, started: datetime, status: str = "completed", event: str = "repository_dispatch",
         conclusion: str = "success") -> dict[str, Any]:
    return {"id": 1000 + number, "run_number": number, "event": event, "status": status,
            "conclusion": conclusion, "run_started_at": _iso(started), "head_branch": "main"}


class FakeClient(d.WatcherClient):
    """The REST calls of one decision: the record on main, the watcher's runs, the dispatch."""

    def __init__(self, record: dict[str, Any], runs: list[dict[str, Any]]) -> None:
        super().__init__("o/r", "t", "https://api.github.com")
        self.record_payload = record
        self.runs = runs
        self.calls: list[tuple[str, str]] = []
        self.dispatched: list[dict[str, Any]] = []

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        self.calls.append((method, path.split("?", 1)[0]))
        if method == "POST":
            self.dispatched.append(dict(payload))
            return None
        if path.startswith("/contents/"):
            raw = json.dumps(self.record_payload).encode("utf-8")
            return {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"}
        if path.startswith(f"/actions/workflows/{d.WORKFLOW}/runs"):
            return {"workflow_runs": list(self.runs)}
        raise AssertionError(path)


def _payload(at: datetime | None, success: datetime | None, outcome: str = "failure") -> dict[str, Any]:
    entry: dict[str, Any] = {"outcome": outcome}
    if at is not None:
        entry["at"] = _iso(at)
    if success is not None:
        entry["last_success"] = _iso(success)
    return {"steps": {"KI-Wächter": entry}}


def _record(at: datetime | None, success: datetime | None) -> d.Record:
    return d.parse_record(_payload(at, success))


# --- the decision, as the former gate took it -----------------------------------------


@pytest.mark.parametrize(
    ("record", "now", "due"),
    [
        # Before 15:00 Vienna time nothing runs, however old the record (9:00 until 2026-11-05).
        (_record(None, None), _at("2026-11-05 14:59"), False),
        (_record(_at("2026-11-04 14:02"), _at("2026-11-03 11:32")), _at("2026-11-05 14:59"), False),
        # From 15:00 the first tick of the day runs; yesterday's failure does not hold it back.
        (_record(_at("2026-11-04 14:02"), _at("2026-11-03 11:32")), _at("2026-11-05 15:00"), True),
        (_record(None, None), _at("2026-11-05 15:00"), True),
        # Succeeded today: done for the day.
        (_record(_at("2026-11-05 15:03"), _at("2026-11-05 15:03")), _at("2026-11-05 15:30"), False),
        (_record(_at("2026-11-05 15:03"), _at("2026-11-05 15:03")), _at("2026-11-05 23:59"), False),
        # Failed today: waits two hours, then tries again.
        (_record(_at("2026-11-05 15:03"), _at("2026-11-04 11:32")), _at("2026-11-05 16:30"), False),
        (_record(_at("2026-11-05 15:03"), _at("2026-11-04 11:32")), _at("2026-11-05 17:02"), False),
        (_record(_at("2026-11-05 15:03"), _at("2026-11-04 11:32")), _at("2026-11-05 17:04"), True),
        # A failure late yesterday is yesterday's, a success yesterday too.
        (_record(_at("2026-11-04 23:30"), None), _at("2026-11-05 15:01"), True),
        (_record(_at("2026-11-04 23:30"), _at("2026-11-04 23:30")), _at("2026-11-05 15:01"), True),
        # Summer: 15:00 Vienna is 13:00 UTC; the Vienna clock decides.
        (_record(None, None), datetime(2026, 7, 1, 12, 59, tzinfo=UTC), False),
        (_record(None, None), datetime(2026, 7, 1, 13, 0, tzinfo=UTC), True),
    ],
)
def test_not_due_follows_the_former_gate(record: d.Record, now: datetime, due: bool) -> None:
    assert (d.not_due(record, now) == "") is due


def test_the_start_hour_and_the_retry_spacing_are_parameters() -> None:
    record = _record(_at("2026-11-05 15:03"), None)
    assert d.not_due(d.Record(), _at("2026-11-05 08:30"), start_hour=8) == ""
    assert d.not_due(record, _at("2026-11-05 16:10"), retry_hours=1) == ""
    assert d.not_due(record, _at("2026-11-05 16:00"), retry_hours=1) != ""
    assert "24:00" in d.not_due(d.Record(), _at("2026-11-05 23:59"), start_hour=24)


def test_parse_record_reads_both_times_and_tolerates_a_missing_file(tmp_path: Path) -> None:
    record = d.parse_record({"steps": {"KI-Wächter": {"outcome": "overloaded", "at": "2026-11-04T13:02:05+00:00",
                                                      "last_success": "2026-11-03T10:32:42+00:00"}}})
    assert record.at == datetime(2026, 11, 4, 13, 2, 5, tzinfo=UTC)
    assert record.last_success == datetime(2026, 11, 3, 10, 32, 42, tzinfo=UTC)
    assert d.parse_record({"steps": {"KI-Wächter": {"outcome": "failure", "at": "kein Datum"}}}) == d.Record()
    assert d.parse_record({"steps": {}}) == d.Record() and d.parse_record(None) == d.Record()
    # A ``Z`` suffix and a naive time (UTC) are read too.
    assert d.parse_record({"steps": {"KI-Wächter": {"at": "2026-11-04T13:02:05Z"}}}).at == datetime(
        2026, 11, 4, 13, 2, 5, tzinfo=UTC)
    assert d.read_record(tmp_path / "fehlt.json") == d.Record()
    (tmp_path / "kaputt.json").write_text("{", encoding="utf-8")
    assert d.read_record(tmp_path / "kaputt.json") == d.Record()
    (tmp_path / "ok.json").write_text(json.dumps(_payload(_at("2026-11-05 15:03"), None)), encoding="utf-8")
    assert d.read_record(tmp_path / "ok.json").at == _at("2026-11-05 15:03")
    # The real record of the repository parses.
    assert d.read_record(REPO / d.RECORD).at is not None


# --- the three requests of a due tick -------------------------------------------------

_NOW = _at("2026-11-05 15:01")
_YESTERDAY = _payload(_at("2026-11-04 14:02"), _at("2026-11-03 11:32"), "overloaded")
_RUN_URL = "https://github.com/o/r/actions/runs/5"


def test_a_due_tick_reads_main_and_the_runs_and_dispatches() -> None:
    client = FakeClient(_YESTERDAY, [_run(190, _at("2026-11-04 14:00"))])
    line = d.decide_and_dispatch(client, d.parse_record(_YESTERDAY), _NOW, _RUN_URL)
    assert line.startswith("KI-Wächter gestartet") and "05.11.2026 15:01 Wien" in line
    assert client.dispatched == [{"event_type": "ki-waechter", "client_payload": {"update_run": _RUN_URL}}]
    assert client.calls == [("GET", f"/contents/{d.RECORD.as_posix()}"),
                            ("GET", f"/actions/workflows/{d.WORKFLOW}/runs"), ("POST", "/dispatches")]


def test_the_checkout_decides_first_without_a_request() -> None:
    client = FakeClient(_YESTERDAY, [])
    today = d.parse_record(_payload(_at("2026-11-05 15:03"), _at("2026-11-05 15:03"), "success"))
    line = d.decide_and_dispatch(client, today, _at("2026-11-05 15:31"), _RUN_URL)
    assert line.startswith("Kein KI-Wächter: die heutige Prüfung ist gelungen") and client.calls == []


def test_the_fresh_record_on_main_wins_over_the_checkout() -> None:
    """The audit of the previous tick published its record after this tick's checkout."""
    fresh = _payload(_at("2026-11-05 15:03"), _at("2026-11-05 15:03"), "success")
    client = FakeClient(fresh, [_run(200, _at("2026-11-05 15:01"))])
    line = d.decide_and_dispatch(client, d.parse_record(_YESTERDAY), _at("2026-11-05 15:31"), _RUN_URL)
    assert "gelungen" in line and "neuer als der Checkout" in line
    assert client.dispatched == [] and client.calls == [("GET", f"/contents/{d.RECORD.as_posix()}")]


@pytest.mark.parametrize("status", sorted(d.ACTIVE_STATES))
def test_no_dispatch_while_a_run_of_the_watcher_is_active(status: str) -> None:
    client = FakeClient(_YESTERDAY, [_run(200, _at("2026-11-05 15:01"), status=status, conclusion="")])
    line = d.decide_and_dispatch(client, d.parse_record(_YESTERDAY), _at("2026-11-05 15:31"), _RUN_URL)
    assert line == "Kein KI-Wächter: Lauf #200 des Wächters läuft oder wartet noch."
    assert client.dispatched == []


def test_a_failed_try_two_hours_back_is_tried_again_without_reading_jobs() -> None:
    """Until later on 2026-11-05 the cycle read the jobs of today's newest run to see
    whether the gate had refused it for the day's budget of Gemini requests
    (01.11.2026: the trial runs had used it, the gate said so 28 more times).
    Nothing counts a budget since, Google does (scripts/ki_waechter.py)."""
    failed_today = _payload(_at("2026-11-05 15:03"), _at("2026-11-04 17:32"))
    runs = [_run(201, _at("2026-11-05 17:02")), _run(200, _at("2026-11-05 15:01"))]
    client = FakeClient(failed_today, runs)
    line = d.decide_and_dispatch(client, d.parse_record(failed_today), _at("2026-11-05 19:31"), _RUN_URL)
    assert line.startswith("KI-Wächter gestartet")
    assert [path for _method, path in client.calls] == [
        f"/contents/{d.RECORD.as_posix()}", f"/actions/workflows/{d.WORKFLOW}/runs", "/dispatches",
    ]


def test_the_record_on_main_is_decoded_from_base64_and_capped() -> None:
    client = FakeClient(_YESTERDAY, [])
    assert client.record_on_main() == d.parse_record(_YESTERDAY)
    too_big = FakeClient({"steps": {"KI-Wächter": {"filler": "x" * d.MAX_RECORD_BYTES}}}, [])
    with pytest.raises(RuntimeError, match="zu groß"):
        too_big.record_on_main()


# --- the command line and the workflows -------------------------------------------------


def test_the_step_sends_nothing_before_nine_and_asks_github_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KI_WAECHTER_START_HOUR", "24")
    monkeypatch.setattr(d, "client_from_env", lambda: pytest.fail("no client before the start hour"))
    assert kw.main(["dispatch", "--record", str(tmp_path / "fehlt.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Kein KI-Wächter: ") and "24:00" in out


def test_the_step_warns_and_leaves_the_next_tick_to_decide_when_github_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Broken(FakeClient):
        def _request(self, method: str, path: str, payload: Any = None) -> Any:
            raise RuntimeError("GitHub GET /contents: HTTP 502")

    monkeypatch.setenv("KI_WAECHTER_START_HOUR", "0")
    monkeypatch.setattr(d, "client_from_env", lambda: Broken(_YESTERDAY, []))
    assert d.main(["--record", str(tmp_path / "fehlt.json")]) == 1
    out = capsys.readouterr().out
    assert out.startswith("::warning title=KI-Wächter::Start nicht entschieden: RuntimeError: GitHub GET")


def test_the_step_dispatches_through_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient(_YESTERDAY, [])
    monkeypatch.setenv("KI_WAECHTER_START_HOUR", "0")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setattr(d, "client_from_env", lambda: client)
    record = tmp_path / "record.json"
    record.write_text(json.dumps(_YESTERDAY), encoding="utf-8")
    assert kw.main(["dispatch", "--record", str(record)]) == 0
    assert capsys.readouterr().out.startswith("KI-Wächter gestartet")
    assert client.dispatched[0]["client_payload"] == {"update_run": "https://github.com/o/r/actions/runs/77"}


def test_client_from_env_needs_token_and_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    with pytest.raises(RuntimeError, match="fehlt"):
        d.client_from_env()
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", "http://evil.example")
    with pytest.raises(RuntimeError, match="vertrauenswürdig"):
        d.client_from_env()
    monkeypatch.delenv("GITHUB_API_URL")
    client = d.client_from_env()
    assert (client.repository, client.api_url) == ("o/r", "https://api.github.com")


def test_the_workflows_hand_the_decision_to_the_update_cycle() -> None:
    """No runner starts for the watcher on a tick without an audit (2026-11-05)."""
    watcher = yaml.safe_load((REPO / ".github/workflows/ki-waechter.yml").read_text(encoding="utf-8"))
    on = watcher[True] if True in watcher else watcher["on"]
    assert "workflow_run" not in on
    assert on["repository_dispatch"] == {"types": [d.EVENT_TYPE]}
    assert "KI_WAECHTER_START_HOUR" not in watcher["env"]
    assert watcher["env"]["KI_WAECHTER_RETRY_HOURS"] == str(d.RETRY_HOURS)
    gate = watcher["jobs"]["gate"]["steps"][0]
    assert "repository_dispatch)" in gate["run"] and "CYCLE_CONCLUSION" not in gate["run"]
    assert gate["env"]["CYCLE_RUN"] == "${{ github.event.client_payload.update_run }}"
    cycle = yaml.safe_load((REPO / ".github/workflows/update-cycle.yml").read_text(encoding="utf-8"))
    job = cycle["jobs"]["cycle"]
    steps = job["steps"]
    step = steps[-1]
    assert step["name"] == "Start the KI-Wächter when due"
    assert step["run"] == "python scripts/ki_waechter.py dispatch"
    assert step["if"] == "github.ref == 'refs/heads/main'" and step["continue-on-error"] is True
    assert "always()" not in step["if"]  # only after a tick that went through, like "Update cycle success"
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert step["env"]["KI_WAECHTER_START_HOUR"] == str(d.START_HOUR)
    assert step["env"]["KI_WAECHTER_RETRY_HOURS"] == str(d.RETRY_HOURS)
    # After the publish, so the audit's checkout of main holds this tick's feed.
    assert steps[-2]["name"] == "Validate and publish cycle outputs"
    # ``POST /dispatches`` needs contents: write, the runs listing actions: read.
    assert job["permissions"]["contents"] == "write" and job["permissions"]["actions"] == "read"


def test_the_gate_takes_no_other_event(tmp_path: Path) -> None:
    """``workflow_run`` of the Update cycle no longer reaches the gate; an unexpected event fails loudly."""
    with pytest.raises(subprocess.CalledProcessError):
        _run_gate(tmp_path, "workflow_run")


def test_the_gate_names_the_update_run_that_sent_the_dispatch(tmp_path: Path) -> None:
    out = _run_gate(tmp_path, "repository_dispatch", {"CYCLE_RUN": _RUN_URL})
    assert out["run"] == "true" and out["publish"] == "true" and out["revs"] == '["HEAD"]'


def test_a_day_of_ticks_costs_requests_only_when_an_audit_may_be_due() -> None:
    """48 ticks of a normal day: the record of the checkout answers 46 of them without a request."""
    requests_total = 0
    dispatched = 0
    record = _YESTERDAY
    for tick in range(48):
        now = _at("2026-11-05 00:01") + timedelta(minutes=30 * tick)
        client = FakeClient(record, [])
        if d.not_due(d.parse_record(record), now):
            continue
        d.decide_and_dispatch(client, d.parse_record(record), now, _RUN_URL)
        requests_total += len(client.calls)
        if client.dispatched:
            dispatched += 1
            record = _payload(now + timedelta(minutes=2), now + timedelta(minutes=2), "success")
    assert dispatched == 1 and requests_total == 3
