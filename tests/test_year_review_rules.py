"""The guard and the stored text of the yearly Baustellen review: round two of the review of 2026-11-10.

Third file next to ``tests/test_year_review_guard.py`` and ``tests/test_year_review_more.py``
(the 700-line limit of ``tests/test_file_line_limit.py``). Each block names
what ``src/markdown_stats/year_review.py`` did before:

* rule d took "Durchweg", "Vorweg" and "Gering" at the start of a sentence for street names,
* rule h took the year in "Es gab 2026 Baustellen" for a count of sites,
* rule j said "Text nicht auf Deutsch" about a German text in note style, and no test held its 15 % boundary,
* a stored text that no longer passes logged the same WARNING up to four times in one run,
* the prompt gave counts of the recorded sites without saying that the record began late in the year.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

import pytest

from src.markdown_stats import baustellen_year as by
from src.markdown_stats import year_review as yr
from src.markdown_stats.baustellen_year import BaustelleSite
from tests._year_review_support import NOW, REPO, SITES, YEAR, _answer, _check, _ensure, _site, _with
from tests.test_year_review_more import ANSWERED_AT, BASE, STATS_LOGGER, _real

COUNT = "Anzahl der Baustellen nicht aus dem Faktenblock"


@pytest.fixture(scope="module")
def answered_sites(tmp_path_factory: pytest.TempPathFactory) -> list[BaustelleSite]:
    """The 19 sites of 2026 as the real ledger held them when the first real answer was given, read from a copy."""
    with (REPO / "data" / "stats" / "baustellen_2026.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [row for row in reader if datetime.fromisoformat(row["timestamp"]) <= ANSWERED_AT]
    folder = tmp_path_factory.mktemp("stats")
    with (folder / "baustellen_2026.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    sites = by.load_sites(YEAR, stats_dir=folder)
    assert len(sites) == 19
    return sites


# ---- rule d: an ordinary word at the start of a sentence -------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Durchweg wurden Buslinien umgeleitet.",
        "Vorweg wurden Haltestellen verlegt.",
        "Gering blieben die Folgen für den Verkehr.",
        "Hinweg über das ganze Jahr wurde gearbeitet.",
        "Weg fielen zwei Haltestellen.",
    ],
)
def test_rule_d_lets_an_ordinary_word_open_a_sentence(sentence: str) -> None:
    assert _check(_with(sentence)) == []


def test_rule_d_knows_the_adverbs_and_adjectives_with_a_street_ending() -> None:
    assert {"durchweg", "vorweg", "hinweg", "weg", "rundweg", "schlechtweg", "glattweg", "kurzweg", "gering"} <= yr._ORDINARY_WORDS
    # The limit named in _street_names: before an ordinary word, a word in "-er" makes a two-word name.
    assert _check(_with("Neuer Radweg und Gehsteig wurden hergestellt.")) == ["Straßenname nicht in den Daten: Neuer Radweg"]


def test_rule_d_still_rejects_a_street_at_the_start_of_a_sentence() -> None:
    assert _check(_with("Zieglergasse und Rennweg waren betroffen.")) == ["Straßenname nicht in den Daten: Zieglergasse"]


# ---- rule h: a year is a time, not a count ---------------------------------------------------------------


def test_rule_h_takes_the_year_of_the_page_for_a_time(answered_sites: list[BaustelleSite]) -> None:
    assert _real(BASE + "Es gab 2026 Baustellen in 13 Bezirken, am häufigsten in Favoriten.", answered_sites) == []
    for sentence in ("Es gab 2026 Baustellen.", "Im Jahre 2026 Baustellen zu zählen, war die Aufgabe.",
                     "Im Jahr 2026 Baustellen in 3 Bezirken."):
        assert yr._count_reasons(sentence, SITES, YEAR) == [], sentence


def test_rule_h_takes_a_year_that_stands_in_the_data_for_a_time() -> None:
    later = [*SITES[:3], _site("Rennweg von 33A bis 37", date(2026, 9, 30), date(2027, 8, 31), "3", "Gleisbau",
                               "Die Arbeiten dauern seit 2025 an. Die Post liegt in 1230 Wien. "
                               "Zufahrt über Objekt 2100 oder Objekt 2200.")]
    for year, sentence in (("2027", "Bis 2027 Baustellen am Rennweg."), ("2025", "Seit 2025 Baustellen am Rennweg."),
                           ("2100", "Es gab 2100 Baustellen.")):
        assert yr._count_reasons(sentence, later, YEAR) == [], sentence
        assert yr._count_reasons(sentence, SITES, YEAR) == [f"{COUNT}: {year}"], sentence
    # A year that stands nowhere is a count, and so is a four-digit number in the data that is no
    # plausible year, below 2000 or above 2100 (src.utils.vienna_clock.PLAUSIBLE_YEARS); rule c lets
    # both pass, because they stand in the data, so rule h alone rejects them.
    assert yr._count_reasons("Es gab 2030 Baustellen.", later, YEAR) == [f"{COUNT}: 2030"]
    for number in ("1230", "2200"):
        assert yr._count_reasons(f"Es gab {number} Baustellen.", later, YEAR) == [f"{COUNT}: {number}"], number


def test_rule_h_reads_the_years_from_the_source_text_check_review_hands_it() -> None:
    source = "Text: Die Arbeiten dauern bis Ende 2028."
    assert yr._count_reasons("Bis 2028 Baustellen.", SITES, YEAR, source) == []
    assert yr._count_reasons("Bis 2028 Baustellen.", SITES, YEAR) == [f"{COUNT}: 2028"]


# ---- rule j: the share of German function words ----------------------------------------------------------


@pytest.mark.parametrize(("german", "words"), [(3, 20), (15, 100), (30, 200)])
def test_rule_j_accepts_exactly_15_percent_and_rejects_less(german: int, words: int) -> None:
    exact = " ".join(["und"] * german + ["Baustelle"] * (words - german))
    assert yr._language_reasons(exact) == []
    fewer = " ".join(["und"] * (german - 1) + ["Baustelle"] * (words - german + 1))
    assert yr._language_reasons(fewer) == [f"Anteil deutscher Funktionswörter unter 15 %: {german - 1} von {words} Wörtern"]
    more_words = exact + " Baustelle"
    assert yr._language_reasons(more_words) == [
        f"Anteil deutscher Funktionswörter unter 15 %: {german} von {words + 1} Wörtern"
    ]


def test_rule_j_names_the_share_and_not_a_language() -> None:
    notes = ("Im Jahr 2026 sind 19 Baustellen erfasst. Betroffene Linien: 7A, 19A, 57A, 65A, 66A, 4A, 31, 49. Häufigste "
             "Maßnahmen: Straßenbau, Rohrlegung, Gleisbau. Häufigster Bezirk: Favoriten. Folgen: verlegte Haltestellen, "
             "Umleitungen, Kurzführungen, Schienenersatzverkehr.")
    assert yr._language_reasons(notes) == ["Anteil deutscher Funktionswörter unter 15 %: 2 von 27 Wörtern"]
    assert yr._language_reasons("Baustelle") == ["Anteil deutscher Funktionswörter unter 15 %: 0 von 1 Wort"]


# ---- the stored text: one WARNING a run ------------------------------------------------------------------


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


def test_a_dropped_stored_review_is_logged_once_a_run(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _ensure(tmp_path, _answer())
    path = tmp_path / "baustellen_rueckblick_2026.json"
    stored = path.read_text(encoding="utf-8")
    yr.reset_dropped_review_log()
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        # Both passes over the year pages, the overdue check and ensure_review read the file.
        for _ in range(3):
            assert yr.load_review(YEAR, SITES[:3], stats_dir=tmp_path) is None
        assert yr.ensure_review(YEAR, SITES[:3], now=NOW, stats_dir=tmp_path, key="").text is None
    dropped = [message for message in _warnings(caplog) if "passt nicht mehr" in message]
    assert len(dropped) == 1 and dropped[0].startswith("Baustellen-Rückblick 2026: der gespeicherte Text passt nicht mehr")
    assert _warnings(caplog)[-1] == "Baustellen-Rückblick 2026: GEMINI_API_KEY ist leer, keine Anfrage."
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        # Another reason is logged once as well; a new run (or another file) logs again.
        path.write_text(stored.replace("Im Jahr 2026", "Im Jahr  2026"), encoding="utf-8")
        for _ in range(2):
            assert yr.load_review(YEAR, SITES, stats_dir=tmp_path) is None
        yr.reset_dropped_review_log()
        assert yr.load_review(YEAR, SITES, stats_dir=tmp_path) is None
        other = tmp_path / "andere"
        other.mkdir()
        (other / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        assert yr.load_review(YEAR, SITES, stats_dir=other) is None
    assert _warnings(caplog) == ["Baustellen-Rückblick 2026: der gespeicherte Text ist nicht in der Form, in der er "
                                 "gespeichert wird (Leerraum oder Steuerzeichen), und wird nicht verwendet."] * 3


# ---- the prompt: counts of the recorded sites --------------------------------------------------------------


def test_the_prompt_says_that_its_counts_are_counts_of_the_recorded_sites() -> None:
    prompt = yr.build_prompt(SITES, year=YEAR)
    rule = "- Die Zahlen im Faktenblock zählen die erfassten Baustellen; steht dort „Erfasst seit“, schreibe „erfasst“."
    assert rule in prompt.split("\n")
    assert prompt.index(rule) < prompt.index("- Erfasst seit: 24.05.2026")
