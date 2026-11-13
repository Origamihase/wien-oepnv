"""The guard of the yearly Baustellen review: the cases of the review of 2026-11-10.

Second file next to ``tests/test_year_review_guard.py`` (the 700-line limit of
``tests/test_file_line_limit.py``), with its fixtures. Each block names what
``src/markdown_stats/year_review.py`` or ``review_client.py`` did before:

* rule d took "hinweg", "gering" and "Radweg" for street names,
* rule c rejected "bis 2027" for sites that end in 2027,
* a wrong count of sites passed when the number stood anywhere in the data,
* tone was four markers: "unser", "du", "leider" and an English text passed,
* a sentence that ends in a short number was not counted,
* a list marker at the start and "$$" reached the page,
* every year without a review had a full deadline of its own,
* a stored text that stopped passing was dropped with a count or no line,
* a key that Google rejects with HTTP 400 was sent to all four models,

and the boundaries no test caught (finding 69 of that review).
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.markdown_stats import baustellen_year as by
from src.markdown_stats import review_client as rc
from src.markdown_stats import year_review as yr
from src.markdown_stats import year_review_prompt as yr_prompt
from src.markdown_stats.baustellen_year import BaustelleSite
from src.markdown_stats.review_client import ReviewAnswer
from tests.test_review_client import KEY, _Clock, _fake, _ok, _Response
from tests._year_review_support import GOOD, NOW, REPO, SITES, YEAR, _answer, _check, _ensure, _with

STATS_LOGGER = "generate_markdown_stats"
# The first real answer (gemini-3.5-flash-lite, 2026-11-10 23:19 Europe/Vienna), which passed the guard.
REAL_ANSWER = (
    "Im Jahr 2026 gab es insgesamt 19 Baustellen mit Folgen für den öffentlichen Verkehr in Wien. Häufig vorkommende "
    "Maßnahmen waren Straßenbau, Rohrlegung und Gleisbau, während der 10. Bezirk (Favoriten) besonders oft betroffen "
    "war. Die Texte nennen als Folgen für Haltestellen unter anderem provisorische Verlegungen, Zusammenlegungen oder "
    "Auflassungen. Für die betroffenen Linien führten die Baustellen zu Umleitungen, Kurzführungen, Einstellungen oder "
    "zur Einrichtung von Schienenersatzverkehr. Dabei waren beispielsweise die Straßenbahnlinie 49, die Linie 31 sowie "
    "diverse Buslinien wie die Buslinie 7A von diesen Maßnahmen betroffen."
)
# The rows of data/stats/baustellen_2026.csv that existed when that answer was given: the ledger only grows.
ANSWERED_AT = datetime.fromisoformat("2026-11-10T23:19:00+01:00")
BASE = ("Im Jahr 2026 sind in Wien 19 Baustellen mit Folgen für den öffentlichen Verkehr erfasst. Am häufigsten "
        "kamen Straßenbau, Rohrlegung und Gleisbau vor. Die Texte nennen verlegte Haltestellen und umgeleitete Buslinien. ")


@pytest.fixture(autouse=True)
def _usual_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client tests ask the default chain, whatever ``JAHRESRUECKBLICK_MODEL`` the caller's shell holds."""
    monkeypatch.delenv(rc.MODEL_ENV, raising=False)


@pytest.fixture(scope="module")
def real_sites(tmp_path_factory: pytest.TempPathFactory) -> list[BaustelleSite]:
    """The 19 sites of 2026 as the real ledger held them on 2026-11-10, read from a copy."""
    folder = tmp_path_factory.mktemp("stats")
    with (REPO / "data" / "stats" / "baustellen_2026.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [row for row in reader if datetime.fromisoformat(row["timestamp"]) <= ANSWERED_AT]
    with (folder / "baustellen_2026.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    sites = by.load_sites(YEAR, stats_dir=folder)
    assert len(sites) == 19
    return sites


def _real(text: str, sites: list[BaustelleSite]) -> list[str]:
    return yr.check_review(rc.clean_review(text), sites, year=YEAR)


def test_the_real_answer_of_2026_11_10_passes_the_guard(real_sites: list[BaustelleSite]) -> None:
    assert len(REAL_ANSWER) == 634 and _real(REAL_ANSWER, real_sites) == []


# ---- rule d: only capitalised words, ordinary words exempt (decision of the review of 2026-11-10) ----------


@pytest.mark.parametrize(
    "sentence",
    [
        "Die Folgen für den öffentlichen Verkehr blieben gering.",
        "Über das Jahr hinweg wurde gearbeitet.",
        "An einer Baustelle fiel eine Haltestelle weg.",
        "Bei einer Baustelle wurde ein Radweg hergestellt.",
        "Eine Straße wurde während der Arbeiten zur Einbahnstraße.",
        "Mehrere Haltestellen fanden an anderer Stelle Platz.",
        "Betroffen war jeweils eine Straße oder eine Gasse.",
        "Das Gelände neben dem Parkplatz blieb frei.",
        "Der Verkehr wich auf den zieglerweg aus.",       # lower case: never a street name
    ],
)
def test_rule_d_lets_ordinary_words_pass(sentence: str) -> None:
    assert _check(_with(sentence)) == []


@pytest.mark.parametrize(
    ("sentence", "name"),
    [
        ("In der Mariahilfer Straße wurde gearbeitet.", "Mariahilfer Straße"),
        ("Am Ring wurde gearbeitet.", "Ring"),         # alone a street of Vienna: needs the data
        ("Am Gürtel wurde gearbeitet.", "Gürtel"),
        ("Am Kai wurde gearbeitet.", "Kai"),
        ("Die Auer Gasse war gesperrt.", "Auer Gasse"),  # a name part of four letters (boundary of _is_name_part)
    ],
)
def test_rule_d_still_checks_names_and_the_streets_of_vienna(sentence: str, name: str) -> None:
    assert _check(_with(sentence)) == [f"Straßenname nicht in den Daten: {name}"]
    assert not {"ring", "gürtel", "kai"} & yr._ORDINARY_WORDS


# ---- rule c: the year of a full date, the decimal comma ------------------------------------------------


def test_rule_c_derives_the_year_of_a_full_date_and_nothing_less() -> None:
    assert yr._digit_tokens("bis 31.08.2027") == {"31.08.2027", "31.08", "2027"}
    assert yr._digit_tokens("am 31.08. im Jahr") == {"31.08"}


def test_rule_c_reads_a_decimal_comma_as_one_number() -> None:
    assert yr._number_reasons("Die Fahrbahn ist 3,5 Meter breit.", "Baustelle 3, Bezirk 5") == [
        "Zahl oder Kennung nicht in den Daten: 3,5"
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "Mehrere Baustellen reichen bis ins Jahr 2027, wie die Zeiträume zeigen.",
        "Davon entfielen 7 auf Straßenbau, 6 auf Rohrlegung, 5 auf Gleisbau und 1 auf Brückenbau.",
        "Bei 5 Baustellen mit Gleisbau wurde der Straßenbahnbetrieb teilweise eingestellt.",
    ],
)
def test_true_years_and_measure_counts_of_2026_pass(real_sites: list[BaustelleSite], sentence: str) -> None:
    assert _real(BASE + sentence, real_sites) == []


def test_a_year_no_site_touches_is_still_rejected(real_sites: list[BaustelleSite]) -> None:
    text = BASE + "Mehrere Baustellen begannen schon im Jahr 2025, wie die Zeiträume zeigen."
    assert _real(text, real_sites) == ["Zahl oder Kennung nicht in den Daten: 2025"]


# ---- rule h: a count of sites is one the script counted ------------------------------------------------


def test_rule_h_rejects_a_count_that_merely_stands_in_the_data(real_sites: list[BaustelleSite]) -> None:
    # 21 stands in "21., Floridsdorf", 13 in "13., Hietzing": rule c lets both through.
    assert _real(BASE.replace("19 Baustellen", "21 Baustellen"), real_sites) == [
        "Anzahl der Baustellen nicht aus dem Faktenblock: 21"
    ]
    text = BASE + "In Favoriten lagen 13 Baustellen, im Juli begannen 9 Baustellen."
    assert _real(text, real_sites) == ["Anzahl der Baustellen nicht aus dem Faktenblock: 13"]
    assert _real(BASE + "Mit 4 Baustellen lag Favoriten vorn, im Juli begannen 9.", real_sites) == []


def test_rule_h_reads_digits_directly_before_baustelle_only() -> None:
    assert yr._count_reasons("Insgesamt waren es 77 Baustellen und 1 Baustelle.", SITES, YEAR) == [
        "Anzahl der Baustellen nicht aus dem Faktenblock: 77"
    ]
    assert _check(_with("Insgesamt waren es 77 Baustellen.")) == [
        "Zahl oder Kennung nicht in den Daten: 77", "Anzahl der Baustellen nicht aus dem Faktenblock: 77",
    ]
    # The year of the page, the tail of a date and a number word are no count (round two of the review of
    # 2026-11-10 changed "Es gab 2026 Baustellen.", rejected before, to accepted: the year is a time there).
    for sentence in ("Im Jahr 2026 Baustellen in 3 Bezirken.", "Ab 30.09.2026 Baustellen am Rennweg.",
                     "Es waren siebenundsiebzig Baustellen.", "Es gab 2026 Baustellen."):
        assert yr._count_reasons(sentence, SITES, YEAR) == [], sentence


def test_rule_h_takes_its_counts_from_the_facts_block() -> None:
    assert by.fact_counts(SITES, year=YEAR) == frozenset({0, 1, 2, 4})
    assert yr._count_reasons("Es waren 4 Baustellen, 2 davon im 3. Bezirk, 0 Baustellen davor.", SITES, YEAR) == []
    assert yr._count_reasons("Es waren 3 Baustellen.", SITES, YEAR) == ["Anzahl der Baustellen nicht aus dem Faktenblock: 3"]


# ---- rules g, i, j: tone and language -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "marker"),
    [("mich", "ich"), ("mir", "ich"), ("meine", "ich"), ("meiner", "ich"), ("uns", "wir"), ("unser", "wir"),
     ("unserer", "wir"), ("unsrem", "wir"), ("du", "du"), ("dich", "du"), ("dir", "du"), ("deine", "du"),
     ("euch", "du"), ("euer", "du"), ("eure", "du")],
)
def test_rule_g_knows_the_forms_of_its_markers(word: str, marker: str) -> None:
    assert _check(_with(f"Das betraf {word} kaum.")) == [f"Ich-Form, Anrede oder Rat im Text: {marker}"]


@pytest.mark.parametrize(
    "word",
    ["leider", "bedauerlicherweise", "erfreulicherweise", "glücklicherweise", "ärgerlich", "vorbildlich",
     "katastrophal", "Verkehrschaos", "Zumutung", "Skandal", "versagt"],
)
def test_rule_i_rejects_a_word_that_judges(word: str) -> None:
    assert _check(_with(f"Das war {word} für die Linie 71.")) == [f"Wertung im Text: {word.lower()}"]


@pytest.mark.parametrize(
    "sentence",
    [
        ("Die Arbeiten sollten laut den Texten bis November dauern, gut die Hälfte begann im Juli, und die "
        "Wiener Linien verlegten ihre Haltestellen."),
        "Die Stadt Wien nennt in den Texten eine empfohlene Umleitung für den Verkehr.",
        "Die Arbeiten wirkten sich auf den Busverkehr aus, und die Meinungen der Texte unterscheiden sich nicht.",
    ],
)
def test_neutral_words_next_to_the_lists_pass(sentence: str) -> None:
    assert _check(_with(sentence)) == []


def test_rule_j_rejects_a_text_in_another_language(real_sites: list[BaustelleSite]) -> None:
    english = ("In 2026, Vienna recorded 19 construction sites with consequences for public transport. Road works, "
               "pipe laying and track works were the most frequent measures. Most sites were located in Favoriten, "
               "and 9 of them started in July. The texts mention relocated stops and rail replacement services.")
    assert _real(english, real_sites) == ["Anteil deutscher Funktionswörter unter 15 %: 3 von 43 Wörtern"]
    assert yr.MIN_GERMAN_SHARE == 0.15


def test_the_known_limits_of_tone_and_language_pass(real_sites: list[BaustelleSite]) -> None:
    """Named in check_review's docstring: closed lists catch no insult, instruction or half-English text."""
    for sentence in ("Die Stadtregierung besteht aus Idioten. Der Bürgermeister ist korrupt und gehört abgewählt.",
                     "Most sites were located in Favoriten. Overall it was a busy year for public transport.",
                     "Fahrgäste sollten mehr Zeit einplanen und auf andere Linien ausweichen."):
        assert _real(BASE + sentence, real_sites) == [], sentence


# ---- rule a: a sentence that ends in a short number -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("Die Zahl lag bei 19. Im Juli begannen 9. In Favoriten lagen 4. Die Linie 31 wurde kurzgeführt.", 4),
        ("Betroffen war die Linie 31. Betroffen war die Linie 49. Ende.", 3),
        ("Am 2. Mai wurde im 10. Bezirk gearbeitet. Ende.", 2),
        ("Im 3. Bezirk wurde bis 14.08. gearbeitet. Das war z. B. am Rennweg so.", 2),
        ("Die Arbeiten im 2. Quartal und in der 1. Bauphase. Ende.", 2),
    ],
)
def test_a_short_number_ends_a_sentence_before_a_capitalised_word(text: str, count: int) -> None:
    assert yr.count_sentences(text) == count


# ---- rule b: a list marker and "$" -------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["- ", "+ ", "1. ", "2) "])
def test_rule_b_rejects_a_list_marker_at_the_start(marker: str) -> None:
    assert _check(marker + GOOD) == ["Aufzählungszeichen am Anfang"]
    assert yr._markup_reasons(GOOD.replace("Im Jahr 2026", "2026")) == []


def test_rule_b_rejects_a_dollar_sign() -> None:
    assert _check(_with("Dazu steht $$x^2$$ im Text.")) == ["Formatierungszeichen im Text: $"]


# ---- the default request, the stored text -----------------------------------------------------------------


def test_the_time_left_of_the_run_reaches_the_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_ask_review(prompt: str, **kwargs: Any) -> ReviewAnswer | None:
        seen.append(kwargs)
        return None

    monkeypatch.setattr(yr, "ask_review", fake_ask_review)
    yr.ensure_review(YEAR, SITES, now=NOW, stats_dir=tmp_path, key="k", deadline_s=42.5)
    yr.ensure_review(YEAR, SITES, now=NOW, stats_dir=tmp_path, key="k")
    # Without a budget the client keeps its own deadline (review_client.DEFAULT_DEADLINE_S).
    assert seen == [{"key": "k", "deadline_s": 42.5}, {"key": "k"}]


def test_a_stored_text_that_is_dropped_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _ensure(tmp_path, _answer())
    path = tmp_path / "baustellen_rueckblick_2026.json"
    stored = path.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=STATS_LOGGER):
        assert yr.load_review(YEAR, SITES[:3], stats_dir=tmp_path) is None
        path.write_text(stored.replace("Im Jahr 2026", "Im Jahr  2026"), encoding="utf-8")
        assert yr.load_review(YEAR, SITES, stats_dir=tmp_path) is None
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", "Baustellen-Rückblick 2026: der gespeicherte Text passt nicht mehr zu den Daten oder zur Prüfung: "
                    "Zahl oder Kennung nicht in den Daten: 30.09, 30.09.2026, 4, 71; Straßenname nicht in den Daten: "
                    "Rennweg, Wiedner Hauptstraße; Anzahl der Baustellen nicht aus dem Faktenblock: 4."),
        ("WARNING", "Baustellen-Rückblick 2026: der gespeicherte Text ist nicht in der Form, in der er gespeichert wird "
                    "(Leerraum oder Steuerzeichen), und wird nicht verwendet."),
    ]


def test_a_prompt_of_exactly_the_cap_is_sent_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    whole = yr.build_prompt(SITES, year=YEAR)
    monkeypatch.setattr(yr_prompt, "MAX_PROMPT_CHARS", len(whole))
    assert yr.build_prompt(SITES, year=YEAR) == whole
    monkeypatch.setattr(yr_prompt, "MAX_PROMPT_CHARS", len(whole) - 1)
    assert "- Hinweis: Die Texte der Baustellen sind in den Daten auf höchstens" in yr.build_prompt(SITES, year=YEAR)


# ---- review_client: a key Google rejects with 400, the bounds of the deadline and the key ----------------


def _rejected_key(reason: str) -> _Response:
    return _Response(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                     "status": "INVALID_ARGUMENT", "details": [{"reason": reason}]}})


def test_a_key_rejected_with_400_ends_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_rejected_key("API_KEY_INVALID"), _ok()])
    assert rc.ask_review("Prompt", key=KEY) is None and len(calls) == 1
    # Any other 400 (a model name Google no longer takes) still hands over.
    calls = _fake(monkeypatch, [_rejected_key("MODEL_NOT_FOUND"), _ok()])
    answer = rc.ask_review("Prompt", key=KEY)
    assert answer is not None and answer.model == "gemini-3.6-flash" and len(calls) == 2
    # A 400 whose body is no JSON names no rejected key either.
    calls = _fake(monkeypatch, [_Response(400, None, raw=b"kein JSON"), _ok()])
    assert rc.ask_review("Prompt", key=KEY) is not None and len(calls) == 2


def test_a_request_is_sent_with_exactly_the_minimum_left(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=KEY, deadline_s=rc.MIN_REQUEST_S, clock=_Clock()) is not None
    assert [call["timeout"] for call in calls] == [(10.0, 15.0)]
    calls = _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=KEY, deadline_s=rc.MIN_REQUEST_S - 0.5, clock=_Clock()) is None and calls == []


def test_the_key_is_sent_without_surrounding_space(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, [_ok()])
    assert rc.ask_review("Prompt", key=f"  {KEY}\n") is not None
    assert calls[0]["headers"]["x-goog-api-key"] == KEY
