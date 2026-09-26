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


def _loc(name: str, ext_id: str, classes: int, lat: float, lon: float) -> dict[str, Any]:
    return {"name": name, "extId": ext_id, "pCls": classes, "crd": {"x": round(lon * 1e6), "y": round(lat * 1e6)}}


# Hütteldorf as HAFAS answered in the probe (pCls 4479, ~110 m from stations.json).
RAIL_HUETTELDORF = _loc("Hütteldorf (Wien)", "1191401", 4479, 48.197391, 16.261073)
# A same-named stop that serves only tram (512) and bus (64).
TRAM_HUETTELDORF = _loc("Wien Hütteldorf (Straßenbahn)", "1391999", 512 | 64, 48.1966, 16.2622)


def _loc_match(*locations: object, err: str = "OK") -> dict[str, Any]:
    return {"svcResL": [{"meth": "LocMatch", "err": err, "res": {"match": {"locL": list(locations)}}}]}


def _post(loc_match: object, board: object, calls: list[Any] | None = None) -> Any:
    def post(service_requests: list[Any], **kwargs: Any) -> object:
        request = service_requests[0]
        if calls is not None:
            calls.append((request, kwargs))
        return loc_match if request["meth"] == "LocMatch" else board

    return post


# A stop resolved under the current rule, with a line: not looked up again.
RESOLVED = {"hafas_ext_id": "1191401", "hafas_classes": 4479, "lines": {"S45": "2026-09-20"}}


def test_the_nearest_rail_candidate_is_chosen() -> None:
    (station,) = ul.select_stations([HUETTELDORF])
    far_rail = _loc("Hütteldorf Nord", "1191499", 32, 48.2020, 16.2620)  # ~630 m
    chosen = ul.pick_rail_location(_loc_match(TRAM_HUETTELDORF, far_rail, RAIL_HUETTELDORF), station)
    assert chosen is not None
    assert (chosen.ext_id, chosen.name, chosen.classes) == ("1191401", "Hütteldorf (Wien)", 4479)
    assert 50 < chosen.distance_m < 200


def test_a_tram_stop_of_the_same_name_is_not_a_railway_station() -> None:
    # The first run's bug: Wien Mitte-Landstraße, Rennweg and Quartier
    # Belvedere resolved to a same-named non-rail stop, their rail boards
    # came back empty.
    (station,) = ul.select_stations([HUETTELDORF])
    assert ul.pick_rail_location(_loc_match(TRAM_HUETTELDORF), station) is None


def test_a_bus_terminal_with_an_ic_class_is_not_the_station() -> None:
    # Run of 2026-09-26: "Flughafen Wien Busterminal" (pCls 1090: IC/EC,
    # bus, 1024) lay 15 m from the station and won under the any-rail rule;
    # its boards showed only "CAT by bus".
    airport = {**HUETTELDORF, "bst_id": "528", "name": "Flughafen Wien", "latitude": 48.1209, "longitude": 16.5634}
    terminal = _loc("Flughafen Wien Busterminal", "1332474", 1090, 48.1210, 16.5635)
    station_stop = _loc("Flughafen Wien Bahnhof", "1293001", 63, 48.1204, 16.5626)
    (station,) = ul.select_stations([airport])
    chosen = ul.pick_rail_location(_loc_match(terminal, station_stop), station)
    assert chosen is not None and chosen.ext_id == "1293001"


@pytest.mark.parametrize(("classes", "counts"), [(16, True), (32, True), (1 | 2 | 4 | 8 | 4096, False)])
def test_only_regional_rail_and_s_bahn_count(classes: int, counts: bool) -> None:
    (station,) = ul.select_stations([HUETTELDORF])
    chosen = ul.pick_rail_location(_loc_match({**RAIL_HUETTELDORF, "pCls": classes}), station)
    assert (chosen is not None) is counts


def test_a_railway_station_too_far_away_is_rejected() -> None:
    karlsplatz = {**HUETTELDORF, "bst_id": "900101", "name": "Wien Karlsplatz", "latitude": 48.2008, "longitude": 16.3694}
    rennweg = _loc("Wien Rennweg", "1290303", 32, 48.19465, 16.386478)  # 1.4 km away
    (station,) = ul.select_stations([karlsplatz])
    assert ul.pick_rail_location(_loc_match(rennweg), station) is None


@pytest.mark.parametrize(
    "location",
    [
        {**RAIL_HUETTELDORF, "extId": ""},
        {**RAIL_HUETTELDORF, "pCls": "4479"},
        {**RAIL_HUETTELDORF, "pCls": True},
        {**RAIL_HUETTELDORF, "crd": {"x": "16261073", "y": 48197391}},
        {**RAIL_HUETTELDORF, "crd": {"x": True, "y": 48197391}},
        {**RAIL_HUETTELDORF, "crd": {"x": 16261073, "y": 480_000_000_000}},
        {k: v for k, v in RAIL_HUETTELDORF.items() if k != "crd"},
        "junk",
    ],
)
def test_malformed_candidates_are_skipped(location: object) -> None:
    (station,) = ul.select_stations([HUETTELDORF])
    assert ul.pick_rail_location(_loc_match(location), station) is None


def test_no_match_without_station_coordinates() -> None:
    (station,) = ul.select_stations([{**HUETTELDORF, "latitude": None}])
    assert ul.pick_rail_location(_loc_match(RAIL_HUETTELDORF), station) is None


def test_refresh_resolves_the_station_and_records_its_lines() -> None:
    calls: list[Any] = []
    state: dict[str, Any] = {}
    stations = ul.select_stations([HUETTELDORF])
    post = _post(_loc_match(TRAM_HUETTELDORF, RAIL_HUETTELDORF), _board([S45, REX51, SEV_BUS]), calls)
    result = ul.refresh(stations, state, date(2026, 9, 27), post=post, pause=0)
    assert (result.checked, result.failed, result.unresolved, result.aborted) == (1, 0, 0, False)
    assert state["804"] == {
        "name": "Wien Hütteldorf",
        "hafas_ext_id": "1191401",
        "hafas_name": "Hütteldorf (Wien)",
        "hafas_classes": 4479,
        "hafas_distance_m": state["804"]["hafas_distance_m"],
        "lines": {"REX51": "2026-09-27", "S45": "2026-09-27"},
        "checked": "2026-09-27",
    }
    loc_match_request, _ = calls[0]
    assert loc_match_request["req"]["input"]["maxLoc"] == ul.LOC_MATCH_CANDIDATES
    boards = calls[1:]
    # two dates × two windows, each capped, on the chosen rail stop
    assert [(c[0]["req"]["date"], c[0]["req"]["time"]) for c in boards] == [
        ("20260929", "060000"),
        ("20260929", "150000"),
        ("20261103", "060000"),
        ("20261103", "150000"),
    ]
    assert all(c[0]["req"]["stbLoc"]["lid"] == "A=1@L=1191401@" for c in boards)
    assert all(c[1]["max_bytes"] == ul.BOARD_MAX_BYTES for c in boards)


def test_a_known_hafas_id_is_not_looked_up_again() -> None:
    calls: list[Any] = []
    state: dict[str, Any] = {"804": {**RESOLVED, "lines": {"S80": "2026-09-20"}}}
    post = _post(pytest.fail, _board([S45]), calls)
    ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert all(c[0]["meth"] == "StationBoard" for c in calls)
    # S80 is not on today's boards (Ersatzverkehr) but was seen a week ago: it stays.
    assert state["804"]["lines"] == {"S45": "2026-09-27", "S80": "2026-09-20"}


def test_an_id_from_the_first_run_is_resolved_again() -> None:
    # The first run stored the top hit by name without checking its classes.
    state: dict[str, Any] = {"804": {"hafas_ext_id": "1391999", "lines": {"S45": "2026-09-25"}}}
    calls: list[Any] = []
    post = _post(_loc_match(TRAM_HUETTELDORF, RAIL_HUETTELDORF), _board([S45]), calls)
    ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert calls[0][0]["meth"] == "LocMatch"
    assert (state["804"]["hafas_ext_id"], state["804"]["hafas_classes"]) == ("1191401", 4479)


def test_a_stop_from_the_any_rail_rule_is_resolved_again() -> None:
    # The bus terminal (1090) was stored with lines from an earlier match.
    state: dict[str, Any] = {
        "804": {"hafas_ext_id": "1332474", "hafas_classes": 1090, "lines": {"S7": "2026-09-25"}}
    }
    calls: list[Any] = []
    post = _post(_loc_match(RAIL_HUETTELDORF), _board([S45]), calls)
    ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert calls[0][0]["meth"] == "LocMatch"
    assert state["804"]["hafas_ext_id"] == "1191401"


def test_a_station_without_lines_is_looked_up_again() -> None:
    calls: list[Any] = []
    state: dict[str, Any] = {"804": {**RESOLVED, "lines": {}}}
    post = _post(_loc_match(RAIL_HUETTELDORF), _board([S45]), calls)
    ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert [c[0]["meth"] for c in calls] == ["LocMatch"] + ["StationBoard"] * 4
    assert state["804"]["lines"] == {"S45": "2026-09-27"}


def test_a_failed_lookup_keeps_a_known_stop() -> None:
    calls: list[Any] = []
    state: dict[str, Any] = {"804": {**RESOLVED, "lines": {}}}
    post = _post(_loc_match(err="FAIL"), _board([S45]), calls)
    result = ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert (result.checked, result.failed, result.unresolved) == (1, 0, 0)
    assert state["804"]["hafas_ext_id"] == "1191401"
    assert all(c[0]["req"]["stbLoc"]["lid"] == "A=1@L=1191401@" for c in calls[1:])


def test_a_failed_lookup_without_a_stop_counts_as_failed() -> None:
    post = _post(_loc_match(err="FAIL"), pytest.fail)
    result = ul.refresh(ul.select_stations([HUETTELDORF]), {}, date(2026, 9, 27), post=post, pause=0)
    assert (result.checked, result.failed, result.unresolved) == (0, 1, 0)


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Wien Hauptbahnhof", "Wien Hbf"),
        ("St. Pölten Hauptbahnhof", "St. Pölten Hbf"),
        ("Wien Meidling", None),
        ("Wien Hauptbahnhofstraße", None),
    ],
)
def test_short_name(name: str, short: str | None) -> None:
    assert ul.short_name(name) == short


HAUPTBAHNHOF = {
    **HUETTELDORF,
    "bst_id": "900100",
    "name": "Wien Hauptbahnhof",
    "latitude": 48.186116,
    "longitude": 16.374399,
}
# What "Wien Hauptbahnhof" offered on 2026-09-26: big stations elsewhere.
MEIDLING = _loc("Meidling (Wien)", "1191201", 4991, 48.174, 16.334)  # 3.3 km away
HBF = _loc("Wien Hbf (U)", "1290401", 4991, 48.1851, 16.3762)


def _loc_match_by_name(answers: dict[str, object], calls: list[Any]) -> Any:
    def post(service_requests: list[Any], **_kwargs: Any) -> object:
        request = service_requests[0]
        if request["meth"] != "LocMatch":
            return _board([S45])
        query = request["req"]["input"]["loc"]["name"]
        calls.append(query)
        return answers[query]

    return post


def test_the_hauptbahnhof_is_found_by_its_short_name(caplog: pytest.LogCaptureFixture) -> None:
    queries: list[str] = []
    post = _loc_match_by_name({"Wien Hauptbahnhof": _loc_match(MEIDLING), "Wien Hbf": _loc_match(MEIDLING, HBF)}, queries)
    state: dict[str, Any] = {}
    with caplog.at_level("INFO", logger="oebb_station_lines"):
        result = ul.refresh(ul.select_stations([HAUPTBAHNHOF]), state, date(2026, 9, 27), post=post, pause=0)
    assert queries == ["Wien Hauptbahnhof", "Wien Hbf"]
    assert (result.checked, result.unresolved) == (1, 0)
    assert state["900100"]["hafas_ext_id"] == "1290401"
    assert "No rail stop within 800 m for Wien Hauptbahnhof; candidates: Meidling (Wien)" in caplog.text
    assert "Wien Hauptbahnhof (as Wien Hbf) → Wien Hbf (U) (1290401, pCls 4991, " in caplog.text


def test_another_towns_hauptbahnhof_is_never_taken() -> None:
    # "Wien Hbf" keeps the town, and the radius is measured from Wien
    # Hauptbahnhof's own coordinates: St. Pölten Hbf, 56 km away, never counts.
    st_poelten = _loc("St.Pölten Hbf", "1130165", 4991, 48.2079, 15.6243)
    queries: list[str] = []
    post = _loc_match_by_name(
        {"Wien Hauptbahnhof": _loc_match(MEIDLING), "Wien Hbf": _loc_match(st_poelten)}, queries
    )
    state: dict[str, Any] = {}
    result = ul.refresh(ul.select_stations([HAUPTBAHNHOF]), state, date(2026, 9, 27), post=post, pause=0)
    assert result.unresolved == 1
    assert "hafas_ext_id" not in state["900100"]


def test_under_the_short_name_only_a_hbf_counts() -> None:
    # Quartier Belvedere lies 526 m from Wien Hauptbahnhof, inside the radius.
    # Found by "Wien Hbf" without the Hbf itself, it must not become the Hbf.
    belvedere = _loc("Wien Quartier Belvedere Bahnhst", "8101473", 608, 48.1909, 16.3771)
    queries: list[str] = []
    post = _loc_match_by_name(
        {"Wien Hauptbahnhof": _loc_match(MEIDLING), "Wien Hbf": _loc_match(belvedere)}, queries
    )
    state: dict[str, Any] = {}
    result = ul.refresh(ul.select_stations([HAUPTBAHNHOF]), state, date(2026, 9, 27), post=post, pause=0)
    assert result.unresolved == 1
    assert "hafas_ext_id" not in state["900100"]
    # The full name is not held to it: there the name already matched.
    (station,) = ul.select_stations([HAUPTBAHNHOF])
    assert ul.pick_rail_location(_loc_match(belvedere), station) is not None


def test_the_short_name_is_not_queried_after_a_match_or_a_failure() -> None:
    queries: list[str] = []
    post = _loc_match_by_name({"Wien Hauptbahnhof": _loc_match(HBF)}, queries)
    ul.refresh(ul.select_stations([HAUPTBAHNHOF]), {}, date(2026, 9, 27), post=post, pause=0)
    assert queries == ["Wien Hauptbahnhof"]

    queries.clear()
    post = _loc_match_by_name({"Wien Hauptbahnhof": _loc_match(err="FAIL")}, queries)
    result = ul.refresh(ul.select_stations([HAUPTBAHNHOF]), {}, date(2026, 9, 27), post=post, pause=0)
    assert queries == ["Wien Hauptbahnhof"]
    assert result.failed == 1


def test_without_a_rail_match_the_old_id_is_forgotten() -> None:
    state: dict[str, Any] = {"804": {"hafas_ext_id": "1391999", "lines": {"S45": "2026-09-25"}}}
    post = _post(_loc_match(TRAM_HUETTELDORF), pytest.fail)
    result = ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert result.unresolved == 1
    assert "hafas_ext_id" not in state["804"]
    assert state["804"]["lines"] == {"S45": "2026-09-25"}  # kept until the retention expires


def test_a_station_without_answers_keeps_its_lines() -> None:
    def post(*_a: Any, **_k: Any) -> object:
        raise requests.ConnectionError("down")

    state: dict[str, Any] = {"804": {**RESOLVED, "lines": {"S45": "2026-09-20"}}}
    result = ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert result.failed == 1
    assert state["804"]["lines"] == {"S45": "2026-09-20"}
    assert "checked" not in state["804"]


def test_consecutive_failures_stop_the_run() -> None:
    calls: list[Any] = []
    stations = ul.select_stations(
        [HUETTELDORF, {**HUETTELDORF, "bst_id": "805"}, {**HUETTELDORF, "bst_id": "806"}]
    )
    state = {s.bst_id: dict(RESOLVED) for s in stations}
    post = _post(None, _board([], err="FAIL"), calls)
    result = ul.refresh(stations, state, date(2026, 9, 27), post=post, pause=0)
    assert result.aborted
    assert len(calls) == ul.MAX_CONSECUTIVE_FAILURES


def test_failed_loc_matches_count_as_failures() -> None:
    calls: list[Any] = []
    stations = ul.select_stations([{**HUETTELDORF, "bst_id": str(n)} for n in range(800, 810)])
    post = _post(_loc_match(err="FAIL"), pytest.fail, calls)
    result = ul.refresh(stations, {}, date(2026, 9, 27), post=post, pause=0)
    assert result.aborted
    assert len(calls) == ul.MAX_CONSECUTIVE_FAILURES


def test_failures_between_answers_do_not_stop_the_run() -> None:
    # Every station: the first date answers, the second (past the timetable
    # horizon, say) fails twice. Six failures, never five in a row.
    answers = iter([_board([S45]), _board([S45]), _board([], err="FAIL"), _board([], err="FAIL")] * 3)
    stations = ul.select_stations(
        [HUETTELDORF, {**HUETTELDORF, "bst_id": "805"}, {**HUETTELDORF, "bst_id": "806"}]
    )
    state = {s.bst_id: dict(RESOLVED) for s in stations}
    result = ul.refresh(stations, state, date(2026, 9, 27), post=lambda *_a, **_k: next(answers), pause=0)
    assert (result.aborted, result.checked) == (False, 3)


def test_stations_out_of_scope_are_dropped() -> None:
    state: dict[str, Any] = {"999": {"lines": {"S1": "2026-09-20"}}}
    ul.refresh([], state, date(2026, 9, 27), post=lambda *_a, **_k: None, pause=0)
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


def test_describe_candidates_lists_what_hafas_offered() -> None:
    (station,) = ul.select_stations([HUETTELDORF])
    no_coords = {"name": "Irgendwo", "extId": "1", "pCls": 64}
    text = ul.describe_candidates(_loc_match(TRAM_HUETTELDORF, no_coords, "junk"), station)
    assert text.startswith("Wien Hütteldorf (Straßenbahn) (1391999, pCls 576, ")
    assert " m); Irgendwo (1, pCls 64, ?)" in text
    bool_coords = {**TRAM_HUETTELDORF, "crd": {"x": True, "y": 48197391}}
    assert ul.describe_candidates(_loc_match(bool_coords), station).endswith("pCls 576, ?)")
    assert ul.describe_candidates(_loc_match(), station) == "no candidates"
    assert ul.describe_candidates(_loc_match(err="FAIL"), station) == "no candidates"


def test_board_summary() -> None:
    assert ul.board_summary(_board([], err="FAIL")) == "failed"
    assert ul.board_summary({"svcResL": [{"err": "OK", "res": {}}]}) == "jny 0, prod 0"
    summary = ul.board_summary(_board([S45, RJ820], journeys=2))
    assert summary == "jny 2, prod 2: S 45 [S/45/at:obb:vor|S45:], RJ 820 [RJ//]"
    many = ul.board_summary(_board([S45] * 9))
    assert many.startswith("jny 3, prod 9: ") and many.count("S 45") == ul.MAX_LOGGED_PRODUCTS


def test_an_unresolved_station_logs_the_candidates(caplog: pytest.LogCaptureFixture) -> None:
    post = _post(_loc_match(TRAM_HUETTELDORF), pytest.fail)
    with caplog.at_level("INFO", logger="oebb_station_lines"):
        ul.refresh(ul.select_stations([HUETTELDORF]), {}, date(2026, 9, 27), post=post, pause=0)
    assert "No rail stop within 800 m for Wien Hütteldorf; candidates: Wien Hütteldorf (Straßenbahn)" in caplog.text


def test_boards_without_a_line_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, Any] = {"804": dict(RESOLVED)}
    post = _post(None, _board([RJ820], journeys=4))
    with caplog.at_level("INFO", logger="oebb_station_lines"):
        ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert "No line on the boards of Wien Hütteldorf: 29.09. 06h jny 4, prod 1: RJ 820 [RJ//]" in caplog.text
    assert caplog.text.count(" | ") == 3  # four windows


def test_boards_without_a_line_after_a_lookup_log_the_candidates(caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, Any] = {"804": {**RESOLVED, "lines": {}}}
    post = _post(_loc_match(RAIL_HUETTELDORF, TRAM_HUETTELDORF), _board([], journeys=0))
    with caplog.at_level("INFO", logger="oebb_station_lines"):
        ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=post, pause=0)
    assert "03.11. 15h jny 0, prod 0; candidates: Hütteldorf (Wien) (1191401, pCls 4479, " in caplog.text
    assert "Wien Hütteldorf (Straßenbahn) (1391999, pCls 576, " in caplog.text


def test_boards_with_lines_log_nothing_extra(caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, Any] = {"804": dict(RESOLVED)}
    with caplog.at_level("INFO", logger="oebb_station_lines"):
        ul.refresh(ul.select_stations([HUETTELDORF]), state, date(2026, 9, 27), post=_post(None, _board([S45])), pause=0)
    assert "No line on the boards" not in caplog.text
