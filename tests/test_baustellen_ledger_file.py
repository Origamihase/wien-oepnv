"""The yearly Baustellen ledger: the file, and reading an existing one.

Header and lines of the file, which guids count as known, and what happens
to a file that is damaged, foreign or saved by a spreadsheet. Of the review
of 2026-11-10, each test
names what the writer in ``src/utils/stats_baustellen.py`` did before:

* a damaged line counted as known while the reader of the year page
  dropped it, so the site was never entered again,
* a last line without its line break took the next row onto itself,
* a byte order mark or an empty first line made the file foreign,
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from src.markdown_stats.baustellen_year import load_sites
from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import (
    MAX_ROW_BYTES,
    append_baustellen_rows,
    known_guids,
)
from tests._baustellen_ledger_support import (
    ENDS_2027,
    GUID,
    HEADER_LINE,
    LEDGER_LOGGER,
    LINE,
    NOW,
    _event,
    _ledger,
    _records,
    _row,
    _warnings,
)


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


def _titles(stats_dir: Path, year: int = 2026) -> list[str]:
    """The sites the year page would list, read by its own reader."""
    return [site.title for site in load_sites(year, stats_dir=stats_dir)]


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
    ],
    ids=["other-ledger", "column-missing", "column-added", "other-case", "no-header", "blank"],
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
    assert append_baustellen_rows([_event(ends_at=ENDS_2027)], now=new_year, stats_dir=stats_dir) == 1


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


# --- lines the reader of the year page drops ----------------------------------


_SITE_LINE = "2026-11-10T17:44:05+01:00,Di,17,{guid},Burggasse 67,2026-07-28,2026-10-30,7,Straßenbau,"


@pytest.mark.parametrize("ending", ["\r\n", "\n", ""], ids=["crlf", "lf", "no-line-break"])
@pytest.mark.parametrize(
    "damaged",
    [
        _SITE_LINE + '"Die Haltestelle wird',
        _SITE_LINE + '"Text" x',
        _SITE_LINE.replace("2026-11-10T17:44:05+01:00", "gestern") + "Text",
        _SITE_LINE.replace("2026-11-10T17:44:05+01:00", "1899-11-10T17:44:05+01:00") + "Text",
        _SITE_LINE.replace("Burggasse 67", " ") + "Text",
    ],
    ids=["open-quote-in-the-text", "text-after-the-quote", "time-stamp", "implausible-year", "no-title"],
)
def test_a_line_the_reader_drops_is_not_known_and_its_site_is_entered_again(damaged: str, ending: str, stats_dir: Path) -> None:
    stats_dir.mkdir()
    content = "\r\n".join([HEADER_LINE, LINE.format(guid="eins"), damaged.format(guid=GUID)]) + ending
    _ledger(stats_dir).write_text(content, encoding="utf-8", newline="")
    assert _titles(stats_dir) == ["Titel"]
    assert known_guids(2026, stats_dir=stats_dir) == {"eins"}

    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 1

    # Whole again for both sides, with the first run: the open last line was ended before the row.
    assert _titles(stats_dir) == ["Burggasse 67", "Titel"]
    assert known_guids(2026, stats_dir=stats_dir) == {"eins", GUID}
    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 0
    assert _ledger(stats_dir).read_bytes().startswith(content.encode("utf-8"))


def test_a_guid_with_spaces_around_it_is_the_guid_the_reader_sees(stats_dir: Path) -> None:
    stats_dir.mkdir()
    _ledger(stats_dir).write_text(HEADER_LINE + "\n" + LINE.format(guid=f"  {GUID} ") + "\n", encoding="utf-8", newline="")
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}
    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 0


# --- a last line without its line break ----------------------------------------


@pytest.mark.parametrize("cut", [2, 1], ids=["crlf-missing", "lf-missing"])
def test_a_last_line_without_line_break_does_not_take_the_next_row(cut: int, stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    old = [_event(guid="alt-1", title="Gasse 1"), _event(guid="alt-2", title='Gasse "2", Ost')]
    assert append_baustellen_rows(old, now=NOW, stats_dir=stats_dir) == 2
    whole = _ledger(stats_dir).read_bytes()
    assert whole.endswith(b"\r\n")
    _ledger(stats_dir).write_bytes(whole[:-cut])  # an editor that leaves the last line open
    caplog.set_level(logging.WARNING)

    # Nothing new: the file is not touched, the line break waits for the next row.
    assert append_baustellen_rows(old, now=NOW, stats_dir=stats_dir) == 0
    assert _ledger(stats_dir).read_bytes() == whole[:-cut]

    new = [_event(guid="neu-1", title="Gasse 3"), _event(guid="neu-2", title="Gasse 4")]
    assert append_baustellen_rows(old + new, now=NOW, stats_dir=stats_dir) == 2

    assert known_guids(2026, stats_dir=stats_dir) == {"alt-1", "alt-2", "neu-1", "neu-2"}
    assert _titles(stats_dir) == ['Gasse "2", Ost', "Gasse 1", "Gasse 3", "Gasse 4"]
    data = _ledger(stats_dir).read_bytes()
    # The old bytes stay, one line break ends the open line, every record is one line.
    assert data.startswith(whole[:-cut] + b"\n") and data.endswith(b"\r\n")
    assert len(data.splitlines()) == 5 and b"" not in data.splitlines()
    assert _warnings(caplog) == []


def test_a_whole_file_gets_no_extra_line_break(stats_dir: Path) -> None:
    assert append_baustellen_rows([_event(guid="a")], now=NOW, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([_event(guid="b")], now=NOW, stats_dir=stats_dir) == 1
    assert _ledger(stats_dir).read_bytes().count(b"\n") == 3
    assert b"\r\n\r\n" not in _ledger(stats_dir).read_bytes() and b"\n\n" not in _ledger(stats_dir).read_bytes()


def test_rows_held_back_meet_an_ended_last_line_when_they_are_written(stats_dir: Path) -> None:
    """Inside ``rows_held_back`` the row waits; the open line is ended at once, so the later write starts a line."""
    assert append_baustellen_rows([_event(guid="alt")], now=NOW, stats_dir=stats_dir) == 1
    whole = _ledger(stats_dir).read_bytes()
    _ledger(stats_dir).write_bytes(whole[:-2])
    with stats_utils.rows_held_back() as held:
        assert append_baustellen_rows([_event(guid="neu")], now=NOW, stats_dir=stats_dir) == 1
    assert _ledger(stats_dir).read_bytes() == whole[:-2] + b"\n"
    held.write()
    assert known_guids(2026, stats_dir=stats_dir) == {"alt", "neu"}


# --- a file a spreadsheet saved ---------------------------------------------------


@pytest.mark.parametrize("prefix", ["﻿", "\r\n", "\n \n\n", "﻿\r\n"], ids=["bom", "blank-line", "blank-lines", "bom-and-blank-line"])
def test_bom_and_empty_first_lines_do_not_make_the_ledger_foreign(prefix: str, stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    stats_dir.mkdir()
    content = prefix + HEADER_LINE + "\r\n" + LINE.format(guid="eins") + "\r\n"
    _ledger(stats_dir).write_text(content, encoding="utf-8", newline="")
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)

    assert known_guids(2026, stats_dir=stats_dir) == {"eins"}
    events = [_event(guid="eins"), _event()]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 1
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 0

    assert known_guids(2026, stats_dir=stats_dir) == {"eins", GUID}
    data = _ledger(stats_dir).read_bytes()
    assert data.startswith(content.encode("utf-8")) and data.count(HEADER_LINE.encode("utf-8")) == 1
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "content",
    ["﻿", "\n\n", "﻿\n", "﻿timestamp,weekday,hour,provider\n", "\n" + LINE.format(guid="eins") + "\n" + HEADER_LINE + "\n"],
    ids=["only-bom", "only-blank-lines", "bom-and-blank-line", "bom-and-other-header", "header-below-a-row"],
)
def test_a_file_without_the_header_stays_refused(content: str, stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Appended to, such a file would hold rows under no header or under another one."""
    stats_dir.mkdir()
    _ledger(stats_dir).write_text(content, encoding="utf-8", newline="")
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    assert append_baustellen_rows([_event()], now=NOW, stats_dir=stats_dir) == 0
    assert _ledger(stats_dir).read_text(encoding="utf-8") == content
    assert ["andere Kopfzeile" in m for m in _warnings(caplog)] == [True]
