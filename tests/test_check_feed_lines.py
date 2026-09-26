"""Stage 3 of the line check: the lines of the German feed against the directory.

The places and distances mirror the real directory: "Wien Bhf. Hütteldorf
(WL)" lies 126 m from the ÖBB station, "Meidling Hauptstraße" 1 km from
Bahnhof Meidling, and Pasettistraße (a temporary stop in a real 37A item)
is no railway station at all. Rennweg and Himberg are closed: HAFAS shows
no train there (2026-09-26), Rennweg has tram stops next to it, Himberg
nothing at all.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import check_feed_lines as cl

REPO = Path(__file__).resolve().parents[1]

HUETTELDORF = {
    "bst_id": "804",
    "name": "Wien Hütteldorf",
    "aliases": ["Hütteldorf", "Bf Hütteldorf", "Hf", "490056000"],
    "latitude": 48.1964971,
    "longitude": 16.2619551,
}
HUETTELDORF_WL = {
    "name": "Wien Bhf. Hütteldorf (WL)",
    "aliases": ["Bhf. Hütteldorf"],
    "wl_lines": ["U4", "49A"],
    "latitude": 48.1976,
    "longitude": 16.2616,  # ~126 m
}
# A stop of the same area but 300 m away: not part of the station.
HUETTELDORF_FAR = {
    "name": "Wien Hütteldorf, Bujattigasse (WL)",
    "aliases": ["Hütteldorf, Bujattigasse"],
    "wl_lines": ["99A"],
    "latitude": 48.1991,
    "longitude": 16.2619,
}
MEIDLING = {
    "bst_id": "1410",
    "name": "Wien Meidling",
    "aliases": ["Meidling", "Bahnhof Meidling"],
    "latitude": 48.17475,
    "longitude": 16.33392,
}
MEIDLING_HAUPTSTRASSE = {
    "name": "Wien Meidling Hauptstraße (WL)",
    "aliases": ["Bahnhof Meidling Hauptstraße"],
    "wl_lines": ["U4", "7A"],
    "latitude": 48.1837,
    "longitude": 16.3289,  # ~1 km from Bahnhof Meidling
}
PASETTISTRASSE = {
    "name": "Wien Pasettistraße (WL)",
    "aliases": ["Pasettistraße"],
    "wl_lines": ["5A"],
    "latitude": 48.2361,
    "longitude": 16.3858,
}
RENNWEG = {
    "bst_id": "1352",
    "name": "Wien Rennweg",
    "aliases": ["Rennweg", "Bahnhof Rennweg"],
    "latitude": 48.1968,
    "longitude": 16.3838,
}
RENNWEG_WL = {
    "name": "Wien Rennweg (WL)",
    "aliases": ["Rennweg (WL)"],
    "wl_lines": ["71", "O"],
    "latitude": 48.1972,
    "longitude": 16.3842,  # ~50 m
}
HIMBERG = {"bst_id": "835", "name": "Himberg", "aliases": ["Himberg"], "latitude": 48.0836, "longitude": 16.4401}
ENTRIES = [
    HUETTELDORF,
    HUETTELDORF_WL,
    HUETTELDORF_FAR,
    MEIDLING,
    MEIDLING_HAUPTSTRASSE,
    PASETTISTRASSE,
    RENNWEG,
    RENNWEG_WL,
    HIMBERG,
]
OEBB_LINES: dict[str, Any] = {
    "804": {"lines": {"S45": "2026-09-26", "S50": "2026-09-26"}},
    "1410": {"lines": {"S1": "2026-09-26"}},
    "1352": {"lines": {}},
    "835": {"lines": {}},
}
PLANNED = {"stations": [{"bst_id": "804", "name": "Wien Hütteldorf", "lines": ["S80"], "until": None}]}
WL_NAMES = {"U4", "U6", "49A", "5A", "7A", "37A", "1", "99A", "S80"}
TODAY = date(2026, 9, 27)


@pytest.fixture(autouse=True)
def _keep_the_real_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A test that reaches the default path must not touch data/feed_line_anomalies.json.
    monkeypatch.setattr(cl, "DEFAULT_ANOMALIES", tmp_path / "default" / "feed_line_anomalies.json")


def _directory(entries: list[dict[str, Any]] = ENTRIES, planned: object = PLANNED) -> cl.Directory:
    active, _ = cl.parse_planned(planned, TODAY)
    return cl.build_directory(entries, OEBB_LINES, active, WL_NAMES)


def _check(title: str, description: str = "", directory: cl.Directory | None = None) -> list[str]:
    return [f.text for f in cl.check_item(cl.FeedItem(title, description), directory or _directory())]


@pytest.mark.parametrize(
    ("title", "lines", "rest"),
    [
        ("7A/N65/N66: Arthaberplatz", ["7A", "N65", "N66"], "Arthaberplatz"),
        ("N68R: Quellenplatz", ["N68R"], "Quellenplatz"),
        ("Wien Hütteldorf ↔ Wien Hauptbahnhof", [], "Wien Hütteldorf ↔ Wien Hauptbahnhof"),
        ("S-Bahn Stammstrecke: Verspätungen", [], "S-Bahn Stammstrecke: Verspätungen"),
        ("7a: klein", [], "7a: klein"),
    ],
)
def test_split_title(title: str, lines: list[str], rest: str) -> None:
    assert cl.split_title(title) == (lines, rest)


def test_the_hutteldorf_item_is_reported() -> None:
    # The item that started it all (2026-09-25): WL's "ÖBB-Ersatzbus für
    # 80" surfaced under line 1, which does not serve Hütteldorf.
    findings = _check("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf [Am 26.09.2026]")
    assert findings == ["1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"]


def test_a_planned_line_confirms_a_station() -> None:
    # The S80 is replaced by buses at Hütteldorf; HAFAS does not show it.
    assert _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf [Am 26.09.2026]") == []


def test_an_expired_planned_line_no_longer_confirms() -> None:
    expired = {"stations": [{**PLANNED["stations"][0], "until": "2026-09-26"}]}
    directory = _directory(planned=expired)
    assert _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf", directory) == [
        "S80 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, U4)"
    ]
    assert cl.parse_planned(expired, TODAY) == (
        [],
        [cl.PlannedLines("804", frozenset({"S80"}), date(2026, 9, 26), "Wien Hütteldorf")],
    )


def test_a_train_line_is_not_judged_where_no_train_was_seen() -> None:
    # Rennweg: the Stammstrecke is closed, HAFAS shows no train, only the
    # trams next door are known. Unknown is not wrong.
    assert _check("S1: Störung", "Bahnhof Rennweg") == ["S1 at Wien Rennweg not judged (no train seen there)"]
    assert _check("S1/S45: Störung", "Rennweg") == ["S1/S45 at Wien Rennweg not judged (no train seen there)"]


def test_other_lines_are_still_judged_where_no_train_was_seen() -> None:
    assert _check("71: Umleitung", "Rennweg") == []
    assert _check("7A: Demonstration", "Betrieb ab Rennweg") == [
        "7A at Wien Rennweg not confirmed (known there: 71, O)"
    ]
    # A line the WL list knows but no station's trains: judged like a bus.
    assert _check("U6: Störung", "Rennweg") == ["U6 at Wien Rennweg not confirmed (known there: 71, O)"]
    # One line that can be judged is enough to judge the item.
    assert _check("S1/7A: Störung", "Rennweg") == ["S1/7A at Wien Rennweg not confirmed (known there: 71, O)"]


def test_a_station_without_any_known_line_is_not_judged() -> None:
    # Himberg: HAFAS shows no train and no WL stop lies within 200 m.
    assert _check("S45: Umbau", "Himberg") == ["S45 at Himberg not judged (no line known there)"]
    assert _check("99A: Umleitung", "Himberg") == ["99A at Himberg not judged (no line known there)"]


def test_a_train_line_is_judged_where_trains_run() -> None:
    assert _check("S45: Störung", "Bahnhof Meidling") == ["S45 at Wien Meidling not confirmed (known there: S1)"]


def test_the_directory_knows_the_train_lines() -> None:
    assert _directory().rail_lines == {"S1", "S45", "S50", "S80"}
    rennweg = _directory().stations_in("Rennweg")[0]
    assert (rennweg.lines, rennweg.rail_lines) == (frozenset({"71", "O"}), frozenset[str]())


def test_a_wl_stop_counts_only_within_200_m() -> None:
    assert _check("U4: Störung", "Bhf. Hütteldorf") == []  # 126 m
    assert _check("99A: Umleitung", "Bhf. Hütteldorf") == [
        "99A at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_temporary_stops_off_the_line_are_not_judged() -> None:
    # Real item, 2026-09-25: a temporary stop on another line's route.
    assert _check("37A: Busse halten Pasettistraße vor Hellwagstraße (bei Linie 5A)") == []


def test_the_longest_name_wins() -> None:
    # "Meidling Hauptstraße" is the U4 station 1 km away, not Bahnhof Meidling.
    assert _check("7A: Haltestellenverlegung", "ab Meidling Hauptstraße U") == []
    assert _check("7A: Haltestellenverlegung", "ab Bahnhof Meidling") == [
        "7A at Wien Meidling not confirmed (known there: S1)"
    ]


def test_case_and_punctuation_do_not_hide_a_station() -> None:
    assert _check("1: Umleitung", "Ab HÜTTELDORF, dann …") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_a_direction_is_not_a_stop() -> None:
    # Replay of 678 feed versions: "43: Stromstörung Züge halten bei Linie 9
    # Richtung Westbahnhof" named the destination, not a stop of the 43.
    assert _check("1: Umleitung", "Züge fahren Richtung Hütteldorf") == []
    assert _check("1: Umleitung", "in Ri. Bhf. Hütteldorf") == []
    assert _check("1: Umleitung", "Busse fahren (Richtung Hütteldorf) ab") == []
    assert _check("1: Umleitung", "Richtung Meidling, dann ab Hütteldorf") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_a_name_with_a_comma_is_matched_whole() -> None:
    # "Hütteldorf, Bujattigasse" is the WL stop 300 m away, not the station.
    assert _check("99A: Haltestellenverlegung", "Haltestelle Hütteldorf, Bujattigasse") == []


def test_a_name_at_the_end_of_a_sentence_is_found() -> None:
    assert _check("1: Umleitung", "Die Züge enden in Hütteldorf. Danach …") == [
        "1 at Wien Hütteldorf not confirmed (known there: 49A, S45, S50, S80, U4)"
    ]


def test_short_codes_and_numbers_are_no_names() -> None:
    # "Hf" is Hütteldorf's operating code, "490056000" its VOR id.
    assert _check("1: Umleitung", "Hf 490056000") == []


def test_one_line_of_several_is_enough() -> None:
    assert _check("1/U4: Umleitung", "Bhf. Hütteldorf") == []


def test_an_ambiguous_name_is_not_judged() -> None:
    other = {**MEIDLING, "bst_id": "999", "name": "Anderswo", "aliases": ["Hütteldorf"]}
    directory = cl.build_directory([*ENTRIES, other], {**OEBB_LINES, "999": {"lines": {"R9": "2026-09-26"}}}, [], WL_NAMES)
    assert _check("1: Umleitung", "Hütteldorf", directory) == []


@pytest.mark.parametrize(
    ("line", "known"),
    [("U4", True), ("S45", True), ("S80", True), ("U6E", True), ("26E", False), ("X9", False), ("E", False)],
)
def test_known_lines(line: str, known: bool) -> None:
    # U6E: replacement for the known U6. 26E is not in this test's WL list.
    assert _directory().is_known(line) is known


def test_an_unknown_line_is_reported() -> None:
    assert _check("X9: Störung") == ["line X9 is in no directory"]


def test_items_without_a_line_prefix_are_skipped() -> None:
    assert _check("Wien Hütteldorf ↔ Wien Hauptbahnhof", "Bhf. Hütteldorf, Linie X9") == []


@pytest.mark.parametrize(
    "entry",
    [
        {"bst_id": 804, "lines": ["S80"]},
        {"bst_id": "80a", "lines": ["S80"]},
        {"bst_id": "804", "lines": []},
        {"bst_id": "804", "lines": ["s80"]},
        {"bst_id": "804", "lines": ["S80"], "until": "Ende 2028"},
        {"bst_id": "804", "lines": ["S80"], "until": 2028},
        "junk",
    ],
)
def test_invalid_planned_entries_are_skipped(entry: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="feed_line_check"):
        assert cl.parse_planned({"stations": [entry]}, TODAY) == ([], [])
    assert "Skipping invalid planned-lines entry" in caplog.text


def test_planned_payload_shapes() -> None:
    assert cl.parse_planned(None, TODAY) == ([], [])
    assert cl.parse_planned({"stations": "x"}, TODAY) == ([], [])
    (entry,), _ = cl.parse_planned({"stations": [{"bst_id": "1", "lines": ["S1"], "until": "2026-09-27"}]}, TODAY)
    assert entry == cl.PlannedLines("1", frozenset({"S1"}), date(2026, 9, 27), "1")  # in force on its last day


def _planned(until: str) -> cl.PlannedLines:
    return cl.PlannedLines("804", frozenset({"S80"}), date.fromisoformat(until), "Wien Hütteldorf")


def test_an_expired_entry_needs_review_only_while_hafas_lacks_its_lines() -> None:
    expired = [_planned("2027-12-11")]
    (finding,) = cl.expired_findings(expired, OEBB_LINES)
    assert (finding.kind, finding.subject, finding.text, finding.is_finding) == (
        "planned_expired",
        "Wien Hütteldorf",
        "planned S80 expired on 2027-12-11; HAFAS shows no S80 there",
        False,
    )
    back = {**OEBB_LINES, "804": {"lines": {"S45": "2027-12-19", "S80": "2027-12-19"}}}
    assert cl.expired_findings(expired, back) == []


def _record(**fields: Any) -> dict[str, Any]:
    record = {
        "kind": "not_confirmed",
        "subject": "Wien Hütteldorf",
        "title": "1: Bhf. Hütteldorf",
        "first_seen": "2026-09-25",
        "last_seen": "2026-09-25",
        "days_seen": 1,
        "detail": "1 at Wien Hütteldorf not confirmed",
    }
    return {**record, **fields}


def _finding(title: str = "1: Bhf. Hütteldorf", text: str = "1 at Wien Hütteldorf not confirmed (new)") -> cl.Finding:
    return cl.Finding(title, text, "not_confirmed", "Wien Hütteldorf")


def test_a_new_anomaly_starts_a_record() -> None:
    assert cl.merge_anomalies([], [_finding()], TODAY) == [
        _record(first_seen="2026-09-27", last_seen="2026-09-27", detail="1 at Wien Hütteldorf not confirmed (new)")
    ]


def test_a_known_anomaly_counts_each_day_once() -> None:
    merged = cl.merge_anomalies([_record()], [_finding(), _finding()], TODAY)
    assert merged == [_record(last_seen="2026-09-27", days_seen=2, detail="1 at Wien Hütteldorf not confirmed (new)")]
    # Every cycle of the same day: nothing changes.
    assert cl.merge_anomalies(merged, [_finding()], TODAY) == merged


def test_records_are_kept_while_the_anomaly_is_gone() -> None:
    assert cl.merge_anomalies([_record()], [], TODAY) == [_record()]


@pytest.mark.parametrize(
    "record",
    [
        _record(kind="wrong"),
        _record(subject=None),
        _record(title=7),
        _record(days_seen=0),
        _record(days_seen=True),
        _record(first_seen="gestern"),
        _record(last_seen=None),
        _record(last_seen="20260925"),  # fromisoformat accepts it, the order would break
        "junk",
    ],
)
def test_invalid_records_are_dropped(record: object) -> None:
    assert cl.merge_anomalies([record], [], TODAY) == []


def test_the_collection_keeps_the_most_recent_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cl, "MAX_ANOMALY_RECORDS", 2)
    old = [
        _record(title="1: seit Langem", first_seen="2026-09-21", last_seen="2026-09-26"),
        _record(title="2: vorbei", first_seen="2026-09-22", last_seen="2026-09-22"),
        _record(title="3: vorbei", first_seen="2026-09-23", last_seen="2026-09-23"),
    ]
    merged = cl.merge_anomalies(old, [], TODAY)
    # The one seen longest ago goes, whenever it started.
    assert [record["title"] for record in merged] == ["1: seit Langem", "3: vorbei"]


def test_a_title_with_hidden_characters_stays_one_record(tmp_path: Path) -> None:
    # The writer scrubs Trojan-Source characters; the key must match what it wrote.
    path = tmp_path / "anomalies.json"
    finding = _finding("1: Bhf.\u200b Hütteldorf")
    assert cl.record_anomalies(path, [finding], TODAY) == 1
    before = path.read_bytes()
    assert cl.record_anomalies(path, [finding], TODAY) == 0
    assert path.read_bytes() == before
    (record,) = json.loads(before)["anomalies"]
    assert record["title"] == "1: Bhf. Hütteldorf"


def test_long_texts_are_cut() -> None:
    (record,) = cl.merge_anomalies([], [_finding("7A: " + "x" * 400, "y" * 400)], TODAY)
    assert (len(record["title"]), len(record["detail"])) == (cl.MAX_RECORD_TEXT, cl.MAX_RECORD_TEXT)


def test_load_wl_line_names(tmp_path: Path) -> None:
    csv_path = tmp_path / "linien.csv"
    csv_path.write_text('"LineID";"LineText"\n1;"U4"\n2;" 13A "\n3;""\n', encoding="utf-8")
    assert cl.load_wl_line_names(csv_path) == {"U4", "13A"}
    assert cl.load_wl_line_names(tmp_path / "missing.csv") == set()


def test_an_oversized_wl_line_list_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csv_path = tmp_path / "linien.csv"
    csv_path.write_text('"LineID";"LineText"\n1;"U4"\n', encoding="utf-8")
    monkeypatch.setattr(cl, "MAX_CSV_BYTES", 10)
    assert cl.load_wl_line_names(csv_path) == set()


def _feed(tmp_path: Path, *items: tuple[str, str]) -> Path:
    body = "".join(f"<item><title>{t}</title><description>{d}</description></item>" for t, d in items)
    path = tmp_path / "feed.xml"
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?><rss><channel>{body}</channel></rss>', encoding="utf-8")
    return path


def _main_args(tmp_path: Path, feed: Path, record: Path | None = None) -> list[str]:
    stations = tmp_path / "stations.json"
    stations.write_text(json.dumps({"stations": ENTRIES}), encoding="utf-8")
    oebb = tmp_path / "oebb.json"
    oebb.write_text(json.dumps({"stations": OEBB_LINES}), encoding="utf-8")
    planned = tmp_path / "planned.json"
    planned.write_text(json.dumps(PLANNED), encoding="utf-8")
    wl = tmp_path / "linien.csv"
    wl.write_text('"LineID";"LineText"\n' + "".join(f'{n};"{line}"\n' for n, line in enumerate(WL_NAMES)), encoding="utf-8")
    return [
        *("--feed", str(feed), "--stations", str(stations), "--oebb-lines", str(oebb)),
        *("--planned", str(planned), "--wl-lines", str(wl)),
        *(("--no-record",) if record is None else ("--anomalies", str(record))),
    ]


def test_main_reports_and_leaves_everything_unchanged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    feed = _feed(
        tmp_path,
        ("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf"),
        ("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf"),
        ("Wien A ↔ Wien B", ""),
    )
    before = feed.read_bytes()
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(_main_args(tmp_path, feed)) == 0
    assert "Line check: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80: 1 at Wien Hütteldorf not confirmed" in caplog.text
    assert "Line check: 3 items, 2 with a line prefix, 1 findings, 0 not judged" in caplog.text
    assert feed.read_bytes() == before


def _collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Writes are confined to data/, docs/ and log/ (validate_path).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cl, "_today", lambda: TODAY)
    (tmp_path / "data").mkdir()
    return tmp_path / "data" / "feed_line_anomalies.json"


def test_main_collects_the_anomalies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    record = _collection(tmp_path, monkeypatch)
    feed = _feed(
        tmp_path,
        ("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf"),
        ("S1: Störung", "Rennweg"),
        ("X9: Störung", ""),
        ("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf"),
    )
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(_main_args(tmp_path, feed, record)) == 0
    assert "4 items, 4 with a line prefix, 2 findings, 1 not judged" in caplog.text
    assert "Line check: 3 new anomalies, collected in feed_line_anomalies.json" in caplog.text
    document = json.loads(record.read_text(encoding="utf-8"))
    assert [(r["kind"], r["subject"], r["title"], r["first_seen"], r["days_seen"]) for r in document["anomalies"]] == [
        ("not_confirmed", "Wien Hütteldorf", "1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "2026-09-27", 1),
        ("not_judged", "Wien Rennweg", "S1: Störung", "2026-09-27", 1),
        ("unknown_line", "X9", "X9: Störung", "2026-09-27", 1),
    ]
    # The next cycle of the same day finds the same: the file stays as it is.
    before = record.read_bytes()
    caplog.clear()
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(_main_args(tmp_path, feed, record)) == 0
    assert record.read_bytes() == before
    assert "Line check: 0 new anomalies" in caplog.text


def test_main_collects_expired_planned_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    record = _collection(tmp_path, monkeypatch)
    args = _main_args(tmp_path, _feed(tmp_path), record)
    (tmp_path / "planned.json").write_text(
        json.dumps({"stations": [{**PLANNED["stations"][0], "until": "2026-09-26"}]}), encoding="utf-8"
    )
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(args) == 0
    assert "Planned lines for Wien Hütteldorf: planned S80 expired on 2026-09-26" in caplog.text
    (entry,) = json.loads(record.read_text(encoding="utf-8"))["anomalies"]
    assert (entry["kind"], entry["subject"], entry["title"]) == ("planned_expired", "Wien Hütteldorf", "")

    # Once HAFAS shows the S80 again, the expired entry needs no review.
    (tmp_path / "oebb.json").write_text(
        json.dumps({"stations": {**OEBB_LINES, "804": {"lines": {"S80": "2026-09-27"}}}}), encoding="utf-8"
    )
    caplog.clear()
    with caplog.at_level("INFO", logger="feed_line_check"):
        assert cl.main(args) == 0
    assert "Planned lines for Wien Hütteldorf have expired; HAFAS shows them again" in caplog.text
    assert "review data/planned_station_lines.json" not in caplog.text


def test_main_starts_the_collection_without_anomalies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _collection(tmp_path, monkeypatch)
    assert cl.main(_main_args(tmp_path, _feed(tmp_path, ("U4: Störung", "Bhf. Hütteldorf")), record)) == 0
    assert json.loads(record.read_text(encoding="utf-8"))["anomalies"] == []


def test_main_without_record_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _collection(tmp_path, monkeypatch)
    args = _main_args(tmp_path, _feed(tmp_path, ("X9: Störung", "")), record)
    assert cl.main([*args, "--no-record"]) == 0
    assert not record.exists()


def test_main_refuses_a_collection_outside_the_allowed_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _collection(tmp_path, monkeypatch)
    feed = _feed(tmp_path, ("X9: Störung", ""))
    assert cl.main(_main_args(tmp_path, feed, tmp_path / "anomalies.json")) == 1
    assert not (tmp_path / "anomalies.json").exists()


def test_main_starts_over_from_a_broken_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _collection(tmp_path, monkeypatch)
    record.write_text("{not json", encoding="utf-8")
    assert cl.main(_main_args(tmp_path, _feed(tmp_path, ("X9: Störung", "")), record)) == 0
    (entry,) = json.loads(record.read_text(encoding="utf-8"))["anomalies"]
    assert entry["subject"] == "X9"


def test_main_reports_a_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _collection(tmp_path, monkeypatch)

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cl, "write_anomalies", broken)
    assert cl.main(_main_args(tmp_path, _feed(tmp_path, ("X9: Störung", "")), record)) == 1


def test_main_without_a_feed_fails(tmp_path: Path) -> None:
    assert cl.main(_main_args(tmp_path, tmp_path / "missing.xml")) == 1


def test_main_rejects_an_oversized_or_broken_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = _feed(tmp_path, ("1: Bhf. Hütteldorf", ""))
    args = _main_args(tmp_path, feed)
    monkeypatch.setattr(cl, "MAX_FEED_BYTES", 10)
    assert cl.main(args) == 1
    monkeypatch.undo()
    feed.write_text("<rss><channel><item>", encoding="utf-8")
    assert cl.main(args) == 1


def test_main_rejects_entity_declarations(tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rss><channel><item><title>&x;</title></item></channel></rss>',
        encoding="utf-8",
    )
    assert cl.main(_main_args(tmp_path, feed)) == 1


def test_the_committed_planned_lines_are_valid() -> None:
    payload = json.loads((REPO / "data" / "planned_station_lines.json").read_text(encoding="utf-8"))
    stations = json.loads((REPO / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]
    directory = {e["bst_id"]: e["name"] for e in stations if e.get("bst_id")}
    entries = payload["stations"]
    active, expired = cl.parse_planned(payload, date(2026, 9, 26))
    assert len(active) + len(expired) == len(entries)  # no entry is invalid
    for entry in entries:
        assert directory.get(entry["bst_id"]) == entry["name"]


def test_the_real_directory_resolves_the_key_names() -> None:
    entries = json.loads((REPO / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]
    oebb = json.loads((REPO / "data" / "oebb_station_lines.json").read_text(encoding="utf-8"))["stations"]
    planned_payload = json.loads((REPO / "data" / "planned_station_lines.json").read_text(encoding="utf-8"))
    planned, _ = cl.parse_planned(planned_payload, date(2026, 9, 26))
    directory = cl.build_directory(entries, oebb, planned, cl.load_wl_line_names(cl.DEFAULT_WL_LINES))
    texts = ("Bhf. Hütteldorf", "Meidling Hauptstraße U", "Hauptbahnhof S U", "Pasettistraße")
    names = {text: [s.name for s in directory.stations_in(text)] for text in texts}
    assert names == {
        "Bhf. Hütteldorf": ["Wien Hütteldorf"],
        "Meidling Hauptstraße U": [],
        "Hauptbahnhof S U": ["Wien Hauptbahnhof"],
        "Pasettistraße": [],
    }
    assert _check("1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80", "Bhf. Hütteldorf", directory)
    assert not _check("S80: ÖBB-Ersatzbus", "Bhf. Hütteldorf", directory)
    # Closed stations without a planned entry (2026-09-26): not judged.
    assert _check("S1: Störung", "Wien Mitte-Landstraße", directory) == [
        "S1 at Wien Mitte-Landstraße not judged (no train seen there)"
    ]
    assert _check("S60: Umbau", "Bahnhof Himberg", directory) == ["S60 at Himberg not judged (no line known there)"]
