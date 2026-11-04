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
    """Update cycle runs that change with the fake clock."""

    def __init__(self, timeline: list[tuple[datetime, dict[str, Any]]], issues: list[int] | None = None) -> None:
        self.timeline = timeline
        self.now = T0
        self.dispatched: list[tuple[datetime, str]] = []
        self.issues = list(issues or [])
        self.created: list[str] = []
        self.closed: list[int] = []

    def clock(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def update_runs(self) -> list[tf.Run]:
        visible = [raw for moment, raw in self.timeline if moment <= self.now]
        for moment, workflow in self.dispatched:
            if workflow == tf.UPDATE_WORKFLOW and moment + timedelta(seconds=30) <= self.now:
                number = 100 + len([d for d in self.dispatched if d[0] <= moment])
                visible.append(_raw(number, moment, "workflow_dispatch", tf.BOT_LOGIN,
                                    conclusion=self.fallback_conclusion))
        return tf.parse_runs({"workflow_runs": visible})

    fallback_conclusion = "success"

    def dispatch(self, workflow: str) -> None:
        self.dispatched.append((self.now, workflow))

    def open_issues(self) -> list[int]:
        return list(self.issues)

    def create_issue(self, title: str, body: str) -> None:
        self.created.append(body)
        self.issues.append(7)

    def close_issue(self, number: int, comment: str) -> None:
        self.closed.append(number)
        self.issues.remove(number)


def _watch(client: FakeClient) -> int:
    return tf.watch(client, clock=client.clock, sleep=client.sleep)  # type: ignore[arg-type]


def test_waiter_ends_without_a_start_when_ifttt_ticks() -> None:
    client = FakeClient([(T0, _raw(1, T0)), (T0 + timedelta(minutes=30), _raw(2, T0 + timedelta(minutes=30)))])
    assert _watch(client) == 0
    assert client.dispatched == [] and client.created == []
    assert client.now < T0 + timedelta(minutes=32)


def test_silence_starts_the_update_cycle_every_40_minutes_and_opens_one_issue() -> None:
    late_ifttt = T0 + timedelta(minutes=125)
    client = FakeClient([(T0, _raw(1, T0)), (late_ifttt, _raw(2, late_ifttt))])
    assert _watch(client) == 0
    starts = [moment - T0 for moment, workflow in client.dispatched if workflow == tf.UPDATE_WORKFLOW]
    assert starts == [timedelta(minutes=40), timedelta(minutes=80), timedelta(minutes=120)]
    assert len(client.created) == 1 and "IFTTT" in client.created[0]


def test_issue_closes_when_the_waiter_of_an_ifttt_run_starts() -> None:
    client = FakeClient([(T0, _raw(1, T0)), (T0 + timedelta(minutes=30), _raw(2, T0 + timedelta(minutes=30)))],
                        issues=[7])
    _watch(client)
    assert client.closed == [7]


def test_a_failed_fallback_run_starts_the_health_check() -> None:
    client = FakeClient([(T0, _raw(1, T0)), (T0 + timedelta(minutes=70), _raw(2, T0 + timedelta(minutes=70)))])
    client.fallback_conclusion = "failure"
    _watch(client)
    assert [w for _, w in client.dispatched] == [tf.UPDATE_WORKFLOW, tf.HEALTH_WORKFLOW]


def test_waiter_starts_its_successor_at_the_end_of_its_life(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tf, "WAITER_LIFETIME", timedelta(minutes=10))
    client = FakeClient([(T0, _raw(1, T0))])
    _watch(client)
    assert client.dispatched[-1][1] == tf.SELF_WORKFLOW


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
