"""The yearly Baustellen ledger ``data/stats/baustellen_<year>.csv``.

Owner's decision of 2026-11-10: the page of a finished year summarises the
year's Baustellen. The rows for it are written when the Baustellen cache is
written from the city's answer (``scripts/update_baustellen_cache.py``),
one per site and year (``src/utils/stats_baustellen.py``).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import (
    BAUSTELLEN_HEADER,
    MAX_DESCRIPTION_CHARS,
    MAX_ROW_BYTES,
    append_baustellen_rows,
    baustelle_row,
    known_guids,
)
from tests.test_update_baustellen_cache._support import SAMPLE_PATH, _real_cache_file, _site

REAL_STATS_DIR = Path(__file__).resolve().parents[1] / "data" / "stats"
LEDGER_LOGGER = "utils.stats_baustellen"
SCRIPT_LOGGER = "update_baustellen_cache"

# Tuesday, 10 November 2026, 17:44:05 in Vienna (CET, +01:00).
NOW = datetime(2026, 11, 10, 17, 44, 5, tzinfo=VIENNA_TZ)
GUID = "1642f76cdb2a921752f24384fa1ad815176094f31dfa2f9d5130723b697b3205"
HEADER_LINE = ",".join(BAUSTELLEN_HEADER)
# A well-formed data line; ``{guid}`` is filled in by the tests.
LINE = "2026-11-10T17:44:05+01:00,Di,17,{guid},Titel,2026-07-28,,7,Straßenbau,Text"


def _event(**changes: Any) -> dict[str, Any]:
    """A cache event as ``serialize_for_cache`` leaves it (dates as ISO strings)."""
    event: dict[str, Any] = {
        "source": "Stadt Wien – Baustellen", "category": "Baustelle", "pubDate": "2026-07-28T00:00:00+02:00",
        "link": "https://www.wien.gv.at/verkehr/baustellen",
        "location": {"coordinates": {"lat": 48.2037, "lon": 16.3442}},
        "title": "Burggasse 67",
        "description": (
            "Die Haltestelle wird von Burggasse ONr. 67 nach Burggasse ONr. 69 verlegt. \n"
            "Beginn: 28.07.2026 00:00 Uhr \nGeplant bis: 30.10.2026 00:00 Uhr \n"
            "Maßnahme: Straßenbau \nBezirk: 7"
        ),
        "guid": GUID,
        "starts_at": "2026-07-28T00:00:00+02:00",
        "ends_at": "2026-10-30T00:00:00+01:00",
        "context": {"district": "7", "measure": "Straßenbau"},
    }
    event.update(changes)
    return event


def _row(event: dict[str, Any], seen_at: datetime = NOW) -> dict[str, str]:
    row = baustelle_row(event, seen_at=seen_at)
    assert row is not None
    return dict(zip(BAUSTELLEN_HEADER, row, strict=True))


def _ledger(folder: Path, year: int = 2026) -> Path:
    return folder / f"baustellen_{year}.csv"


def _records(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


# --- the row ----------------------------------------------------------------


def test_header_and_caps_are_the_agreed_ones() -> None:
    names = "timestamp weekday hour guid title starts_at ends_at district measure description"
    assert BAUSTELLEN_HEADER == tuple(names.split())
    assert (MAX_DESCRIPTION_CHARS, MAX_ROW_BYTES) == (2000, 4000)


def test_row_of_a_cache_event() -> None:
    assert baustelle_row(_event(), seen_at=NOW) == (
        "2026-11-10T17:44:05+01:00", "Di", "17", GUID, "Burggasse 67", "2026-07-28", "2026-10-30", "7", "Straßenbau",
        "Die Haltestelle wird von Burggasse ONr. 67 nach Burggasse ONr. 69 verlegt. "
        "Beginn: 28.07.2026 00:00 Uhr Geplant bis: 30.10.2026 00:00 Uhr "
        "Maßnahme: Straßenbau Bezirk: 7",
    )
    # Vienna time: 23:30 UTC on Saturday is 00:30 on Sunday.
    row = _row(_event(), datetime(2026, 11, 14, 23, 30, 9, 999, tzinfo=UTC))
    assert (row["timestamp"], row["weekday"], row["hour"]) == ("2026-11-15T00:30:09+01:00", "So", "00")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-28T00:00:00+02:00", "2026-07-28"),
        # The instant counts, not the digits: 23:30 UTC is the next day in Vienna.
        ("2026-12-31T23:30:00+00:00", "2027-01-01"),
        ("2026-12-31T23:30:00Z", "2027-01-01"),
        ("2026-07-28", "2026-07-28"),
        (datetime(2026, 7, 28, 0, 0, tzinfo=VIENNA_TZ), "2026-07-28"),
        (datetime(2026, 7, 27, 22, 0, tzinfo=UTC), "2026-07-28"),
        (datetime(2026, 7, 28, 9, 15), "2026-07-28"),
        (date(2026, 7, 28), "2026-07-28"),
        # 31.12.9999 23:59 UTC has no Vienna date: an empty field, not a lost row.
        (datetime(9999, 12, 31, 23, 59, tzinfo=UTC), ""),
        ("", ""), ("demnächst", ""), (None, ""), (20260728, ""),
    ],
)
def test_dates_as_datetime_or_string(value: Any, expected: str) -> None:
    row = _row(_event(starts_at=value, ends_at=value))
    assert (row["starts_at"], row["ends_at"]) == (expected, expected)
    assert row["title"] == "Burggasse 67"


def test_event_without_end_and_context() -> None:
    event = _event()
    del event["ends_at"], event["context"]
    row = _row(event)
    assert (row["ends_at"], row["district"], row["measure"]) == ("", "", "")
    row = _row(_event(context={"district": 21, "measure": None, "status": "aktiv"}))
    assert (row["district"], row["measure"]) == ("21", "")
    assert _row(_event(context="7"))["district"] == ""


def test_text_fields_are_one_line_without_invisible_characters() -> None:
    row = _row(_event(
        title="  Burg\u200bgasse\r\n67\t  (Teil\u00a01) \u202e\x00",
        description="Erste Zeile. \nZweite\n\n\nZeile.\r\nDritte\u2028Zeile\x85Ende \u200b Halte\u00adstelle\ufeff  ",
        context={"district": " 7\n8 ", "measure": "Straßenbau\nGleisbau"},
    ))
    assert row["title"] == "Burggasse 67 (Teil 1)"
    # A line separator becomes a space; an invisible character between two
    # spaces leaves one space, not two.
    assert row["description"] == "Erste Zeile. Zweite Zeile. Dritte Zeile Ende Haltestelle"
    assert (row["district"], row["measure"]) == ("7 8", "Straßenbau Gleisbau")


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_formula_guard(prefix: str) -> None:
    payload = f"{prefix}HYPERLINK(1)"
    row = _row(_event(
        title=f" \u200b{payload}",
        description=f"\n{payload}",
        context={"district": payload, "measure": f"\t{payload}"},
    ))
    for field in ("title", "description", "district", "measure"):
        assert row[field] == f"'{payload}", field


def test_caps_of_the_text_fields() -> None:
    row = _row(_event(title="T" * 500, context={"district": "D" * 500, "measure": "M" * 500}))
    assert (len(row["title"]), len(row["district"]), len(row["measure"])) == (200, 200, 200)
    exact = "a" * MAX_DESCRIPTION_CHARS
    assert _row(_event(description=exact))["description"] == exact
    longer = _row(_event(description=exact + "a"))["description"]
    assert longer == "a" * (MAX_DESCRIPTION_CHARS - 1) + "…"
    # The guard's apostrophe counts towards the cap.
    guarded = _row(_event(description="=" + "a" * 3000))["description"]
    assert guarded.startswith("'=a") and guarded.endswith("a…")
    assert len(guarded) == MAX_DESCRIPTION_CHARS


@pytest.mark.parametrize(
    "description",
    ["Straßenbahnhaltestelle für Öffi-Fahrgäste über die Brücke verlegt. " * 75, "ü" * 5000, '"' * 5000, "🚧" * 5000, "ä, ö; ü\n" * 625],
    ids=["text-with-umlauts", "only-umlauts", "only-quotes", "four-byte", "commas-and-newlines"],
)
def test_row_stays_below_4000_bytes(description: str) -> None:
    assert len(description) >= 5000
    row = baustelle_row(
        _event(title="Ö" * 300, description=description, context={"district": "ß" * 300, "measure": "Ü" * 300}),
        seen_at=NOW,
    )
    assert row is not None
    buffer = io.StringIO()
    csv.writer(buffer).writerow(row)
    encoded = buffer.getvalue().encode("utf-8")
    assert len(encoded) < MAX_ROW_BYTES
    assert encoded.count(b"\n") == 1  # one record, one physical line
    shortened = row[-1]
    assert shortened.endswith("…")
    assert 0 < len(shortened) <= MAX_DESCRIPTION_CHARS
    assert " ".join(description.split()).startswith(shortened[:-1])  # the beginning is kept
    # The longest beginning that fits: at most a few bytes of room are left
    # (one four-byte character, a doubled quotation mark or a cut-off space).
    assert len(encoded) >= MAX_ROW_BYTES - 6 or len(shortened) == MAX_DESCRIPTION_CHARS


def test_a_row_of_exactly_4000_bytes_is_too_long(stats_dir: Path) -> None:
    # "Below 4,000 bytes": ASCII text reaches every byte count, so the whole rows end at 3,999 and the shortened ones stay there.
    wide = {"title": "🚧" * 200, "context": {"district": "🚧" * 200, "measure": "🚧" * 200}}
    events = [_event(guid=f"{n:04d}", description="a" * n, **wide) for n in range(1300, 1800)]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 500
    lines = _ledger(stats_dir).read_bytes().split(b"\r\n")[1:-1]
    assert max(len(line) + 2 for line in lines) == MAX_ROW_BYTES - 1
    assert lines[0].endswith(b"a") and lines[-1].endswith("a…".encode())


def test_a_lone_surrogate_does_not_cost_the_row(stats_dir: Path) -> None:
    event = _event(title="Burg\udc80gasse", description="Text \ud800 Ende")
    assert append_baustellen_rows([event], now=NOW, stats_dir=stats_dir) == 1
    record = _records(_ledger(stats_dir))[0]
    assert (record["title"], record["description"]) == ("Burg?gasse", "Text ? Ende")


@pytest.mark.parametrize(
    "changes",
    [
        {"guid": ""}, {"guid": "  \n "}, {"guid": None}, {"guid": 4711}, {"guid": "\u200b"},
        {"title": ""}, {"title": " \t"}, {"title": None}, {"title": ["Burggasse"]},
    ],
)
def test_no_row_without_guid_or_title(changes: dict[str, Any], stats_dir: Path) -> None:
    assert baustelle_row(_event(**changes), seen_at=NOW) is None
    assert append_baustellen_rows([_event(**changes)], now=NOW, stats_dir=stats_dir) == 0
    assert not stats_dir.exists()


def test_event_without_the_guid_key_is_skipped_and_the_next_one_written(stats_dir: Path) -> None:
    without = _event()
    del without["guid"]
    assert baustelle_row(without, seen_at=NOW) is None
    assert append_baustellen_rows([without, _event(guid="b")], now=NOW, stats_dir=stats_dir) == 1
    assert known_guids(2026, stats_dir=stats_dir) == {"b"}


# --- the file ---------------------------------------------------------------


def test_append_writes_header_once_and_one_line_per_site(stats_dir: Path) -> None:
    events = [_event(), _event(guid="b" * 64, title='Thaliastraße "Ost", Teil 2')]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2
    text = _ledger(stats_dir).read_bytes().decode("utf-8")
    assert text.count("\n") == 3 and text.endswith("\r\n")
    assert text.split("\r\n")[0] == HEADER_LINE
    records = _records(_ledger(stats_dir))
    assert [r["guid"] for r in records] == [GUID, "b" * 64]
    assert records[1]["title"] == 'Thaliastraße "Ost", Teil 2'
    assert dict(records[0]) == _row(_event())
    assert sorted(p.name for p in stats_dir.iterdir()) == ["baustellen_2026.csv"]


def test_a_guid_is_written_once_across_calls(stats_dir: Path) -> None:
    later = datetime(2026, 11, 11, 6, 0, tzinfo=VIENNA_TZ)
    # Both WFS layers can list the same site: once per answer, too.
    assert append_baustellen_rows([_event(), _event()], now=NOW, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([_event()], now=later, stats_dir=stats_dir) == 0
    # The text of a known site may change; its row stays the first one.
    changed = _event(description="Neuer Text.", title="Burggasse 67–69")
    assert append_baustellen_rows([changed, _event(guid="neu")], now=later, stats_dir=stats_dir) == 1
    records = _records(_ledger(stats_dir))
    assert [(r["guid"], r["timestamp"]) for r in records] == [(GUID, "2026-11-10T17:44:05+01:00"), ("neu", "2026-11-11T06:00:00+01:00")]
    assert records[0]["title"] == "Burggasse 67"


@pytest.mark.parametrize(
    ("now", "year"),
    [
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=VIENNA_TZ), 2026),
        (datetime(2027, 1, 1, 0, 0, 0, tzinfo=VIENNA_TZ), 2027),
        # The same two instants given in UTC: the Vienna year decides.
        (datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC), 2026),
        (datetime(2026, 12, 31, 23, 0, 0, tzinfo=UTC), 2027),
        # A naive moment is Vienna time, as in ``to_vienna``.
        (datetime(2026, 12, 31, 23, 59, 59), 2026),
        (datetime(2027, 1, 1, 0, 0, 0), 2027),
    ],
    ids=["vienna-2359", "vienna-0000", "utc-2259", "utc-2300", "naive-2359", "naive-0000"],
)
def test_year_file_is_the_vienna_year_of_now(now: datetime, year: int, stats_dir: Path) -> None:
    assert append_baustellen_rows([_event()], now=now, stats_dir=stats_dir) == 1
    assert [p.name for p in stats_dir.iterdir()] == [f"baustellen_{year}.csv"]
    stamp = _records(_ledger(stats_dir, year))[0]["timestamp"]
    assert stamp.startswith(f"{year}-") and stamp.endswith("+01:00")


def test_a_site_still_running_gets_a_row_in_the_new_year_too(stats_dir: Path) -> None:
    old_year = datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA_TZ)
    new_year = datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ)
    assert append_baustellen_rows([_event()], now=old_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([_event()], now=new_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([_event()], now=new_year, stats_dir=stats_dir) == 0
    assert known_guids(2026, stats_dir=stats_dir) == {GUID} == known_guids(2027, stats_dir=stats_dir)
    assert _records(_ledger(stats_dir, 2026))[0]["timestamp"] == "2026-12-31T23:59:00+01:00"
    assert _records(_ledger(stats_dir, 2027))[0]["timestamp"] == "2027-01-01T00:00:00+01:00"


# --- reading an existing file -----------------------------------------------


def test_missing_and_empty_file(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert known_guids(2026, stats_dir=stats_dir) == set()
    stats_dir.mkdir()
    _ledger(stats_dir).write_bytes(b"")
    assert known_guids(2026, stats_dir=stats_dir) == set()
    assert _warnings(caplog) == []
    # An empty file gets the header like a new one.
    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 1
    assert _ledger(stats_dir).read_text(encoding="utf-8").startswith(HEADER_LINE + "\n")
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}


def test_known_guids_tolerates_malformed_and_repeated_lines(stats_dir: Path) -> None:
    lines = [
        HEADER_LINE,
        LINE.format(guid="eins"),
        LINE.format(guid="eins"),  # the same line twice (union merge)
        HEADER_LINE,  # both sides created the file
        "",
        "zu,wenige,spalten,kurz",
        LINE.format(guid="lang") + ",eine Spalte zu viel",
        LINE.format(guid=""),
        LINE.format(guid="zwei") + "\r",  # the writer's line ending
        "\x00\x01 kein CSV",
        LINE.format(guid="drei"),
    ]
    stats_dir.mkdir()
    _ledger(stats_dir).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    assert known_guids(2026, stats_dir=stats_dir) == {"eins", "zwei", "drei"}


def test_a_torn_line_does_not_hide_the_lines_after_it(stats_dir: Path) -> None:
    # Read as one CSV stream, the open quotation mark would swallow every record up to
    # the next quotation mark, and their sites would be entered again on every run.
    assert append_baustellen_rows([_event(guid="eins")], now=NOW, stats_dir=stats_dir) == 1
    with _ledger(stats_dir).open("ab") as handle:
        handle.write(b'2026-11-10T17:44:05+01:00,Di,17,torn,"Titel mit offenem\r\n')
        # Undecodable bytes (a cut umlaut) spoil only their line as well.
        handle.write(b"2026-11-10T17:44:05+01:00,Di,17,kaputt,Stra\xc3\r\n")
    events = [_event(guid="zwei", title='Gasse "A"'), _event(guid="drei")]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2
    assert known_guids(2026, stats_dir=stats_dir) == {"eins", "zwei", "drei"}
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 0
    # The site of the torn line is not known and is entered again, whole.
    assert append_baustellen_rows([_event(guid="torn")], now=NOW, stats_dir=stats_dir) == 1
    assert "torn" in known_guids(2026, stats_dir=stats_dir)


@pytest.mark.parametrize(
    "first_line",
    [
        "timestamp,weekday,hour,provider,location_name",
        "timestamp,weekday,hour,guid,title,starts_at,ends_at,district,measure",
        HEADER_LINE + ",lat",
        HEADER_LINE.replace("timestamp", "Timestamp"),
        LINE.format(guid="eins"),
        "",
        "\ufeff" + HEADER_LINE,
    ],
    ids=["other-ledger", "column-missing", "column-added", "other-case", "no-header", "blank", "bom"],
)
def test_file_with_a_different_header_is_left_alone(first_line: str, stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    stats_dir.mkdir()
    content = first_line + "\n" + LINE.format(guid="eins") + "\n"
    _ledger(stats_dir).write_text(content, encoding="utf-8", newline="")
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)

    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 0

    assert _ledger(stats_dir).read_text(encoding="utf-8") == content
    assert known_guids(2026, stats_dir=stats_dir) == set()
    messages = _warnings(caplog)
    assert messages and all("baustellen_2026.csv hat eine andere Kopfzeile" in m for m in messages)
    assert all(r.name == LEDGER_LOGGER for r in caplog.records)
    # The check is per year file: the next year's file starts clean.
    new_year = datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ)
    assert append_baustellen_rows([_event()], now=new_year, stats_dir=stats_dir) == 1


def test_file_above_the_read_cap_is_left_alone(stats_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Read as empty, every run would append every site again."""
    assert stats_baustellen.MAX_BAUSTELLEN_CSV_BYTES >= 2000 * MAX_ROW_BYTES
    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 1
    content = _ledger(stats_dir).read_bytes()
    monkeypatch.setattr(stats_baustellen, "MAX_BAUSTELLEN_CSV_BYTES", len(content) - 1)
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)

    assert known_guids(2026, stats_dir=stats_dir) == set()
    assert append_baustellen_rows([_event(guid="neu")], now=NOW, stats_dir=stats_dir) == 0

    assert _ledger(stats_dir).read_bytes() == content
    assert any("nicht lesbar oder größer als" in m for m in _warnings(caplog))
    monkeypatch.setattr(stats_baustellen, "MAX_BAUSTELLEN_CSV_BYTES", len(content))
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}


# --- failures ---------------------------------------------------------------


def _raise_runtime_error(*_args: Any) -> bool:
    raise RuntimeError("Platte voll\nzweite Zeile")


def test_unwritable_folder_does_not_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("eine Datei, kein Ordner", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NOW, stats_dir=blocker / "stats") == 0
    assert blocker.read_text(encoding="utf-8") == "eine Datei, kein Ordner"
    # The cause is the same for every row: one warning, not one per site.
    assert len(_warnings(caplog)) == 1
    # A path the system refuses to look at (name too long) is a file that cannot be used, not an error for the caller.
    assert known_guids(2026, stats_dir=tmp_path / ("x" * 300)) == set()
    assert "baustellen_2026.csv ist nicht lesbar" in _warnings(caplog)[-1]


def test_a_raising_writer_does_not_raise_here(stats_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(stats_baustellen, "_append_row", _raise_runtime_error)
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NOW, stats_dir=stats_dir) == 0
    [message] = _warnings(caplog)
    assert "2 Baustelle(n) nicht eingetragen (RuntimeError: Platte voll" in message
    assert "\n" not in message


def test_a_failing_site_costs_no_other_its_row(stats_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    real = stats_baustellen.baustelle_row

    def picky(event: Any, *, seen_at: datetime) -> tuple[str, ...] | None:
        if event["guid"] == "b":
            raise ValueError("unlesbar")
        return real(event, seen_at=seen_at)

    monkeypatch.setattr(stats_baustellen, "baustelle_row", picky)
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    # Entries that are no mappings are passed over without a word.
    events: list[Any] = [None, "Burggasse", 7, _event(guid="a"), _event(guid="b"), _event(guid="c")]

    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2

    assert known_guids(2026, stats_dir=stats_dir) == {"a", "c"}
    assert [m for m in _warnings(caplog) if "1 Baustelle(n) nicht eingetragen (ValueError: unlesbar)" in m]


def test_events_that_cannot_be_iterated_do_not_raise(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    def breaking() -> Iterator[dict[str, Any]]:
        yield _event(guid="a")
        raise OSError("Quelle\nweg")

    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    nothing: Any = None
    assert append_baustellen_rows(nothing, now=NOW, stats_dir=stats_dir) == 0
    assert any("Zeilen konnten nicht geschrieben werden (TypeError" in m for m in _warnings(caplog))
    # The rows written before an iterable breaks are kept and counted.
    assert append_baustellen_rows(breaking(), now=NOW, stats_dir=stats_dir) == 1
    assert known_guids(2026, stats_dir=stats_dir) == {"a"}
    assert "(OSError: Quelle" in _warnings(caplog)[-1] and all("\n" not in m for m in _warnings(caplog))


def test_rows_held_back_by_the_feed_build_are_not_written_here(stats_dir: Path) -> None:
    """Inside ``rows_held_back`` the row follows that block's rule (``src/utils/stats.py``)."""
    with stats_utils.rows_held_back() as held:
        assert append_baustellen_rows([_event(), _event()], now=NOW, stats_dir=stats_dir) == 1
    assert not stats_dir.exists()
    assert len(held) == 1
    held.write()
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}


# --- test isolation ---------------------------------------------------------


def _real_ledgers() -> list[tuple[str, int, int]]:
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in REAL_STATS_DIR.glob("baustellen_*"))


def test_default_folder_is_read_at_call_time_and_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The autouse fixture ``isolate_stats_writes`` replaces ``src.utils.stats.DEFAULT_STATS_DIR``;
    # a module that bound the constant at import time would still write to the real ``data/stats``.
    assert stats_utils.DEFAULT_STATS_DIR == tmp_path / "stats"
    assert not hasattr(stats_baustellen, "DEFAULT_STATS_DIR")
    before = _real_ledgers()

    assert append_baustellen_rows([_event()], now=NOW) == 1

    assert known_guids(2026) == {GUID}
    assert [p.name for p in (tmp_path / "stats").iterdir()] == ["baustellen_2026.csv"]
    assert _real_ledgers() == before
    # A later change of the constant is followed as well.
    monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", tmp_path / "anderswo")
    assert append_baustellen_rows([_event()], now=NOW) == 1
    assert (tmp_path / "anderswo" / "baustellen_2026.csv").is_file()


# --- the hook in scripts/update_baustellen_cache.py --------------------------


_FEATURES = [
    _site("Rennweg von 33A bis 37", "Der linke Fahrstreifen wird gesperrt."),
    _site("Hauptstraße", "Die Buslinie 7A wird über die Rothenhofgasse umgeleitet."),
    _site("Gasse 1", "Die Haltestelle der Linie 13A wird verlegt."),
]


class _Run:
    """What one ``main()`` did: the cache writes and the ledger calls, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: list[str] = []
        self.cached: list[list[dict[str, Any]]] = []
        self.ledger: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        monkeypatch.setattr(update_baustellen_cache, "write_cache", self._write_cache)
        monkeypatch.setattr(update_baustellen_cache, "append_baustellen_rows", self._append)

    def _write_cache(self, _provider: str, items: list[dict[str, Any]], **_kw: Any) -> None:
        self.order.append("cache")
        self.cached.append(items)

    def _append(self, *args: Any, **kwargs: Any) -> int:
        """Record the call and let the real writer run (into ``tmp_path``)."""
        self.order.append("ledger")
        self.ledger.append((args, kwargs))
        return append_baustellen_rows(*args, **kwargs)


def _live_answer(monkeypatch: pytest.MonkeyPatch, features: list[dict[str, Any]]) -> None:
    payload = {"type": "FeatureCollection", "features": features}
    monkeypatch.setattr(update_baustellen_cache, "_fetch_layers", lambda *_a: update_baustellen_cache._collect_events(payload))


def _written_ledger(tmp_path: Path) -> list[dict[str, str]]:
    """The records of the one ledger file ``main()`` wrote (whatever the year is today)."""
    [path] = (tmp_path / "stats").iterdir()
    assert path.name.startswith("baustellen_") and path.suffix == ".csv"
    return _records(path)


def _no_live_answer(monkeypatch: pytest.MonkeyPatch, *, cache_exists: bool, fallback: str) -> None:
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", lambda *_a: None)
    monkeypatch.setattr(update_baustellen_cache, "_log_endpoint_diagnostic", lambda *a, **k: None)
    monkeypatch.setattr(update_baustellen_cache, "_cache_exists", lambda: cache_exists)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", fallback)


def _real_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")
    _real_cache_file(monkeypatch, target)
    return target


@pytest.mark.parametrize(
    ("fallback_layers", "exit_code"),
    # Exit 3: one layer came from its last good answer. Its sites are still
    # the city's own (not the demo sample), so they are entered as well.
    [([], 0), (["BAUSTELLENLINOGD"], 3)],
    ids=["live", "one-layer-from-its-last-good-answer"],
)
def test_hook_gets_the_sites_of_the_city_after_the_cache_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fallback_layers: list[str], exit_code: int
) -> None:
    run = _Run(monkeypatch)
    _live_answer(monkeypatch, _FEATURES)
    monkeypatch.setattr(update_baustellen_cache, "_fallback_layers", fallback_layers)

    assert update_baustellen_cache.main() == exit_code

    assert run.order == ["cache", "ledger"]
    args, kwargs = run.ledger[0]
    # Exactly what went into the cache: the sites that passed the transit rule.
    assert len(args) == 1 and args[0] is run.cached[0]
    assert kwargs == {}
    assert sorted(item["title"] for item in args[0]) == ["Gasse 1", "Hauptstraße"]
    assert len(_written_ledger(tmp_path)) == 2


def test_live_answer_ends_in_the_ledger_and_changes_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The real writer behind the real ``main()``: cache, exit code and log as before."""
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    before = _real_ledgers()
    caplog.set_level(logging.INFO)

    assert update_baustellen_cache.main() == 0
    assert update_baustellen_cache.main() == 0  # the next tick adds nothing

    records = _written_ledger(tmp_path)
    by_title = {r["title"]: r for r in records}
    assert sorted(by_title) == ["Gasse 1", "Hauptstraße"] and len(records) == 2
    # ``_site``: OBJEKT_BEGINN "2026-09-30Z" is Vienna midnight of 1 October.
    assert (by_title["Gasse 1"]["starts_at"], by_title["Gasse 1"]["ends_at"]) == ("2026-10-01", "2026-11-15")
    assert by_title["Gasse 1"]["description"].startswith("Die Haltestelle der Linie 13A wird verlegt. Beginn: ")
    cached = json.loads(target.read_text(encoding="utf-8"))
    assert sorted(item["guid"] for item in cached) == sorted(r["guid"] for r in records)
    assert _real_ledgers() == before
    script_lines = [r.getMessage() for r in caplog.records if r.name == SCRIPT_LOGGER]
    assert script_lines.count("Baustellen: Cache mit 2 Einträgen aktualisiert.") == 2
    assert [r for r in caplog.records if r.name in (LEDGER_LOGGER, "utils.stats")] == []


@pytest.mark.parametrize(
    ("cache_exists", "fallback", "exit_code", "cache_writes"),
    [
        # Exit 2: the bundled sample is written to an empty checkout's cache;
        # its two sites from 2025 are no answer of the city.
        (False, str(SAMPLE_PATH), 2, 1),
        (True, str(SAMPLE_PATH), 1, 0),
        # Inside the repository (a path outside falls back to the bundled
        # sample), and not there: only looked up, never written.
        (False, "data/samples/gibt_es_nicht_baustellen.geojson", 1, 0),
    ],
    ids=["demo-sample", "cache-kept", "no-sample"],
)
def test_hook_is_not_called_without_a_live_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cache_exists: bool, fallback: str, exit_code: int, cache_writes: int
) -> None:
    run = _Run(monkeypatch)
    _no_live_answer(monkeypatch, cache_exists=cache_exists, fallback=fallback)

    assert update_baustellen_cache.main() == exit_code
    assert len(run.cached) == cache_writes
    assert run.ledger == []
    assert not (tmp_path / "stats").exists()


def test_demo_sample_refused_by_the_cache_guard_is_not_entered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``write_cache`` refuses the sample (``DataDegradationError``): exit 1, no ledger call."""
    target = _real_cache(monkeypatch, tmp_path)
    target.write_text(json.dumps([{"guid": f"old-{i}"} for i in range(30)]), encoding="utf-8")
    calls: list[Any] = []
    monkeypatch.setattr(update_baustellen_cache, "append_baustellen_rows", calls.append)
    _no_live_answer(monkeypatch, cache_exists=False, fallback=str(SAMPLE_PATH))
    assert update_baustellen_cache.main() == 1
    assert calls == []


@pytest.mark.parametrize(
    ("features", "exit_code", "order"),
    [
        # The city delivers no site at all: an outage, the cache is kept.
        ([], 1, []),
        # Sites delivered, none with a transit effect: nothing to enter.
        ([_site("Gasse 9", "Die Fahrbahn wird eingeengt.")], 0, ["cache", "ledger"]),
    ],
    ids=["no-site", "empty-selection"],
)
def test_nothing_to_enter_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, features: list[dict[str, Any]], exit_code: int, order: list[str]
) -> None:
    run = _Run(monkeypatch)
    _live_answer(monkeypatch, features)

    assert update_baustellen_cache.main() == exit_code
    assert run.order == order
    assert not (tmp_path / "stats").exists()


@pytest.mark.parametrize("failure", ["folder", "writer", "header"])
def test_a_ledger_failure_never_changes_the_run(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    if failure == "folder":
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", blocker / "stats")
    elif failure == "writer":
        monkeypatch.setattr(stats_baustellen, "_append_row", _raise_runtime_error)
    else:
        (tmp_path / "stats").mkdir()
        this_year = datetime.now(VIENNA_TZ).year
        for year in (this_year, this_year + 1):  # the run may cross New Year
            _ledger(tmp_path / "stats", year).write_text("fremd\n", encoding="utf-8")
    caplog.set_level(logging.INFO)

    assert update_baustellen_cache.main() == 0

    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2
    script_lines = [r.getMessage() for r in caplog.records if r.name == SCRIPT_LOGGER]
    assert script_lines[-1] == "Baustellen: Cache mit 2 Einträgen aktualisiert."
    # The failure is said once, by the statistics loggers, at WARNING.
    said = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(said) == 1 and said[0].name in (LEDGER_LOGGER, "utils.stats")
