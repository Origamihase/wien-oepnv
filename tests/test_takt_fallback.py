"""Takt-Fallback (Michael 2026-11-04): start only when IFTTT is silent, issue, Health check red."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import src.health_check.takt as takt_check
from src import takt_fallback as tf

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _raw(number: int, created: datetime, event: str = "repository_dispatch",
         actor: str = "Origamihase", status: str = "completed", conclusion: str = "success") -> dict[str, Any]:
    return {"run_number": number, "id": 1000 + number, "event": event,
            "triggering_actor": {"login": actor}, "created_at": created.isoformat().replace("+00:00", "Z"),
            "status": status, "conclusion": conclusion}


def _runs(*raw: dict[str, Any]) -> list[tf.Run]:
    return tf.parse_runs({"workflow_runs": list(raw)})


def test_parse_runs_orders_by_run_number_not_by_time() -> None:
    # 2026-11-01: the API listed runs of 08:00-08:59 UTC one hour late.
    runs = _runs(_raw(9793, T0 + timedelta(hours=1)), _raw(9795, T0), _raw(9794, T0 + timedelta(minutes=90)))
    assert [run.number for run in runs] == [9795, 9794, 9793]


def test_fallback_run_is_recognised_by_event_and_bot() -> None:
    runs = _runs(_raw(3, T0, "workflow_dispatch", tf.BOT_LOGIN), _raw(2, T0, "workflow_dispatch"),
                 _raw(1, T0))
    assert [run.is_fallback for run in runs] == [True, False, False]
    assert tf.newest_foreign_number(runs) == 2


@pytest.mark.parametrize("minutes", [0, 30, 31, 39])
def test_no_start_while_a_run_is_younger_than_40_minutes(minutes: int) -> None:
    runs = _runs(_raw(1, T0))
    assert not tf.decide(runs, T0 + timedelta(minutes=minutes), None).fire


def test_start_after_40_minutes_of_silence() -> None:
    decision = tf.decide(_runs(_raw(1, T0)), T0 + timedelta(minutes=40), None)
    assert decision.fire and decision.ifttt_silent


def test_own_start_counts_even_before_the_api_lists_it() -> None:
    now = T0 + timedelta(minutes=45)
    assert not tf.decide(_runs(_raw(1, T0)), now, last_dispatch=T0 + timedelta(minutes=40)).fire
    assert tf.decide(_runs(_raw(1, T0)), T0 + timedelta(minutes=80), T0 + timedelta(minutes=40)).fire


def test_a_backup_cron_run_also_counts_as_a_tick() -> None:
    runs = _runs(_raw(2, T0 + timedelta(minutes=17), "schedule", tf.BOT_LOGIN), _raw(1, T0))
    assert not tf.decide(runs, T0 + timedelta(minutes=45), None).fire


def test_a_creation_time_in_the_future_reads_as_now() -> None:
    runs = _runs(_raw(1, T0 + timedelta(hours=1)))
    assert not tf.decide(runs, T0, None).fire


class FakeClient:
    """The REST calls of one check, with a fixed list of Update cycle runs."""

    def __init__(self, runs: list[dict[str, Any]], issues: list[int] | None = None,
                 created: datetime | None = None, other_waiting: bool = False) -> None:
        self.runs = runs
        self.dispatched: list[str] = []
        self.issues = list(issues or [])
        self.opened: list[str] = []
        self.closed: list[int] = []
        self.created = created
        self.other_waiting = other_waiting

    def update_runs(self) -> list[tf.Run]:
        return tf.parse_runs({"workflow_runs": list(self.runs)})

    def run_created(self, run_id: int) -> datetime | None:
        return self.created

    def other_check_waiting(self, run_id: int) -> bool:
        return self.other_waiting

    def dispatch(self, workflow: str) -> None:
        self.dispatched.append(workflow)

    def open_issues(self) -> list[int]:
        return list(self.issues)

    def create_issue(self, title: str, body: str) -> None:
        self.opened.append(body)
        self.issues.append(7)

    def close_issue(self, number: int, comment: str) -> None:
        self.closed.append(number)
        self.issues.remove(number)


def _run(client: FakeClient, now: datetime, scheduled: bool = False) -> int:
    return tf.run_check(client, run_id=1, now=now, scheduled=scheduled)  # type: ignore[arg-type]


def test_check_after_40_minutes_of_silence_starts_the_update_cycle_and_opens_one_issue() -> None:
    client = FakeClient([_raw(1, T0)], created=T0)
    assert _run(client, T0 + timedelta(minutes=40)) == 0
    assert client.dispatched == [tf.UPDATE_WORKFLOW, tf.SELF_WORKFLOW]
    assert len(client.opened) == 1 and "IFTTT" in client.opened[0]


def test_a_second_check_in_the_outage_opens_no_second_issue() -> None:
    client = FakeClient([_raw(2, T0 + timedelta(minutes=40), "workflow_dispatch", tf.BOT_LOGIN), _raw(1, T0)],
                        issues=[7], created=T0 + timedelta(minutes=40))
    _run(client, T0 + timedelta(minutes=80))
    assert client.dispatched == [tf.UPDATE_WORKFLOW, tf.SELF_WORKFLOW] and client.opened == []


def test_check_without_silence_starts_no_update_only_its_successor() -> None:
    # A gated backup-cron run 20 minutes ago counts as a tick.
    client = FakeClient([_raw(2, T0 + timedelta(minutes=20), "schedule", tf.BOT_LOGIN), _raw(1, T0)], created=T0)
    _run(client, T0 + timedelta(minutes=40))
    assert client.dispatched == [tf.SELF_WORKFLOW] and client.opened == []


def test_check_that_did_not_wait_fails_loudly_and_starts_nothing() -> None:
    client = FakeClient([], created=T0)
    assert _run(client, T0 + timedelta(minutes=1)) == 1
    assert client.dispatched == []


def test_schedule_does_nothing_while_a_check_waits() -> None:
    client = FakeClient([], other_waiting=True)
    assert _run(client, T0 + timedelta(hours=5), scheduled=True) == 0
    assert client.dispatched == []


def test_schedule_restarts_the_watch_when_none_waits() -> None:
    client = FakeClient([_raw(1, T0)], other_waiting=False)
    _run(client, T0 + timedelta(minutes=10), scheduled=True)
    assert client.dispatched == [tf.SELF_WORKFLOW]


def test_a_failed_fallback_run_starts_the_health_check() -> None:
    client = FakeClient([_raw(2, T0 + timedelta(minutes=40), "workflow_dispatch", tf.BOT_LOGIN,
                              conclusion="failure"), _raw(1, T0)], issues=[7], created=T0 + timedelta(minutes=40))
    _run(client, T0 + timedelta(minutes=80))
    assert client.dispatched == [tf.HEALTH_WORKFLOW, tf.UPDATE_WORKFLOW, tf.SELF_WORKFLOW]


def test_a_start_that_never_appeared_starts_the_health_check() -> None:
    client = FakeClient([_raw(1, T0)], issues=[7], created=T0 + timedelta(minutes=40))
    _run(client, T0 + timedelta(minutes=80))
    assert client.dispatched == [tf.HEALTH_WORKFLOW, tf.UPDATE_WORKFLOW, tf.SELF_WORKFLOW]


def test_ifttt_run_closes_the_issue() -> None:
    client = FakeClient([_raw(3, T0 + timedelta(minutes=90)),
                         _raw(2, T0 + timedelta(minutes=40), "workflow_dispatch", tf.BOT_LOGIN), _raw(1, T0)],
                        issues=[7])
    assert tf.close_issue_when_ticking(client, T0 + timedelta(minutes=90)) == 0  # type: ignore[arg-type]
    assert client.closed == [7]


def test_ifttt_run_without_open_issue_reads_nothing_else() -> None:
    client = FakeClient([], issues=[])
    client.update_runs = None  # type: ignore[assignment,method-assign]
    assert tf.close_issue_when_ticking(client, T0) == 0  # type: ignore[arg-type]


# --- Health check "Takt" ----------------------------------------------------------


def _check(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]], built: datetime | None,
           now: datetime) -> Any:
    monkeypatch.setattr(takt_check, "_github_api_json", lambda path: {"workflow_runs": runs})
    text = None if built is None else (
        f"<rss><channel><lastBuildDate>{built.strftime('%a, %d %b %Y %H:%M:%S +0000')}"
        "</lastBuildDate></channel></rss>")
    monkeypatch.setattr(takt_check, "read_capped_text", lambda *a, **k: text)
    return takt_check.check_takt(now)


def test_health_green_while_ifttt_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check(monkeypatch, [_raw(1, T0)], T0, T0 + timedelta(minutes=20))
    assert check.ok and not check.note


def test_health_note_while_the_fallback_carries_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [_raw(2, T0 + timedelta(minutes=40), "workflow_dispatch", tf.BOT_LOGIN), _raw(1, T0)]
    check = _check(monkeypatch, runs, T0 + timedelta(minutes=44), T0 + timedelta(minutes=70))
    assert check.ok and check.note


def test_health_red_when_the_fallback_fails_too(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [_raw(2, T0 + timedelta(minutes=40), "workflow_dispatch", tf.BOT_LOGIN, conclusion="failure"),
            _raw(1, T0)]
    check = _check(monkeypatch, runs, T0 + timedelta(minutes=4), T0 + timedelta(minutes=90))
    assert not check.ok


def test_health_note_when_the_api_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: str) -> Any:
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(takt_check, "_github_api_json", boom)
    check = takt_check.check_takt(T0)
    assert check.ok and check.note


def test_the_ifttt_run_may_close_the_issue_and_read_the_runs() -> None:
    """Independent check 2026-11-04: the closing step needs ``issues: write``
    and ``actions: read`` in the Update cycle job (``actions: read`` so the
    list of runs does not depend on the repository being public)."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".github/workflows/update-cycle.yml").read_text(encoding="utf-8")
    job_permissions = text.split("    permissions:\n", 1)[1].split("    env:\n", 1)[0]
    assert "issues: write" in job_permissions and "actions: read" in job_permissions
    step = text.split("- name: Close IFTTT outage issue", 1)[1].split("- name:", 1)[0]
    assert "github.event_name == 'repository_dispatch'" in step
    assert "scripts/takt_fallback.py --close-issue" in step
