"""Stage 2 of the line check: the rail lines of each ÖBB station, via HAFAS.

The product shapes below are the ones the probe run of 2026-09-25 logged for
Wien Hütteldorf and Wien Meidling (``prodCtx`` fields, ``lineId``).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import requests

from scripts import update_oebb_station_lines as ul
from src.places.hafas_client import HafasLocation


def _product(name: str, cls: int, **context: str) -> dict[str, Any]:
    return {"name": name, "nameS": name, "cls": cls, "prodCtx": context}


S45 = _product("S 45", 32, line="45", lineId="at:obb:vor|S45:", catOut="S       ", catOutL="S-Bahn")
REX51 = _product("REX 51", 16, line="51", lineId="at:obb:vor|REX51:", catOut="REX     ", catOutL="RegionalExpress")
CJX5 = _product("CJX 5", 16, line="5", lineId="at:obb:vor|CJX5:", catOut="CJX     ")
R23_NO_ID = _product("R 23", 16, line="23", catOut="R       ")
RJ820 = _product("RJ 820", 1, catOut="RJ      ", catOutL="railjet")
WB900 = _product("WB 900", 4096, catOut="WB      ", catOutL="WESTbahn")
SEV_BUS = _product("BusSV910", 2, line="SV910", catOut="Bus      ", catOutL="Schienenersatzverkehr")
# A replacement bus carrying the replaced line's id is still no train of that line.
SEV_S80 = _product("Bus S80", 2, line="80", lineId="at:obb:vor|S80:", catOut="Bus", catOutL="Schienenersatzverkehr")
SEV_S80_AS_RAIL = _product("S 80", 32, line="80", lineId="at:obb:vor|S80:", catOut="S", catOutL="Schienenersatzverkehr")
CLASS_ONLY = {"name": "", "cls": 32}


def _board(products: list[Any], journeys: int = 3, err: str = "OK") -> dict[str, Any]:
    return {
        "svcResL": [
            {
                "meth": "StationBoard",
                "err": err,
                "res": {"jnyL": [{}] * journeys, "common": {"prodL": products}},
            }
        ]
    }


HUETTELDORF = {
    "bst_id": "804",
    "name": "Wien Hütteldorf",
    "in_vienna": True,
    "pendler": False,
    "latitude": 48.1964971,
    "longitude": 16.2619551,
}


@pytest.mark.parametrize(
    ("product", "token"),
    [
        (S45, "S45"),
        (REX51, "REX51"),
        (CJX5, "CJX5"),
        (R23_NO_ID, "R23"),  # no lineId: category + line
        (RJ820, None),  # long distance: a train number, not a line
        (WB900, None),
        (SEV_BUS, None),  # rail replacement bus
        (SEV_S80, None),
        (SEV_S80_AS_RAIL, None),
        (CLASS_ONLY, None),
        ("S 45", None),
    ],
)
def test_line_token(product: object, token: str | None) -> None:
    assert ul.line_token(product) == token


def test_lines_from_board() -> None:
    board = _board([CLASS_ONLY, S45, dict(S45), REX51, RJ820, SEV_BUS])
    assert ul.lines_from_board(board) == {"S45", "REX51"}


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"svcResL": []}, _board([S45], err="PARSE"), _board([S45], err="LOCATION")],
)
def test_a_failed_board_is_none(payload: object) -> None:
    assert ul.lines_from_board(payload) is None


def test_an_empty_board_is_an_answer() -> None:
    assert ul.lines_from_board({"svcResL": [{"err": "OK", "res": {}}]}) == set()


def test_a_capped_board_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    ul.lines_from_board(_board([S45], journeys=ul.MAX_JOURNEYS))
    assert "maxJny" in caplog.text


def test_the_board_request_form() -> None:
    request: Any = ul.board_request("1191401", date(2026, 9, 29), "060000", 180)
    assert request == {
        "meth": "StationBoard",
        "req": {
            "type": "DEP",
            "date": "20260929",
            "time": "060000",
            "stbLoc": {"type": "S", "lid": "A=1@L=1191401@"},
            "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": "4159"}],
            "dur": 180,
            "maxJny": ul.MAX_JOURNEYS,
        },
    }
    assert "getPasslist" not in request["req"]  # ÖBB answers PARSE to it


@pytest.mark.parametrize(
    ("today", "dates"),
    [
        (date(2026, 9, 27), (date(2026, 9, 29), date(2026, 11, 3))),  # Sunday
        (date(2026, 9, 29), (date(2026, 10, 6), date(2026, 11, 10))),  # a Tuesday: next week
    ],
)
def test_sample_dates(today: date, dates: tuple[date, date]) -> None:
    assert ul.sample_dates(today) == dates


def test_select_stations() -> None:
    entries: list[object] = [
        HUETTELDORF,
        {**HUETTELDORF, "bst_id": "11", "name": "Achau", "in_vienna": False, "pendler": True},
        {**HUETTELDORF, "bst_id": "999", "in_vienna": False, "pendler": False},  # outside scope
        {**HUETTELDORF, "bst_id": None},  # a WL stop
        {**HUETTELDORF, "bst_id": "900100", "type": "manual_distant_at"},
        "junk",
    ]
    assert [s.bst_id for s in ul.select_stations(entries)] == ["804", "11"]


def test_merge_lines_stamps_and_expires() -> None:
    today = date(2026, 9, 29)
    known = {"S45": "2026-09-01", "S80": "2026-08-04", "REX7": "2026-08-03", "X": "garbage"}
    merged = ul.merge_lines(known, {"S50"}, today)
    # S80 is exactly RETENTION_DAYS old and stays; REX7 is one day older and goes.
    assert merged == {"S45": "2026-09-01", "S50": "2026-09-29", "S80": "2026-08-04"}


def _locate(lat: float = 48.197391, lon: float = 16.261073) -> Any:
    def locate(_name: str) -> HafasLocation | None:
        return HafasLocation(name="Hütteldorf (Wien)", extId="1191401", lat=lat, lon=lon)

    return locate


def test_refresh_resolves_the_station_and_records_its_lines() -> None:
    calls: list[Any] = []

    def post(service_requests: list[Any], **kwargs: Any) -> object:
        calls.append((service_requests[0]["req"], kwargs))
        return _board([S45, REX51, SEV_BUS])

    state: dict[str, Any] = {}
    stations = ul.select_stations([HUETTELDORF])
    result = ul.refresh(stations, state, date(2026, 9, 27), post=post, locate=_locate(), pause=0)
    assert (result.checked, result.failed, result.unresolved, result.aborted) == (1, 0, 0, False)
    assert state["804"] == {
        "name": "Wien Hütteldorf",
        "hafas_ext_id": "1191401",
        "lines": {"REX51": "2026-09-27", "S45": "2026-09-27"},
        "checked": "2026-09-27",
    }
    # two dates × two windows, each capped
    assert [(c[0]["date"], c[0]["time"]) for c in calls] == [
        ("20260929", "060000"),
        ("20260929", "150000"),
        ("20261103", "060000"),
        ("20261103", "150000"),
    ]
    assert all(c[1]["max_bytes"] == ul.BOARD_MAX_BYTES for c in calls)


def test_a_known_hafas_id_is_not_looked_up_again() -> None:
    def locate(_name: str) -> HafasLocation | None:
        raise AssertionError("LocMatch must not run again")

    state: dict[str, Any] = {"804": {"hafas_ext_id": "1191401", "lines": {"S80": "2026-09-20"}}}
    ul.refresh(
        ul.select_stations([HUETTELDORF]),
        state,
        date(2026, 9, 27),
        post=lambda *_a, **_k: _board([S45]),
        locate=locate,
        pause=0,
    )
    # S80 is not on today's boards (Ersatzverkehr) but was seen a week ago: it stays.
    assert state["804"]["lines"] == {"S45": "2026-09-27", "S80": "2026-09-20"}


def test_a_distant_match_is_rejected() -> None:
    state: dict[str, Any] = {}
    result = ul.refresh(
        ul.select_stations([HUETTELDORF]),
        state,
        date(2026, 9, 27),
        post=lambda *_a, **_k: pytest.fail("no board without a station id"),
        locate=_locate(lat=47.07, lon=15.43),  # a "Hütteldorf" 150 km away
        pause=0,
    )
    assert result.unresolved == 1
    assert "hafas_ext_id" not in state["804"]


def test_a_station_without_answers_keeps_its_lines() -> None:
    def post(*_a: Any, **_k: Any) -> object:
        raise requests.ConnectionError("down")

    state: dict[str, Any] = {"804": {"hafas_ext_id": "1191401", "lines": {"S45": "2026-09-20"}}}
    result = ul.refresh(
        ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, locate=_locate(), pause=0
    )
    assert result.failed == 1
    assert state["804"]["lines"] == {"S45": "2026-09-20"}
    assert "checked" not in state["804"]


def test_consecutive_failures_stop_the_run() -> None:
    calls = 0

    def post(*_a: Any, **_k: Any) -> object:
        nonlocal calls
        calls += 1
        return _board([], err="FAIL")

    stations = ul.select_stations(
        [HUETTELDORF, {**HUETTELDORF, "bst_id": "805"}, {**HUETTELDORF, "bst_id": "806"}]
    )
    state = {s.bst_id: {"hafas_ext_id": "1", "lines": {}} for s in stations}
    result = ul.refresh(stations, state, date(2026, 9, 27), post=post, locate=_locate(), pause=0)
    assert result.aborted
    assert calls == ul.MAX_CONSECUTIVE_FAILURES


def test_failures_between_answers_do_not_stop_the_run() -> None:
    # Every station: the first date answers, the second (past the timetable
    # horizon, say) fails twice. Six failures, never five in a row.
    answers = iter([_board([S45]), _board([S45]), _board([], err="FAIL"), _board([], err="FAIL")] * 3)
    stations = ul.select_stations(
        [HUETTELDORF, {**HUETTELDORF, "bst_id": "805"}, {**HUETTELDORF, "bst_id": "806"}]
    )
    state = {s.bst_id: {"hafas_ext_id": "1", "lines": {}} for s in stations}
    result = ul.refresh(
        stations, state, date(2026, 9, 27), post=lambda *_a, **_k: next(answers), locate=_locate(), pause=0
    )
    assert (result.aborted, result.checked) == (False, 3)


def test_stations_out_of_scope_are_dropped() -> None:
    state: dict[str, Any] = {"999": {"lines": {"S1": "2026-09-20"}}}
    ul.refresh([], state, date(2026, 9, 27), post=lambda *_a, **_k: None, locate=_locate(), pause=0)
    assert state == {}


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "oebb_station_lines.json"
    stations = {
        "804": {"name": "Wien Hütteldorf", "hafas_ext_id": "1191401", "lines": {"S45": "2026-09-27"}},
        "11": {"name": "Achau", "lines": {}},
    }
    ul.write_state(path, stations, date(2026, 9, 27))
    document = json.loads(path.read_text(encoding="utf-8"))
    assert list(document["stations"]) == ["11", "804"]  # numeric order
    assert (document["version"], document["updated"], document["retention_days"]) == (
        1,
        "2026-09-27",
        ul.RETENTION_DAYS,
    )
    assert "Wien Hütteldorf" in path.read_text(encoding="utf-8")
    assert ul.load_state(path) == stations


@pytest.mark.parametrize("content", ["", "[]", '{"stations": []}', "{not json"])
def test_a_broken_state_file_starts_empty(tmp_path: Path, content: str) -> None:
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert ul.load_state(path) == {}


def test_main_refuses_an_output_outside_the_repository(tmp_path: Path) -> None:
    # Matched by message, not class: other tests re-import src.feed.config, and
    # the script keeps the InvalidPathError of the copy it imported first.
    with pytest.raises(Exception, match="outside allowed directories"):
        ul.main(["--output", str(tmp_path / "x.json"), "--limit", "1"])
