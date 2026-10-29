"""Automatic choice of the VAO API version (operator decision 2026-10-29).

Michael: "Lies automatisch aus, was die neuste Version ist. Wenn sie nicht
funktioniert, nimm automatisch die davor". On 2026-10-28 the VAO withdrew
v1.13.0 between 14:30 and 15:00; every poll failed until a code change at
22:30. These tests pin the order of versions a poll asks, the 24-hour back
seat of a failed version (budget), the stored choice and the Wächter rule.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import requests
from zoneinfo import ZoneInfo

from scripts import feed_watch
from scripts import update_stammstrecke_hbf as monitor
from src.providers import vor as vor_provider
from src.utils import vao_version
from src.utils.vao_version import ListedVersion, VersionState

VIENNA = ZoneInfo("Europe/Vienna")
#: Taken at import, before the autouse fixture in conftest.py stubs the list.
_REAL_FETCH = vor_provider.fetch_version_list
_REAL_TICK = monitor._process_tick
WITHDRAWN = datetime(2026, 10, 28, 15, 0, tzinfo=VIENNA)

#: The list as the VAO served it after the withdrawal (fetched 28.10.2026).
LIST_AFTER = [
    {"serviceVersion": "v1.11.0", "serverVersion": "2.49.0", "dialectVersion": "2.49",
     "endPoint": "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/",
     "Decommission": "31.05.2027"},
    {"serviceVersion": "v1.12.0", "serverVersion": "2.52.1", "dialectVersion": "2.52",
     "endPoint": "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.12.0/",
     "Decommission": "30.08.2027"},
]
LIST_BEFORE = [*LIST_AFTER, {"serviceVersion": "v1.13.0", "Decommission": "30.06.2028"}]


def _listed(payload: list[dict[str, str]]) -> list[ListedVersion]:
    return vao_version.parse_version_list(payload)


# --- the list ----------------------------------------------------------------


def test_list_is_read_newest_first_with_its_end_dates() -> None:
    listed = _listed(LIST_BEFORE)
    assert [item.version for item in listed] == ["v1.13.0", "v1.12.0", "v1.11.0"]
    assert listed[1].decommission is not None
    assert listed[1].decommission.isoformat() == "2027-08-30"


def test_list_drops_entries_without_a_version() -> None:
    payload: list[Any] = [
        {"serviceVersion": "v1.12.0"},
        {"serviceVersion": "../../etc"},
        {"serviceVersion": 12},
        "v1.14.0",
        {"serviceVersion": "v1.12.0", "Decommission": "31.13.2027"},
        {"serviceVersion": "v1.10.0", "Decommission": "kein Datum"},
    ]
    listed = vao_version.parse_version_list(payload)
    assert listed == [ListedVersion("v1.12.0"), ListedVersion("v1.10.0")]
    assert vao_version.parse_version_list({"serviceVersion": "v1.12.0"}) == []


def test_versions_sort_by_number_not_by_text() -> None:
    listed = vao_version.parse_version_list(
        [{"serviceVersion": "v1.9.0"}, {"serviceVersion": "v1.10.0"}]
    )
    assert [item.version for item in listed] == ["v1.10.0", "v1.9.0"]


# --- the order a poll asks -------------------------------------------------------


def test_the_newest_listed_version_comes_first_the_one_before_second() -> None:
    order = vao_version.candidates(
        _listed(LIST_BEFORE), VersionState(), default="v1.12.0", now=WITHDRAWN
    )
    assert order == ["v1.13.0", "v1.12.0"]


def test_a_withdrawn_version_is_not_asked() -> None:
    """28.10.2026: the list no longer named v1.13.0, so no poll would have asked it."""
    order = vao_version.candidates(
        _listed(LIST_AFTER), VersionState(version="v1.13.0"), default="v1.13.0", now=WITHDRAWN
    )
    assert order == ["v1.12.0", "v1.11.0"]


def test_a_decommissioned_version_is_not_asked() -> None:
    later = datetime(2027, 6, 1, 8, 0, tzinfo=VIENNA)
    assert vao_version.candidates(
        _listed(LIST_AFTER), VersionState(), default="v1.12.0", now=later
    ) == ["v1.12.0"]


def test_a_failed_version_waits_behind_the_others_for_a_day() -> None:
    state = VersionState(version="v1.12.0", failed={"v1.13.0": WITHDRAWN})
    soon = WITHDRAWN + timedelta(minutes=30)
    assert vao_version.candidates(_listed(LIST_BEFORE), state, default="x", now=soon) == [
        "v1.12.0",
        "v1.11.0",
    ]
    day_later = WITHDRAWN + vao_version.FAILED_SKIP
    assert vao_version.candidates(_listed(LIST_BEFORE), state, default="x", now=day_later) == [
        "v1.13.0",
        "v1.12.0",
    ]


def test_when_every_version_failed_lately_a_poll_asks_only_once() -> None:
    """A refused key or a VAO outage hits every version: one budget slot per poll."""
    state = VersionState(
        version="v1.12.0", failed={"v1.12.0": WITHDRAWN, "v1.11.0": WITHDRAWN}
    )
    order = vao_version.candidates(
        _listed(LIST_AFTER), state, default="x", now=WITHDRAWN + timedelta(minutes=30)
    )
    assert order == ["v1.12.0"]


def test_without_a_list_the_version_that_worked_last_is_asked() -> None:
    now = WITHDRAWN
    assert vao_version.candidates(None, VersionState(), default="v1.12.0", now=now) == ["v1.12.0"]
    assert vao_version.candidates(
        None, VersionState(version="v1.11.0"), default="v1.12.0", now=now
    ) == ["v1.11.0"]


def test_record_outcome_switches_only_on_a_working_answer() -> None:
    state = VersionState(version="v1.13.0")
    vao_version.record_outcome(state, version="v1.13.0", worked=False, now=WITHDRAWN,
                               listed=_listed(LIST_BEFORE))
    assert state.version == "v1.13.0" and state.failed == {"v1.13.0": WITHDRAWN}
    vao_version.record_outcome(state, version="v1.12.0", worked=True, now=WITHDRAWN,
                               listed=_listed(LIST_BEFORE))
    assert (state.version, state.previous, state.changed_at) == ("v1.12.0", "v1.13.0", WITHDRAWN)
    assert state.listed == ["v1.13.0", "v1.12.0", "v1.11.0"]


def test_state_round_trips_and_a_garbled_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "vao_version.json"
    state = VersionState(version="v1.12.0", previous="v1.13.0", changed_at=WITHDRAWN,
                         checked_at=WITHDRAWN, listed=["v1.12.0"], failed={"v1.13.0": WITHDRAWN})
    assert vao_version.save_state(state, path)
    assert vao_version.load_state(path) == state
    path.write_text(json.dumps({"version": "../x", "failed": {"v1": "gestern"}}), encoding="utf-8")
    assert vao_version.load_state(path) == VersionState()


# --- the monitor -----------------------------------------------------------------


class _Vao:
    """A VAO whose versions answer as configured; counts the budget slots spent."""

    def __init__(self, working: set[str]) -> None:
        self.working = working
        self.asked: list[str] = []

    def tick(self, *_a: Any, **kwargs: Any) -> str:
        version = vor_provider.VOR_VERSION
        self.asked.append(version)
        tally = kwargs["tally"]
        if version in self.working:
            tally.departures = 25
            return "ok"
        tally.error = "ValueError: Content-Type text/html not allowed"
        return "error"


@pytest.fixture()
def vao(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("VOR_BASE_URL", "VOR_BASE", "VOR_VERSION", "VOR_VERSIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vor_provider, "VOR_VERSION", vor_provider.VOR_VERSION)
    monkeypatch.setattr(vor_provider, "VOR_BASE_URL", vor_provider.VOR_BASE_URL)

    def make(working: set[str], listed: list[dict[str, str]] | None) -> _Vao:
        fake = _Vao(working)
        monkeypatch.setattr(monitor, "_process_tick", fake.tick)
        monkeypatch.setattr(
            vor_provider, "fetch_version_list", lambda *_a, **_kw: _listed(listed) if listed else None
        )
        return fake

    return make


def _poll(when: datetime) -> tuple[str, monitor._TickTally]:
    tally = monitor._TickTally()
    result = monitor._poll_listed_versions(
        cast(requests.Session, object()), {}, when=when, recently_finalised=None, tally=tally
    )
    return result, tally


def test_replay_28_10_the_poll_after_the_withdrawal_works(vao: Any) -> None:
    """14:30 on v1.13.0, then the VAO drops it: 15:00 asks v1.12.0 and works."""
    fake = vao({"v1.13.0", "v1.12.0", "v1.11.0"}, LIST_BEFORE)
    assert _poll(WITHDRAWN - timedelta(minutes=30))[0] == "ok"
    fake.working.discard("v1.13.0")
    vao_listed_after = vao(fake.working, LIST_AFTER)
    result, tally = _poll(WITHDRAWN)
    assert result == "ok" and tally.error == ""
    assert fake.asked == ["v1.13.0"] and vao_listed_after.asked == ["v1.12.0"]
    state = vao_version.load_state()
    assert (state.version, state.previous, state.changed_at) == ("v1.12.0", "v1.13.0", WITHDRAWN)
    assert "v1.12.0/" in vor_provider.VOR_BASE_URL


def test_a_listed_but_broken_version_falls_back_in_the_same_poll(vao: Any) -> None:
    fake = vao({"v1.12.0", "v1.11.0"}, LIST_BEFORE)
    result, tally = _poll(WITHDRAWN)
    assert result == "ok" and tally.departures == 25 and tally.error == ""
    assert fake.asked == ["v1.13.0", "v1.12.0"]
    assert _poll(WITHDRAWN + timedelta(minutes=30))[0] == "ok"
    assert fake.asked == ["v1.13.0", "v1.12.0", "v1.12.0"]  # 1.13 waits a day
    _poll(WITHDRAWN + timedelta(days=1))
    assert fake.asked[-2:] == ["v1.13.0", "v1.12.0"]  # tried again once a day


def test_a_fault_of_every_version_costs_one_slot_per_poll(vao: Any) -> None:
    fake = vao(set(), LIST_AFTER)
    assert _poll(WITHDRAWN)[0] == "error"
    assert fake.asked == ["v1.12.0", "v1.11.0"]
    for minutes in (30, 60, 90):
        assert _poll(WITHDRAWN + timedelta(minutes=minutes))[0] == "error"
    assert fake.asked[2:] == ["v1.12.0", "v1.12.0", "v1.12.0"]


def test_the_newest_version_is_taken_once_the_vao_lists_it(vao: Any) -> None:
    vao_version.save_state(VersionState(version="v1.12.0"))
    fake = vao({"v1.13.0", "v1.12.0"}, LIST_BEFORE)
    assert _poll(WITHDRAWN)[0] == "ok"
    assert fake.asked == ["v1.13.0"]
    assert vao_version.load_state().previous == "v1.12.0"


def test_without_a_list_the_stored_version_is_polled(vao: Any) -> None:
    vao_version.save_state(VersionState(version="v1.11.0"))
    fake = vao({"v1.11.0"}, None)
    assert _poll(WITHDRAWN)[0] == "ok"
    assert fake.asked == ["v1.11.0"]


def test_a_version_pinned_by_the_environment_is_polled_as_it_is(
    vao: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = vao(set(), LIST_AFTER)
    monkeypatch.setenv("VOR_VERSION", "v1.9.0")
    assert _poll(WITHDRAWN)[0] == "error"
    assert len(fake.asked) == 1
    assert vao_version.load_state() == VersionState()


def test_a_spent_budget_stops_without_blaming_the_version(
    vao: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    vao(set(), LIST_AFTER)
    calls: list[str] = []

    def quota(*_a: Any, **_kw: Any) -> str:
        calls.append(vor_provider.VOR_VERSION)
        return "quota_exceeded"

    monkeypatch.setattr(monitor, "_process_tick", quota)
    assert _poll(WITHDRAWN)[0] == "quota_exceeded"
    assert calls == ["v1.12.0"]
    assert vao_version.load_state().failed == {}


def test_the_real_tick_asks_the_chosen_version_and_charges_each_attempt(
    vao: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through ``_process_tick``: the request URL carries the version."""
    vao(set(), LIST_BEFORE)
    monkeypatch.setattr(monitor, "_process_tick", _REAL_TICK)
    charged: list[datetime] = []
    monkeypatch.setattr(monitor, "_charge_one_request", charged.append)
    urls: list[str] = []

    def board(*_a: Any, **_kw: Any) -> list[Any]:
        urls.append(f"{vor_provider.VOR_BASE_URL}departureBoard")
        if "v1.13.0" in vor_provider.VOR_BASE_URL:
            raise ValueError("Content-Type text/html not allowed")
        return []

    monkeypatch.setattr(monitor, "_query_departure_board", board)
    monitor._BREAKER.reset()
    try:
        result, tally = _poll(WITHDRAWN)
    finally:
        monitor._BREAKER.reset()
    assert result == "ok" and tally.error == ""
    assert [url.rsplit("/", 2)[-2] for url in urls] == ["v1.13.0", "v1.12.0"]
    assert len(charged) == 2


# --- the list request ------------------------------------------------------------


def test_the_list_request_carries_no_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Response:
        content = json.dumps(LIST_AFTER).encode()

    def fake_request(session: requests.Session, url: str, **kwargs: Any) -> _Response:
        seen["url"], seen["auth"], seen["kwargs"] = url, session.auth, kwargs
        return _Response()

    monkeypatch.setattr(vor_provider, "request_safe", fake_request)
    listed = _REAL_FETCH()
    assert listed is not None and [item.version for item in listed] == ["v1.12.0", "v1.11.0"]
    assert seen["url"] == "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
    assert seen["auth"] is None and "params" not in seen["kwargs"]

    def broken(*_a: Any, **_kw: Any) -> Any:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(vor_provider, "request_safe", broken)
    assert _REAL_FETCH() is None


def test_the_ci_probe_asks_the_stored_version(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VOR_BASE_URL", "VOR_BASE", "VOR_VERSION", "VOR_VERSIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vor_provider, "VOR_VERSION", vor_provider.VOR_VERSION)
    monkeypatch.setattr(vor_provider, "VOR_BASE_URL", vor_provider.VOR_BASE_URL)
    vao_version.save_state(VersionState(version="v1.11.0"))
    assert vor_provider.use_stored_version() == "v1.11.0"
    assert vor_provider.VOR_BASE_URL.endswith("/restproxy/v1.11.0/")
    for script in ("verify_vor_access_id.py", "check_vor_auth.py"):
        source = (Path(__file__).resolve().parents[1] / "scripts" / script).read_text(encoding="utf-8")
        assert "vor_module.use_stored_version()" in source, script


# --- the Feed-Wächter ------------------------------------------------------------


def test_the_watcher_reports_a_change_in_two_runs() -> None:
    state = VersionState(version="v1.12.0", previous="v1.13.0", changed_at=WITHDRAWN,
                         listed=["v1.12.0", "v1.11.0"])
    first = feed_watch.check_vao_version(state, WITHDRAWN + timedelta(minutes=3))
    assert [f.rule_id for f in first] == ["vao-version"]
    assert "v1.13.0 → v1.12.0 am 28.10. 15:00" in first[0].text
    assert feed_watch.check_vao_version(state, WITHDRAWN + timedelta(minutes=33))
    assert not feed_watch.check_vao_version(state, WITHDRAWN + timedelta(minutes=63))
    assert "vao-version" in feed_watch.RULES


def test_the_watcher_stays_silent_on_the_first_choice() -> None:
    first_choice = VersionState(version="v1.12.0", changed_at=WITHDRAWN)
    assert not feed_watch.check_vao_version(first_choice, WITHDRAWN)
    assert not feed_watch.check_vao_version(None, WITHDRAWN)
