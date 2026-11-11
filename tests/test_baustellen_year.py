"""The Baustellen section of a finished year's page (``src/markdown_stats/baustellen_year.py``).

Owner's decision of 2026-11-10 (Michael), point 4: the year's Baustellen
stand on the year page. Count and list come from the ledger
``baustellen_<Jahr>.csv`` and are present with or without the review in
words. These tests pin how the ledger is read (one row per guid, one site
per title, damaged lines skipped) and what the section says. What the
review of 2026-11-10 changed is pinned in ``test_baustellen_year_more.py``.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.markdown_stats import baustellen_year as by
from src.markdown_stats.baustellen_year import BaustelleSite
from src.utils import stats
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import BAUSTELLEN_HEADER

YEAR = 2026


def _row(
    guid: str,
    title: str,
    *,
    seen: str = "2026-06-01T08:00:00+02:00",
    starts: str = "2026-06-01",
    ends: str = "2026-06-30",
    district: str = "3",
    measure: str = "Gleisbau",
    description: str = "Die Haltestelle wird verlegt.",
) -> list[str]:
    return [seen, "Mo", "08", guid, title, starts, ends, district, measure, description]


def _csv_line(cells: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(cells)
    return buffer.getvalue()


def _write(folder: Path, rows: list[list[str]], *, lines: list[str] | None = None) -> Path:
    """A ledger of *rows* under *folder*; *lines* are appended as written."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"baustellen_{YEAR}.csv"
    content = [_csv_line(list(BAUSTELLEN_HEADER)), *(_csv_line(row) for row in rows), *(lines or [])]
    path.write_text("\n".join(content) + "\n", encoding="utf-8")
    return path


def _site(
    title: str,
    *,
    starts: date | None = date(2026, 6, 1),
    ends: date | None = date(2026, 6, 30),
    district: str = "3",
    measure: str = "Gleisbau",
    seen: datetime = datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA_TZ),
) -> BaustelleSite:
    return BaustelleSite(title, starts, ends, district, measure, ("Text.",), seen)


def test_the_header_has_the_ten_columns_of_the_ledger() -> None:
    assert BAUSTELLEN_HEADER == (
        "timestamp", "weekday", "hour", "guid", "title", "starts_at", "ends_at", "district", "measure", "description",
    )


# ---- reading the ledger -----------------------------------------------------


def test_a_year_without_a_ledger_has_no_sites(tmp_path: Path) -> None:
    assert by.load_sites(YEAR, stats_dir=tmp_path) == []
    (tmp_path / f"baustellen_{YEAR}.csv").write_text("", encoding="utf-8")
    assert by.load_sites(YEAR, stats_dir=tmp_path) == []


def test_one_row_becomes_one_site_with_its_fields(tmp_path: Path) -> None:
    _write(tmp_path, [_row("g1", "Burggasse 67", district="7", measure="Straßenbau", starts="2026-07-28", ends="2026-10-30")])
    (site,) = by.load_sites(YEAR, stats_dir=tmp_path)
    assert site == BaustelleSite(
        title="Burggasse 67",
        starts=date(2026, 7, 28),
        ends=date(2026, 10, 30),
        district="7",
        measure="Straßenbau",
        descriptions=("Die Haltestelle wird verlegt.",),
        first_seen=datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA_TZ),
    )


def test_the_default_folder_is_looked_up_at_call_time(tmp_path: Path) -> None:
    """The tests' fixture redirects ``DEFAULT_STATS_DIR``; a reader that bound it at import would miss it."""
    _write(stats.DEFAULT_STATS_DIR, [_row("g1", "Burggasse 67")])
    assert stats.DEFAULT_STATS_DIR == tmp_path / "stats"
    assert [site.title for site in by.load_sites(YEAR)] == ["Burggasse 67"]


def test_a_guid_counts_once_with_its_earliest_sighting(tmp_path: Path) -> None:
    first = _row("g1", "Burggasse 67", seen="2026-06-01T08:00:00+02:00")
    again = _row("g1", "Burggasse 67", seen="2026-06-03T08:00:00+02:00", description="Später geschrieben.")
    earlier = _row("g1", "Burggasse 67", seen="2026-05-30T23:10:00+02:00", description="Zuerst gesehen.")
    # A union merge keeps both sides: the same line twice, and the guid again with other times.
    _write(tmp_path, [first, first, again, earlier])
    (site,) = by.load_sites(YEAR, stats_dir=tmp_path)
    assert site.first_seen == datetime(2026, 5, 30, 23, 10, tzinfo=VIENNA_TZ)
    assert site.descriptions == ("Zuerst gesehen.",)


def test_rows_of_one_title_are_one_site(tmp_path: Path) -> None:
    """The city's guid is a hash of title, start and end: a prolonged site gets a new guid."""
    rows = [
        _row("g1", "Rennweg von 33A bis 37", seen="2026-03-01T08:00:00+01:00", starts="2026-03-02", ends="2026-04-30",
             district="3", measure="Gleisbau", description="Erster Text."),
        _row("g2", "Rennweg  von 33A bis 37", seen="2026-04-20T08:00:00+02:00", starts="2026-03-02", ends="2026-06-15",
             district="3", measure="Gleisbau", description="Zweiter Text."),
        _row("g3", "RENNWEG von 33A bis 37", seen="2026-06-10T08:00:00+02:00", starts="2026-02-20", ends="2026-05-31",
             district="", measure="Straßenbau", description="Dritter Text."),
        _row("g4", "Rennweg von 33A bis 37", seen="2026-05-01T08:00:00+02:00", starts="", ends="",
             district="3", measure="Gleisbau", description="Zweiter Text."),
        _row("g5", "Rennweg von 33A bis 37", seen="2026-02-15T08:00:00+01:00", starts="2026-03-02", ends="2026-04-30",
             district="3", measure="Gleisbau", description="Nullter Text."),
    ]
    _write(tmp_path, rows)
    (site,) = by.load_sites(YEAR, stats_dir=tmp_path)
    # Title and measure of the newest row; its empty district falls back to the newest row that names one.
    assert site.title == "RENNWEG von 33A bis 37"
    assert (site.measure, site.district) == ("Straßenbau", "3")
    # The period of the newest row that names one (g3): the city moved the end of g2 earlier.
    assert (site.starts, site.ends) == (date(2026, 2, 20), date(2026, 5, 31))
    # Distinct texts, newest first, three at most.
    assert site.descriptions == ("Dritter Text.", "Zweiter Text.", "Erster Text.")
    assert site.first_seen == datetime(2026, 2, 15, 8, 0, tzinfo=VIENNA_TZ)


def test_a_cut_mark_at_the_end_of_the_title_does_not_split_a_site(tmp_path: Path) -> None:
    rows = [
        _row("g1", "Hauptstraße bis Schlachthausgas", seen="2026-05-24T08:00:00+02:00"),
        _row("g2", "Hauptstraße bis Schlachthausgas…", seen="2026-10-07T08:00:00+02:00", ends="2026-11-30"),
        _row("g3", "Hauptstraße bis Schlachthausgasse", seen="2026-10-08T08:00:00+02:00"),
    ]
    _write(tmp_path, rows)
    whole, cut = by.load_sites(YEAR, stats_dir=tmp_path)
    assert (cut.title, cut.ends) == ("Hauptstraße bis Schlachthausgas…", date(2026, 11, 30))
    assert whole.title == "Hauptstraße bis Schlachthausgasse"


def test_sites_are_ordered_by_start_then_title_and_those_without_start_come_last(tmp_path: Path) -> None:
    rows = [
        _row("g1", "Zieglergasse", starts="2026-03-01"),
        _row("g2", "Offene Gasse", starts="", ends=""),
        _row("g3", "Alser Straße", starts="2026-03-01"),
        _row("g4", "Burggasse", starts="2025-11-01"),
        _row("g5", "alser Bach", starts="2026-03-01"),
    ]
    _write(tmp_path, rows)
    assert [site.title for site in by.load_sites(YEAR, stats_dir=tmp_path)] == [
        "Burggasse", "alser Bach", "Alser Straße", "Zieglergasse", "Offene Gasse",
    ]


def test_damaged_lines_cost_only_themselves(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    good = _row("g1", "Burggasse 67")
    last = _row("g9", "Rennweg 33A", starts="2026-09-30")
    damaged = [
        "nur,drei,Zellen",                                             # too few cells
        _csv_line(_row("g2", "Ohne Zeit", seen="gestern")),            # unreadable time stamp
        _csv_line(_row("", "Ohne Guid")),                              # no guid
        _csv_line(_row("g3", "   ")),                                  # no title
        '2026-06-02T08:00:00+02:00,Di,08,g4,"Offenes Anführungszeichen,2026-06-01,2026-06-30,3,Gleisbau,Text',
        _csv_line(_row("g5", "Jahr 9999", seen="9999-12-31T23:59:59+00:00")),
        _csv_line(_row("g6", "Jahr 1970", seen="1970-01-01T00:00:00+00:00")),  # a year no ledger row can have
        _csv_line([*_row("g7", "Elf Zellen"), "eine Zelle zu viel"]),
        _csv_line(list(BAUSTELLEN_HEADER)),                            # the header again (two branches created the file)
        "",
    ]
    _write(tmp_path, [good], lines=[*damaged, _csv_line(last)])
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        sites = by.load_sites(YEAR, stats_dir=tmp_path)
    assert [site.title for site in sites] == ["Burggasse 67", "Rennweg 33A"]
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1 and "8 unlesbare Zeile(n)" in warnings[0]


def test_a_file_with_another_header_is_not_read(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / f"baustellen_{YEAR}.csv"
    path.write_text("timestamp,weekday,hour,provider,location_name\n" + _csv_line(_row("g1", "Burggasse")) + "\n",
                    encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        assert by.load_sites(YEAR, stats_dir=tmp_path) == []
    assert any("unerwarteten Header" in record.getMessage() for record in caplog.records)


def test_an_oversized_ledger_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, [_row("g1", "Burggasse 67")])
    assert len(by.load_sites(YEAR, stats_dir=tmp_path)) == 1
    monkeypatch.setattr(by, "MAX_CSV_BYTES", path.stat().st_size - 1)
    assert by.load_sites(YEAR, stats_dir=tmp_path) == []


def test_dates_and_times_are_read_tolerantly(tmp_path: Path) -> None:
    rows = [
        # A naive time stamp is Vienna time; "no end" as the year 9999 is no end.
        _row("g1", "Naive Zeit", seen="2026-06-01T08:00:00", starts="2026-06-01", ends="9999-12-31"),
        # A time stamp in UTC is shown in Vienna time; a date with a time keeps its day.
        _row("g2", "UTC", seen="2026-06-30T22:30:00+00:00", starts="2026-07-01T00:00:00+02:00", ends="kein Datum"),
    ]
    _write(tmp_path, rows)
    naive, utc = by.load_sites(YEAR, stats_dir=tmp_path)
    assert naive.first_seen == datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA_TZ) and naive.ends is None
    assert utc.first_seen.isoformat() == "2026-07-01T00:30:00+02:00"
    assert (utc.starts, utc.ends) == (date(2026, 7, 1), None)


def test_whitespace_in_cells_is_collapsed(tmp_path: Path) -> None:
    _write(tmp_path, [_row("g1", "  Burggasse   67 ", description="Satz eins. \t  Satz zwei.", measure=" Straßenbau ")])
    (site,) = by.load_sites(YEAR, stats_dir=tmp_path)
    assert (site.title, site.measure, site.descriptions) == ("Burggasse 67", "Straßenbau", ("Satz eins. Satz zwei.",))


def test_a_record_broken_over_two_lines_is_skipped_not_read_half(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A ledger row is one physical line; the first half of a broken one is not taken for the whole."""
    _write(tmp_path, [_row("g1", "Burggasse 67", description="Zeile eins \n Zeile zwei"), _row("g2", "Rennweg 33A")])
    with caplog.at_level(logging.WARNING, logger="generate_markdown_stats"):
        sites = by.load_sites(YEAR, stats_dir=tmp_path)
    assert [site.title for site in sites] == ["Rennweg 33A"]
    assert any("2 unlesbare Zeile(n)" in record.getMessage() for record in caplog.records)


# ---- district and period ------------------------------------------------------


def test_the_23_districts_are_named_in_order() -> None:
    assert len(by.DISTRICT_NAMES) == 23 and len(set(by.DISTRICT_NAMES)) == 23
    assert (by.DISTRICT_NAMES[0], by.DISTRICT_NAMES[2], by.DISTRICT_NAMES[14], by.DISTRICT_NAMES[22]) == (
        "Innere Stadt", "Landstraße", "Rudolfsheim-Fünfhaus", "Liesing",
    )


@pytest.mark.parametrize(
    ("cell", "numbers", "label"),
    [
        ("3", (3,), "3., Landstraße"),
        ("03", (3,), "3., Landstraße"),
        ("1030", (3,), "3., Landstraße"),
        ("2, 3 und 2", (2, 3), "2., Leopoldstadt; 3., Landstraße"),
        ("23", (23,), "23., Liesing"),
        ("24", (), "24"),
        ("0", (), "0"),
        ("1035", (), "1035"),
        ("12345", (), "12345"),
        ("Niederösterreich", (), "Niederösterreich"),
        ("", (), ""),
    ],
)
def test_a_district_cell_names_its_districts(cell: str, numbers: tuple[int, ...], label: str) -> None:
    assert by.district_numbers(cell) == numbers
    assert by.district_label(cell) == label


@pytest.mark.parametrize(
    ("starts", "ends", "text"),
    [
        (date(2026, 5, 25), date(2026, 9, 6), "25.05.2026 – 06.09.2026"),
        (date(2026, 5, 25), None, "25.05.2026 – offen"),
        (None, date(2026, 9, 6), "bis 06.09.2026"),
        (None, None, "nicht angegeben"),
    ],
)
def test_the_period_as_the_page_writes_it(starts: date | None, ends: date | None, text: str) -> None:
    assert by.period_text(_site("x", starts=starts, ends=ends)) == text


# ---- what the script counts ---------------------------------------------------


def test_the_twelve_months_are_named_as_vienna_writes_them() -> None:
    assert len(by.MONTH_NAMES) == 12 and len(set(by.MONTH_NAMES)) == 12
    assert (by.MONTH_NAMES[0], by.MONTH_NAMES[1], by.MONTH_NAMES[11]) == ("Jänner", "Februar", "Dezember")


def test_the_facts_count_starts_per_month_and_sites_per_district() -> None:
    sites = [
        _site("A", starts=date(2026, 2, 1), ends=None),
        _site("B", starts=date(2026, 7, 28), district="7"),
        _site("C", starts=date(2026, 7, 30), district="1030 Wien"),
        _site("D", starts=date(2026, 9, 30), district="22"),
    ]
    assert by.facts_block(sites, year=YEAR).split("\n") == [
        "- Jahr: 2026",
        "- Baustellen mit Folgen für den öffentlichen Verkehr: 4",
        "- Erfasst seit: 01.06.2026 (Baustellen, die davor endeten, fehlen in den Zahlen)",
        "- Beginn im Jahr 2026, je Monat: Februar: 1, Juli: 2, September: 1",
        "- Beginn vor 2026: 0",
        "- Baustellen je Bezirk: 3. Bezirk (Landstraße): 2, 7. Bezirk (Neubau): 1, 22. Bezirk (Donaustadt): 1",
        "- Baustellen je Maßnahme: Gleisbau: 4",
    ]


def test_the_facts_count_starts_outside_the_year_and_sites_without_district() -> None:
    sites = [
        _site("A", starts=date(2021, 2, 28), ends=date(2028, 12, 31), district="5"),
        _site("B", starts=date(2027, 1, 4), ends=None, district=""),
        _site("C", starts=None, ends=None, district="Niederösterreich"),
    ]
    assert by.facts_block(sites, year=YEAR).split("\n") == [
        "- Jahr: 2026",
        "- Baustellen mit Folgen für den öffentlichen Verkehr: 3",
        "- Erfasst seit: 01.06.2026 (Baustellen, die davor endeten, fehlen in den Zahlen)",
        "- Beginn im Jahr 2026, je Monat: keiner",
        "- Beginn vor 2026: 1",
        "- Beginn nach 2026: 1",
        "- Beginn nicht angegeben: 1",
        "- Baustellen je Bezirk: 5. Bezirk (Margareten): 1",
        "- Baustellen je Maßnahme: Gleisbau: 3",
    ]
    assert "- Baustellen je Bezirk: keine Angabe" in by.facts_block([sites[2]], year=YEAR).split("\n")


def test_months_touched_by_the_periods() -> None:
    one = [_site("x", starts=date(2026, 5, 25), ends=date(2026, 9, 6))]
    assert by.months_touched(one, YEAR) == {5, 6, 7, 8, 9}
    # Several sites: every month one of them touches; an open end runs to the end of the year.
    assert by.months_touched([*one, _site("y", starts=date(2026, 2, 1), ends=None)], YEAR) == set(range(2, 13))
    # A period that began before the year or ends after it counts with its months of the year.
    assert by.months_touched([_site("x", starts=date(2021, 2, 28), ends=date(2026, 3, 1))], YEAR) == {1, 2, 3}
    assert by.months_touched([_site("x", starts=date(2026, 11, 2), ends=date(2028, 12, 31))], YEAR) == {11, 12}
    # An end in the first days of the next year is no end in January of this one.
    assert by.months_touched([_site("x", starts=date(2026, 11, 2), ends=date(2027, 1, 8))], YEAR) == {11, 12}
    # Without a start the site counts from the day it was first recorded, read as Vienna time.
    no_start = [_site("x", starts=None, ends=date(2026, 8, 3), seen=datetime(2026, 6, 30, 23, 0, tzinfo=UTC))]
    assert by.months_touched(no_start, YEAR) == {7, 8}
    # A period wholly outside the year touches none of its months.
    assert by.months_touched([_site("x", starts=date(2025, 1, 1), ends=date(2025, 12, 31))], YEAR) == set()
    assert by.months_touched([_site("x", starts=date(2027, 1, 1), ends=None)], YEAR) == set()


# ---- the section ----------------------------------------------------------


def test_no_sites_no_section() -> None:
    assert by.render_baustellen_section([], year=YEAR) == []
    assert by.render_baustellen_section([], year=YEAR, review="Text", pending=True) == []


def test_the_section_without_a_review() -> None:
    sites = [
        _site("Burggasse 67", starts=date(2026, 7, 28), ends=date(2026, 10, 30), district="7", measure="Straßenbau",
              seen=datetime(2026, 10, 8, 14, 30, tzinfo=VIENNA_TZ)),
        _site("Rennweg von 33A bis 37", starts=date(2026, 9, 30), ends=None, district="3",
              seen=datetime(2026, 5, 24, 0, 52, tzinfo=VIENNA_TZ)),
    ]
    lines = by.render_baustellen_section(sites, year=YEAR)
    assert lines == [
        "## Baustellen mit Folgen für den öffentlichen Verkehr",
        "",
        (
            "Hier stehen die Baustellen aus den Daten der Stadt Wien, deren Text eine Folge für den öffentlichen "
            "Verkehr nennt, etwa eine verlegte oder aufgelassene Haltestelle, eine umgeleitete oder kurzgeführte "
            "Linie, einen eingestellten Straßenbahnbetrieb oder Ersatzbusse. Gezählt wird nach dem Namen, unter dem "
            "die Stadt Wien eine Baustelle führt; trägt sie eine Baustelle unter einem anderen Namen neu ein, steht "
            "sie hier zweimal. Die Aufzeichnung für 2026 beginnt am 24.05.2026."
        ),
        "",
        "Im Jahr 2026 sind **2** solche Baustellen erfasst.",
        "",
        "### Baustellen im Jahr",
        "",
        "| Ort | Zeitraum | Bezirk | Maßnahme |",
        "| --- | --- | --- | --- |",
        "| Burggasse 67 | 28.07.2026 – 30.10.2026 | 7., Neubau | Straßenbau |",
        "| Rennweg von 33A bis 37 | 30.09.2026 – offen | 3., Landstraße | Gleisbau |",
        "",
        (
            "> Datenquelle: Stadt Wien – data.wien.gv.at. Die Texte der Stadt Wien sind hier bearbeitet "
            "(gekürzt und vereinheitlicht). Lizenz: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)."
        ),
    ]


@pytest.mark.parametrize(
    ("seen", "day"),
    [
        # Midnight in Vienna is 22:00 or 23:00 UTC: the day of the page is the Vienna day.
        (datetime(2026, 5, 23, 22, 52, tzinfo=UTC), "24.05.2026"),
        (datetime(2025, 12, 31, 23, 30, tzinfo=UTC), "01.01.2026"),
        (datetime(2026, 12, 31, 22, 59, tzinfo=UTC), "31.12.2026"),
        # A naive time stamp is Vienna time (as in months_touched), whatever the machine's zone is.
        (datetime(2026, 5, 23, 23, 30), "23.05.2026"),
        (datetime(2026, 1, 1, 0, 30), "01.01.2026"),
    ],
)
def test_the_first_day_of_the_recording_is_a_vienna_day(seen: datetime, day: str) -> None:
    lines = by.render_baustellen_section([_site("Burggasse 67", seen=seen)], year=YEAR)
    assert lines[2].endswith(f" Die Aufzeichnung für 2026 beginnt am {day}.")


def test_one_site_is_counted_in_the_singular() -> None:
    lines = by.render_baustellen_section([_site("Burggasse 67")], year=YEAR)
    assert "Im Jahr 2026 ist **eine** solche Baustelle erfasst." in lines


def test_a_pending_review_is_announced_in_one_line() -> None:
    lines = by.render_baustellen_section([_site("Burggasse 67")], year=YEAR, pending=True)
    assert "_Ein kurzer Rückblick in Worten wird hier ergänzt._" in lines
    assert "### Rückblick" not in lines
    # Not announced unless asked for.
    assert not any("wird hier ergänzt" in line for line in by.render_baustellen_section([_site("Burggasse 67")], year=YEAR))


def test_a_review_stands_with_its_label_before_the_list() -> None:
    review = "Im Jahr 2026 ist eine Baustelle erfasst. Sie lag in der Burggasse."
    lines = by.render_baustellen_section([_site("Burggasse 67")], year=YEAR, review=review, pending=True)
    at = lines.index("### Rückblick")
    assert lines[at:at + 6] == [
        "### Rückblick",
        "",
        review,
        "",
        (
            "_Dieser Rückblick wurde automatisch mit Gemini (Google) aus den Texten der Stadt Wien zusammengefasst "
            "und von einem festen Skript gegen diese Texte geprüft. Anzahl und Liste stammen direkt aus den Daten._"
        ),
        "",
    ]
    assert at < lines.index("### Baustellen im Jahr")
    # With a review nothing is announced, and the attribution names the summary.
    assert not any("wird hier ergänzt" in line for line in lines)
    assert lines[-1] == (
        "> Datenquelle: Stadt Wien – data.wien.gv.at. Die Texte der Stadt Wien sind hier bearbeitet "
        "(gekürzt, vereinheitlicht und im Rückblick maschinell zusammengefasst). "
        "Lizenz: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)."
    )


def test_a_blank_review_counts_as_none() -> None:
    lines = by.render_baustellen_section([_site("Burggasse 67")], year=YEAR, review="  \n ", pending=True)
    assert "### Rückblick" not in lines and any("wird hier ergänzt" in line for line in lines)


def test_a_review_cannot_bring_markup_onto_the_page() -> None:
    """The guard rejects markup before a text is stored; the renderer escapes all the same."""
    review = "Zeile eins\n\n## Überschrift <script>alert(1)</script> [Link](https://example.org) {{ site.x }} | Zelle"
    lines = by.render_baustellen_section([_site("Burggasse 67")], year=YEAR, review=review)
    paragraph = lines[lines.index("### Rückblick") + 2]
    assert "\n" not in paragraph and "<script>" not in paragraph and "](" not in paragraph
    assert paragraph.startswith("Zeile eins \\#\\# Überschrift &lt;script&gt;")
    # A bare pipe would make the paragraph a table on the website (kramdown).
    assert paragraph.endswith(" \\| Zelle") and "|" not in paragraph.replace("\\|", "")
    assert sum(1 for line in lines if line.startswith("## ")) == 1


def test_the_list_shows_120_sites_and_counts_the_rest() -> None:
    sites = [_site(f"Gasse {number:03d}", starts=date(2026, 1, 1)) for number in range(1, 124)]
    lines = by.render_baustellen_section(sites, year=YEAR)
    rows = [line for line in lines if line.startswith("| Gasse ")]
    assert by.MAX_LISTED == 120 and len(rows) == 120
    assert rows[0].startswith("| Gasse 001 ") and rows[-1].startswith("| Gasse 120 ")
    assert "Im Jahr 2026 sind **123** solche Baustellen erfasst." in lines
    assert "_Die Liste zeigt die ersten 120 nach Beginn; 3 weitere Baustellen sind hier nicht aufgeführt._" in lines
    one_more = by.render_baustellen_section(sites[:121], year=YEAR)
    assert "_Die Liste zeigt die ersten 120 nach Beginn; 1 weitere Baustelle ist hier nicht aufgeführt._" in one_more
    # Exactly 120: the whole list, no such line.
    assert not any("nicht aufgeführt" in line for line in by.render_baustellen_section(sites[:120], year=YEAR))
    assert len(lines) < 140


def test_ledger_text_cannot_break_the_table() -> None:
    site = BaustelleSite(
        title="Gasse | mit <b>Pipe</b> und [Link](https://example.org) *fett*",
        starts=date(2026, 6, 1),
        ends=None,
        district="Bezirk | 99",
        measure="",
        descriptions=(),
        first_seen=datetime(2026, 6, 1, 8, 0, tzinfo=VIENNA_TZ),
    )
    lines = by.render_baustellen_section([site], year=YEAR)
    (row,) = [line for line in lines if line.startswith("| Gasse")]
    # Four cells: every pipe of the ledger text is escaped.
    assert len([part for part in row.replace("\\|", "").split("|") if part.strip()]) == 4
    assert "<b>" not in row and "](" not in row and "\\*fett\\*" in row
    # An empty cell is a dash, a district without a number stays as written.
    assert row.endswith("| Bezirk \\| 99 | – |")
