"""The yearly Baustellen ledger: the cases of the review of 2026-11-10.

Second file next to ``tests/test_baustellen_ledger.py`` (at the 700-line
limit of ``tests/test_file_line_limit.py``), with its helpers. Each test
names what the writer in ``src/utils/stats_baustellen.py`` did before:

* a site that ended last year got a row in the new year's file,
* a damaged line counted as known while the reader of the year page
  dropped it, so the site was never entered again,
* a last line without its line break took the next row onto itself,
* a site without a text of the city kept a generated line as its text,
* a byte order mark or an empty first line made the file foreign,
* title, district and measure were cut at 200 characters without a mark.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest

from scripts import update_baustellen_cache
from src.markdown_stats.baustellen_year import load_sites
from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import append_baustellen_rows, baustelle_row, known_guids
from tests.test_baustellen_ledger import (
    _FEATURES,
    ENDS_2027,
    GUID,
    HEADER_LINE,
    LEDGER_LOGGER,
    LINE,
    NOW,
    _event,
    _ledger,
    _live_answer,
    _real_cache,
    _records,
    _row,
    _warnings,
)

# The first cache write of 2027 in Vienna (the cron's first tick after midnight).
NEW_YEAR = datetime(2027, 1, 1, 0, 1, 15, tzinfo=VIENNA_TZ)
GENERATED = "Beginn: 28.07.2026 00:00 Uhr \nGeplant bis: 30.10.2026 00:00 Uhr \nMaßnahme: Straßenbau \nBezirk: 7"


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


def _titles(stats_dir: Path, year: int = 2026) -> list[str]:
    """The sites the year page would list, read by its own reader."""
    return [site.title for site in load_sites(year, stats_dir=stats_dir)]


# --- a site and the year it is entered in ------------------------------------


def test_a_site_still_running_gets_a_row_in_the_new_year_too(stats_dir: Path) -> None:
    """Moved here from ``tests/test_baustellen_ledger.py``; the site now ends in 2027."""
    old_year = datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA_TZ)
    new_year = datetime(2027, 1, 1, 0, 0, tzinfo=VIENNA_TZ)
    event = _event(ends_at=ENDS_2027)
    assert append_baustellen_rows([event], now=old_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([event], now=new_year, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([event], now=new_year, stats_dir=stats_dir) == 0
    assert known_guids(2026, stats_dir=stats_dir) == {GUID} == known_guids(2027, stats_dir=stats_dir)
    assert _records(_ledger(stats_dir, 2026))[0]["timestamp"] == "2026-12-31T23:59:00+01:00"
    assert _records(_ledger(stats_dir, 2027))[0]["timestamp"] == "2027-01-01T00:00:00+01:00"


@pytest.mark.parametrize(
    ("ends_at", "written"),
    [
        # The city can still deliver a site on the day after its last day.
        ("2026-12-31T00:00:00+01:00", 0),
        ("2026-12-31T23:59:59+01:00", 0),
        ("2026-09-04T00:00:00+02:00", 0),
        (datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC), 0),
        # The Vienna date counts: 23:30 UTC on 31 December is 1 January in Vienna.
        ("2026-12-31T23:30:00+00:00", 1),
        ("2027-01-01T00:00:00+01:00", 1),
        (ENDS_2027, 1),
        # An open end, and what the reader of the year page shows as one.
        (None, 1), ("", 1), ("demnächst", 1), ("1999-12-31T00:00:00+01:00", 1),
    ],
)
def test_a_site_that_ended_before_the_year_gets_no_row(ends_at: Any, written: int, stats_dir: Path) -> None:
    event = _event(ends_at=ends_at)
    assert append_baustellen_rows([event, _event(guid="läuft", ends_at=ENDS_2027)], now=NEW_YEAR, stats_dir=stats_dir) == written + 1
    assert known_guids(2027, stats_dir=stats_dir) == ({GUID, "läuft"} if written else {"läuft"})
    # In the year it ran in, the same site is entered, on its last day too.
    last_day = datetime(2026, 12, 31, 23, 59, 59, tzinfo=VIENNA_TZ)
    assert append_baustellen_rows([event], now=last_day, stats_dir=stats_dir) == 1
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}


def test_only_ended_sites_leave_no_file_and_no_log_line(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NEW_YEAR, stats_dir=stats_dir) == 0
    assert not stats_dir.exists()
    assert [r for r in caplog.records if r.name == LEDGER_LOGGER] == []


def test_hook_at_new_year_keeps_the_cache_and_enters_no_ended_site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``_site`` ends on 2026-11-15: after New Year the cache still holds what the city sent, the ledger of 2027 nothing."""
    target = _real_cache(monkeypatch, tmp_path)
    _live_answer(monkeypatch, _FEATURES)
    monkeypatch.setattr(
        update_baustellen_cache, "append_baustellen_rows", lambda events: append_baustellen_rows(events, now=NEW_YEAR)
    )
    caplog.set_level(logging.WARNING)

    assert update_baustellen_cache.main() == 0

    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2
    assert not (tmp_path / "stats").exists()
    assert _warnings(caplog) == []


class _ClockAtNewYear(datetime):
    """A clock that stands at 23:30 UTC on 31 December 2026: 00:30 on 1 January 2027 in Vienna."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(datetime(2026, 12, 31, 23, 30, tzinfo=UTC).timestamp(), tz)


def test_without_now_the_clock_gives_the_vienna_year_and_the_time_stamp(stats_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook passes no ``now``; the hook tests pin it, so the default is checked here against a clock that stands still."""
    monkeypatch.setattr(stats_baustellen, "datetime", _ClockAtNewYear)
    assert append_baustellen_rows([_event(), _event(guid="läuft", ends_at=ENDS_2027)], stats_dir=stats_dir) == 1
    assert [p.name for p in stats_dir.iterdir()] == ["baustellen_2027.csv"]
    [record] = _records(_ledger(stats_dir, 2027))
    assert (record["timestamp"], record["weekday"], record["hour"], record["guid"]) == ("2027-01-01T00:30:00+01:00", "Fr", "00", "läuft")


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


# --- the text of the city -------------------------------------------------------


def test_a_site_without_a_text_of_the_city_has_an_empty_description() -> None:
    assert _row(_event(description=GENERATED))["description"] == ""
    assert _row(_event(description="Maßnahme: Straßenbau \nBezirk: 7"))["description"] == ""
    assert _row(_event(description="Geplant bis: 30.10.2026 00:00 Uhr\n"))["description"] == ""
    # The same through the provider: a feature that passes the transit rule by its title alone.
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [16.37, 48.2]},
        "properties": {
            "BEZEICHNUNG": "Haltestelle Burggasse verlegt", "OBJEKT_BEGINN": "2026-07-27Z", "OBJEKT_ENDE": "2026-10-30Z",
            "BEZIRK": 7, "BEHINDERUNGSART": "Straßenbau",
        },
    }
    [event] = update_baustellen_cache._collect_events({"type": "FeatureCollection", "features": [feature]})
    assert event["description"].startswith("Beginn: 28.07.2026 00:00 Uhr")
    row = _row(event)
    assert (row["title"], row["starts_at"], row["measure"], row["description"]) == (
        "Haltestelle Burggasse verlegt", "2026-07-28", "Straßenbau", "",
    )


def test_measure_and_district_with_a_line_break_are_cut_from_the_text() -> None:
    context = {"measure": "Straßen-\nbau", "district": "7\n8"}
    description = (
        "Buslinie 5 fährt nicht. \nBeginn: 28.07.2026 00:00 Uhr \nGeplant bis: 30.10.2026 00:00 Uhr \n"
        "Maßnahme: Straßen-\nbau \nBezirk: 7\n8"
    )
    row = _row(_event(description=description, context=context))
    assert (row["description"], row["measure"], row["district"]) == ("Buslinie 5 fährt nicht.", "Straßen- bau", "7 8")
    # A line break before the last word of a sentence of the city is no generated line.
    kept = _row(_event(description="Die Linie fährt nicht im\n8\nBezirk: 7\n8", context=context))
    assert kept["description"] == "Die Linie fährt nicht im 8"


@pytest.mark.parametrize(
    "own",
    [
        "Linie 13A umgeleitet.\nBeginn: der Umleitung ist im August.",
        "Linie 13A umgeleitet.\nGeplant bis: Ende Oktober.",
        "Linie 13A.\nGeplant bis: Ende Mai.\nBeginn: im Mai.",
        "Beginn: 28.07.2026 00:00 Uhr an der Haltestelle.",
        "Gesperrt ab Beginn: 28.07.2026 00:00 Uhr",
        "Die Haltestelle liegt im Bezirk: 7",
        "Maßnahme: Straßenbau und Gleisbau",
    ],
)
def test_a_sentence_of_the_city_above_the_generated_lines_stays(own: str) -> None:
    """Only the provider's own lines go: its date format, and measure and district as the context names them."""
    for generated in (GENERATED, "Beginn: 28.07.2026 00:00 Uhr \nBezirk: 7", "Maßnahme: Straßenbau"):
        assert _row(_event(description=f"{own} \n{generated}"))["description"] == " ".join(own.split())


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


# --- the 200 characters of the short fields ----------------------------------------


def test_a_cut_short_field_ends_with_the_mark(stats_dir: Path) -> None:
    whole = _row(_event(title="T" * 200, context={"district": "D" * 200, "measure": "M" * 200}))
    assert (whole["title"], whole["district"], whole["measure"]) == ("T" * 200, "D" * 200, "M" * 200)
    cut = _row(_event(title="T" * 201, context={"district": "D" * 300, "measure": "M" * 198 + " und Gleisbau"}))
    assert (cut["title"], cut["district"]) == ("T" * 199 + "…", "D" * 199 + "…")
    # No space before the mark, as in a shortened description.
    assert cut["measure"] == "M" * 198 + "…"
    # The formula guard's apostrophe comes on top, as before: 201 characters.
    assert _row(_event(title="=" + "a" * 300))["title"] == "'=" + "a" * 198 + "…"
    # A guid is cut the same way on every run, so the site is still entered once.
    long_guid = _event(guid="g" * 300)
    assert baustelle_row(long_guid, seen_at=NOW)[3] == "g" * 199 + "…"  # type: ignore[index]
    assert append_baustellen_rows([long_guid], now=NOW, stats_dir=stats_dir) == 1
    assert append_baustellen_rows([long_guid], now=NOW, stats_dir=stats_dir) == 0
    assert known_guids(2026, stats_dir=stats_dir) == {"g" * 199 + "…"}
