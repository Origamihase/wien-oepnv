"""The yearly Baustellen ledger ``data/stats/baustellen_<year>.csv``.

Owner's decision of 2026-11-10: the page of a finished year summarises the
year's Baustellen. The rows for it are written when the Baustellen cache is
written from the city's answer (``scripts/update_baustellen_cache.py``),
one per site and year (``src/utils/stats_baustellen.py``).

This file holds the row of one site (``baustelle_row``): its fields, caps
and the text of the city. The other subjects have their own files:
``test_baustellen_ledger_file.py`` (the file and reading it again),
``test_baustellen_ledger_year.py`` (the year a site is entered in),
``test_baustellen_ledger_failures.py``, ``test_baustellen_ledger_isolation.py``
and ``test_baustellen_ledger_hook.py`` (the hook in the cache script).
Of the review of 2026-11-10, each test
names what the writer in ``src/utils/stats_baustellen.py`` did before:

* a site without a text of the city kept a generated line as its text,
* title, district and measure were cut at 200 characters without a mark.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import (
    BAUSTELLEN_HEADER,
    MAX_DESCRIPTION_CHARS,
    MAX_ROW_BYTES,
    append_baustellen_rows,
    baustelle_row,
    known_guids,
)
from tests._baustellen_ledger_support import (
    GUID,
    NOW,
    _event,
    _ledger,
    _records,
    _row,
)


GENERATED = "Beginn: 28.07.2026 00:00 Uhr \nGeplant bis: 30.10.2026 00:00 Uhr \nMaßnahme: Straßenbau \nBezirk: 7"


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
        # Without the provider's last lines ("Beginn: …", "Bezirk: 7"): columns hold them.
        "Die Haltestelle wird von Burggasse ONr. 67 nach Burggasse ONr. 69 verlegt.",
    )
    kept = _event(description="Beginn: der Arbeiten ist im Mai. \nBezirk: 7 betroffen.\nBezirk: 7")
    assert baustelle_row(kept, seen_at=NOW)[-1] == "Beginn: der Arbeiten ist im Mai. Bezirk: 7 betroffen."  # type: ignore[index]
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
