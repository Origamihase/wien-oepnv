"""Writer and reader of the Baustellen ledger read every line alike.

The writer (``src/utils/stats_baustellen.py``) enters a site again unless
the ledger already holds a line for its guid; the reader of the year page
(``src/markdown_stats/baustellen_year.py``) lists the sites of the lines it
can read. Both now take the same test of a line (``site_line``). Before
the review of 2026-11-10:

* the reader dropped every line with a U+FFFD while the writer counted it
  as known, so a site whose text held one (from the city, or a byte of a
  cut append) was never shown and never entered again,
* the provider's placeholder "Baustelle ohne weitere Angaben" was stored as
  if the city had written it,
* the same damaged line was logged on every read of one run.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.markdown_stats import baustellen_year as by
from src.utils.stats_baustellen import NO_DETAILS_TEXT, append_baustellen_rows, known_guids, site_line
from tests.test_baustellen_ledger import GUID, HEADER_LINE, LINE, NOW, _event, _ledger, _row

STATS_LOGGER = "generate_markdown_stats"


@pytest.fixture(autouse=True)
def _fresh_log_memory() -> Iterator[None]:
    by.forget_reported()
    yield
    by.forget_reported()


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


def _listed(stats_dir: Path) -> dict[str, tuple[str, ...]]:
    """Title and texts of every site the year page lists, read by its own reader."""
    return {site.title: site.descriptions for site in by.load_sites(2026, stats_dir=stats_dir)}


# --- a U+FFFD in the city's text ------------------------------------------------------


def test_a_real_replacement_character_keeps_its_site_on_both_sides(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Valid UTF-8 in the file; the reader dropped the line as unreadable and the writer never wrote it again."""
    events = [
        _event(guid="g-fffd", title="Stra�e 5", description="Die Haltestelle in der Stra�e wird verlegt."),
        _event(guid="g-ok", title="Burggasse 67"),
    ]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2
    assert "�".encode() in _ledger(stats_dir).read_bytes()
    with caplog.at_level(logging.WARNING):
        listed = _listed(stats_dir)
    assert listed == {
        "Stra�e 5": ("Die Haltestelle in der Stra�e wird verlegt.",),
        "Burggasse 67": ("Die Haltestelle wird von Burggasse ONr. 67 nach Burggasse ONr. 69 verlegt.",),
    }
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert known_guids(2026, stats_dir=stats_dir) == {"g-fffd", "g-ok"}
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 0
    # The page shows the character as it stands, like any other.
    page = "\n".join(by.render_baustellen_section(by.load_sites(2026, stats_dir=stats_dir), year=2026))
    assert "| Stra�e 5 |" in page


def test_a_byte_cut_in_an_unquoted_cell_keeps_its_site_on_both_sides(stats_dir: Path) -> None:
    """An append cut inside the "ß" of the last, unquoted cell: the line still reads as a record."""
    events = [_event(guid="g1", title="Linie 13A hält in der Burggasse", description="Die Haltestelle wird verlegt."),
              _event(guid="g2", title="Linie 49 fährt über die Westbahnstraße", description="Umleitung über die Straße")]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2
    raw = _ledger(stats_dir).read_bytes()
    _ledger(stats_dir).write_bytes(raw[: raw.rindex("ß".encode()) + 1])
    assert _listed(stats_dir)["Linie 49 fährt über die Westbahnstraße"] == ("Umleitung über die Stra�",)
    assert known_guids(2026, stats_dir=stats_dir) == {"g1", "g2"}
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 0


def test_a_byte_cut_in_a_quoted_cell_is_dropped_by_both_and_entered_again(
    stats_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The quotation mark is not closed: no record, counted by the reader, written again by the writer."""
    events = [_event(guid="g1", title="Burggasse 67"), _event(guid="g2", title="Gürtel 5", description="Straße, gesperrt")]
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2
    raw = _ledger(stats_dir).read_bytes()
    _ledger(stats_dir).write_bytes(raw[: raw.rindex("ß".encode()) + 1])
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        assert list(_listed(stats_dir)) == ["Burggasse 67"]
    assert [record.getMessage().endswith(": 1 unlesbare Zeile(n) übersprungen.") for record in caplog.records] == [True]
    assert known_guids(2026, stats_dir=stats_dir) == {"g1"}
    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 1
    assert _listed(stats_dir)["Gürtel 5"] == ("Straße, gesperrt",)


_SITE = "2026-11-10T17:44:05+01:00,Di,17,{guid},Burggasse 67,2026-07-28,2026-10-30,7,Straßenbau,"


@pytest.mark.parametrize(
    "line",
    [
        _SITE + "Text",
        _SITE + "Stra�e",
        _SITE.replace("Burggasse", "Burg�gasse") + "Text",
        _SITE + '"Text mit offenem',
        _SITE + '"Text" danach',
        _SITE + "Text,eine Zelle zu viel",
        _SITE.replace("2026-11-10T17:44:05+01:00", "2026-11-10T17:44:05+01:0�") + "Text",
        _SITE.replace("2026-11-10T17:44:05+01:00", "1899-11-10T17:44:05+01:00") + "Text",
        _SITE.replace("2026-11-10T17:44:05+01:00", "2026-11-10T17:44:05") + "Text",
        _SITE.replace("Burggasse 67", "�") + "Text",
        _SITE.replace("Burggasse 67", " ") + "Text",
        _SITE.replace("{guid}", " ") + "Text",
        _SITE + "x" * 131_073,
    ],
    ids=[
        "whole", "fffd-text", "fffd-title", "open-quote", "text-after-quote", "eleven-cells", "fffd-time-stamp",
        "implausible-year", "naive-time-stamp", "title-only-fffd", "no-title", "no-guid", "field-above-the-csv-limit",
    ],
)
def test_the_writer_knows_a_line_exactly_when_the_reader_lists_it(line: str, stats_dir: Path) -> None:
    stats_dir.mkdir()
    _ledger(stats_dir).write_text(f"{HEADER_LINE}\n{LINE.format(guid='eins')}\n{line.format(guid=GUID)}\n", encoding="utf-8")
    listed = {row.guid for row in by._read_rows(_ledger(stats_dir))}
    known = known_guids(2026, stats_dir=stats_dir)
    assert known == listed
    assert (site_line(line.format(guid=GUID)) is not None) == (GUID in known)


def test_the_header_again_is_no_site_and_no_damage(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    stats_dir.mkdir()
    _ledger(stats_dir).write_text(f"{HEADER_LINE}\n{LINE.format(guid='eins')}\n{HEADER_LINE}\n\n", encoding="utf-8")
    assert site_line(HEADER_LINE) is None
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        assert len(by.load_sites(2026, stats_dir=stats_dir)) == 1
    assert caplog.records == []
    assert known_guids(2026, stats_dir=stats_dir) == {"eins"}


# --- the provider's placeholder ------------------------------------------------------------


def test_the_providers_placeholder_is_no_text_of_the_city() -> None:
    """A feature with nothing but its title: the provider writes its own sentence as the description."""
    assert NO_DETAILS_TEXT == "Baustelle ohne weitere Angaben"
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [16.37, 48.2]},
        "properties": {"BEZEICHNUNG": "Haltestelle Burggasse verlegt"},
    }
    [event] = update_baustellen_cache._collect_events({"type": "FeatureCollection", "features": [feature]})
    assert event["description"] == NO_DETAILS_TEXT
    assert _row(event)["description"] == ""
    assert _row(_event(description=f" {NO_DETAILS_TEXT} \n"))["description"] == ""
    # A sentence of the city that merely holds the words stays.
    own = f"{NO_DETAILS_TEXT} zur Dauer; die Haltestelle wird verlegt."
    assert _row(_event(description=own))["description"] == own


@pytest.mark.parametrize("description", [None, 5, ["Text"]])
def test_a_description_that_is_no_text_gives_an_empty_one(description: Any) -> None:
    assert _row(_event(description=description))["description"] == ""


# --- each finding once per run ----------------------------------------------------------


def test_a_ledger_read_several_times_in_one_run_is_reported_once(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The page, the request pass and the overdue check each read the ledger; each repeated the lines."""
    stats_dir.mkdir()
    lines = [HEADER_LINE, LINE.format(guid="eins"), "nur,drei,Zellen",
             "2026-01-01T00:31:00+01:00,Do,00,alt,Im Vorjahr,2025-09-01,2025-12-31,3,Gleisbau,Text"]
    _ledger(stats_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
        for _ in range(3):
            assert len(by.load_sites(2026, stats_dir=stats_dir)) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert messages[0].endswith(": 1 unlesbare Zeile(n) übersprungen.")
    assert messages[1].endswith(": 1 Zeile(n) mit Zeitraum außerhalb des Jahres 2026 nicht gezählt.")
    # Another count is another finding; a new run says everything again.
    with _ledger(stats_dir).open("a", encoding="utf-8") as handle:
        handle.write("noch,eine\n")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
        by.load_sites(2026, stats_dir=stats_dir)
        by.forget_reported()
        by.load_sites(2026, stats_dir=stats_dir)
    path = str(_ledger(stats_dir))
    assert [record.getMessage() for record in caplog.records] == [
        f"Stats-Datei {path}: 2 unlesbare Zeile(n) übersprungen.",
        f"Stats-Datei {path}: 2 unlesbare Zeile(n) übersprungen.",
        f"Stats-Datei {path}: 1 Zeile(n) mit Zeitraum außerhalb des Jahres 2026 nicht gezählt.",
    ]


def test_a_foreign_header_is_reported_once(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    stats_dir.mkdir()
    _ledger(stats_dir).write_text("timestamp,weekday,hour,provider\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        assert by.load_sites(2026, stats_dir=stats_dir) == []
        assert by.load_sites(2026, stats_dir=stats_dir) == []
    assert [record.getMessage().endswith("hat unerwarteten Header — überspringe.") for record in caplog.records] == [True]

