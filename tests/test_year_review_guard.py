"""The guard of the yearly Baustellen review, rule by rule (``src/markdown_stats/year_review_guard.py``).

Topic file of the tests described in ``tests/_year_review_support.py``: the
accepted text, then the rules a to g, each with a text it accepts and one
it rejects, and the guard reporting every broken rule at once.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.markdown_stats import baustellen_year as by
from src.markdown_stats import year_review as yr
from src.markdown_stats.baustellen_year import DISTRICT_NAMES
from tests._year_review_support import GOOD, SITES, YEAR, _check, _site, _with


# ---- the guard: the accepted text --------------------------------------------


def test_the_guard_accepts_a_text_that_uses_only_the_data() -> None:
    assert 200 <= len(GOOD) <= 1500 and yr.count_sentences(GOOD) == 5
    assert _check(GOOD) == []


def test_every_rule_is_a_helper_of_its_own_and_accepts_the_good_text() -> None:
    source = yr.source_text(SITES)
    allowed = source + "\n" + by.facts_block(SITES, year=YEAR)
    assert yr._length_reasons(GOOD) == []
    assert yr._markup_reasons(GOOD) == []
    assert yr._number_reasons(GOOD, allowed) == []
    assert yr._street_reasons(GOOD, source) == []
    assert yr._district_reasons(GOOD, SITES, source) == []
    assert yr._month_reasons(GOOD, SITES, YEAR) == []
    assert yr._voice_reasons(GOOD) == []


# ---- rule a: length and sentences ---------------------------------------------


def test_rule_a_rejects_a_text_that_is_too_short_or_too_long() -> None:
    short = "Im Jahr 2026 sind 4 Baustellen erfasst. Sie lagen in drei Bezirken. Mehr steht hier nicht."
    assert _check(short) == [f"Länge {len(short)} Zeichen, erlaubt sind 200 bis 1500"]
    long = " ".join(["Im Jahr 2026 sind 4 Baustellen mit Folgen für den öffentlichen Verkehr erfasst."] * 19)
    assert len(long) > 1500
    assert _check(long) == [f"Länge {len(long)} Zeichen, erlaubt sind 200 bis 1500", "19 Sätze, erlaubt sind 3 bis 8"]


def test_rule_a_accepts_the_bounds_themselves() -> None:
    def sized(length: int) -> str:
        start = "Ein Satz. Noch ein Satz. Der dritte Satz "
        return start + "x" * (length - len(start) - 1) + "."

    assert (len(sized(200)), len(sized(1500))) == (200, 1500)
    assert yr._length_reasons(sized(200)) == [] and yr._length_reasons(sized(1500)) == []
    assert yr._length_reasons(sized(199)) == ["Länge 199 Zeichen, erlaubt sind 200 bis 1500"]
    assert yr._length_reasons(sized(1501)) == ["Länge 1501 Zeichen, erlaubt sind 200 bis 1500"]
    three = " ".join(["Am Rennweg wurde an den Gleisen gearbeitet, und zwar über lange Zeit hinweg ohne Pause."] * 3)
    eight = " ".join(["Am Rennweg wurde an den Gleisen gearbeitet."] * 8)
    assert yr._length_reasons(three) == [] and yr._length_reasons(eight) == []


def test_rule_a_rejects_too_few_and_too_many_sentences() -> None:
    two = ("Im Jahr 2026 sind 4 Baustellen mit Folgen für den öffentlichen Verkehr erfasst, und sie lagen in der "
           "Burggasse, am Rennweg, in der Eßlinger Hauptstraße und in der Vorderen Zollamtsstraße, wo jeweils "
           "gearbeitet wurde. Mehr steht in den Daten nicht dazu.")
    assert len(two) >= 200 and _check(two) == ["2 Sätze, erlaubt sind 3 bis 8"]
    nine = " ".join(["Am Rennweg wurde an den Gleisen gearbeitet."] * 9)
    assert _check(nine) == ["9 Sätze, erlaubt sind 3 bis 8"] and yr._length_reasons("x" * 250 + ".") == ["1 Satz, erlaubt sind 3 bis 8"]


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("", 0),
        ("Ein Satz ohne Punkt", 1),
        ("Ein Satz. Zwei Sätze! Drei Sätze? Vier", 4),
        # A full stop after a short number, one letter or an abbreviation closes no sentence.
        ("Im 3. Bezirk wurde bis 14.08. gearbeitet. Das war z. B. am Rennweg bzw. in der Burggasse so.", 2),
        # A year does close one.
        ("Die Arbeiten endeten 2026. Danach war die Strecke frei.", 2),
        ("Die Arbeiten endeten am 30.10.2026. Danach war die Strecke frei.", 2),
        # A short number at the very end still ends the text.
        ("Die Linie 71 wurde umgeleitet. Betroffen war auch die Linie 5.", 2),
    ],
)
def test_sentences_are_counted_by_their_closing_punctuation(text: str, count: int) -> None:
    assert yr.count_sentences(text) == count


# ---- rule b: markup and links ---------------------------------------------------


@pytest.mark.parametrize("char", list("<>[]{}|`*_#\\"))
def test_rule_b_rejects_every_markup_character(char: str) -> None:
    assert _check(_with(f"Die Linie {char} fuhr.")) == [f"Formatierungszeichen im Text: {char}"]


def test_rule_b_rejects_markup_as_a_model_would_write_it() -> None:
    assert _check(_with("Die **Burggasse** war gesperrt.")) == ["Formatierungszeichen im Text: *"]
    assert _check(_with("Mehr dazu [hier](https://example.org).")) == ["Formatierungszeichen im Text: [, ]", "Link im Text"]


@pytest.mark.parametrize("link", ["http://example.org", "HTTPS://example.org", "www.example.org", "WWW.wien.gv.at"])
def test_rule_b_rejects_a_link(link: str) -> None:
    assert "Link im Text" in _check(_with(f"Mehr steht auf {link} dazu."))


# ---- rule c: numbers, dates, lines ------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Die Linie 26A fuhr anders.",                 # a line of the data
        "Betroffen war die U4.",                      # "U4-Trasse" in the data
        "Gearbeitet wurde von 20:00 bis 05:00 Uhr.",  # times of the data
        "Die Arbeiten begannen am 25.05.2026.",       # a date of the data
        "Die Arbeiten begannen am 25.05. im Bezirk.", # its short form
        "Die Haltestelle lag bei Nummer 67.",         # "ONr.67"
        "Im 22. Bezirk lag 1 Baustelle.",             # numbers of the facts block
        "Am Rennweg ging es von 33A bis 37.",
    ],
)
def test_rule_c_accepts_what_stands_in_the_data_or_the_facts(sentence: str) -> None:
    assert _check(_with(sentence)) == []


@pytest.mark.parametrize(
    ("sentence", "token"),
    [
        ("Insgesamt waren es 77 Stellen.", "77"),               # a number not in the data (before "Baustellen" also rule h)
        ("Die Linie 13A fuhr anders.", "13A"),                  # a line not in the data
        ("Betroffen war die U6.", "U6"),
        ("Die Arbeiten begannen am 14.08. im Bezirk.", "14.08"),  # a date not in the data
        ("Die Arbeiten enden 2031.", "2031"),
        ("Die Arbeiten begannen am 25. Tag.", "25"),            # a day alone is not derived from a date
        ("Gearbeitet wurde ab 21:00 Uhr.", "21:00"),
        ("Die Haltestelle lag bei Nummer 68.", "68"),
        ("Die Linie 26a fuhr anders.", "26a"),                  # not in the same form
    ],
)
def test_rule_c_rejects_a_number_date_or_line_that_is_not_in_the_data(sentence: str, token: str) -> None:
    assert _check(_with(sentence)) == [f"Zahl oder Kennung nicht in den Daten: {token}"]


def test_rule_c_names_five_tokens_and_counts_the_rest() -> None:
    text = _with("Es waren 91, 92, 93, 94, 95, 97 und 98 Stellen.")
    assert _check(text) == ["Zahl oder Kennung nicht in den Daten: 91, 92, 93, 94, 95 und 2 weitere"]


# ---- rule d: streets ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Die Burggasse war betroffen.",
        "Der Rennweg war betroffen.",                    # "Der" opens the sentence, it is no part of a name
        "In der Wiedner Hauptstraße fuhr die Linie 71.",
        "Bei der Gartenheimstraße lag ein Kreisverkehr.",
        "Die Radetzkybrücke war nahe.",
        "Im Bezirk Landstraße wurde gearbeitet.",        # the district's name stands with its number in the data
    ],
)
def test_rule_d_accepts_streets_of_the_data(sentence: str) -> None:
    assert _check(_with(sentence)) == []


@pytest.mark.parametrize(
    ("sentence", "name"),
    [
        ("Die Zieglergasse war betroffen.", "Zieglergasse"),                  # a street not in the data
        ("In der Mariahilfer Straße wurde gearbeitet.", "Mariahilfer Straße"),
        # Both words stand in the data, the name does not.
        ("In der Eßlinger Gartenheimstraße wurde gearbeitet.", "Eßlinger Gartenheimstraße"),
        ("In der Hernalser Hauptstraße wurde gearbeitet.", "Hernalser Hauptstraße"),
        ("Am Schottenring wurde gearbeitet.", "Schottenring"),
        ("Am Franz-Josefs-Kai wurde gearbeitet.", "Franz-Josefs-Kai"),
        # A capitalised name counts in any case of its other letters.
        ("Die BURGGASSE war gesperrt, die ZIEGLERGASSE auch.", "ZIEGLERGASSE"),
        ("Der Verkehr wich auf den Vorplatz aus.", "Vorplatz"),  # a noun with such an ending outside the ordinary words
        ("Der Wiener Radweg war gesperrt.", "Wiener Radweg"),    # an ordinary word as part of a name
    ],
)
def test_rule_d_rejects_a_street_that_is_not_in_the_data(sentence: str, name: str) -> None:
    assert _check(_with(sentence)) == [f"Straßenname nicht in den Daten: {name}"]


def test_rule_d_covers_every_street_ending() -> None:
    assert set(yr.STREET_SUFFIXES) == {
        "straße", "strasse", "gasse", "platz", "ring", "gürtel", "brücke", "kai", "weg", "allee", "markt",
        "steg", "zeile", "lände", "damm", "ufer",
    }
    for word in (f"Test{suffix}" for suffix in yr.STREET_SUFFIXES):
        assert yr._street_reasons(f"Die Stelle {word} war gesperrt.", "kein Name") == [f"Straßenname nicht in den Daten: {word}"]
        assert yr._street_reasons(f"Die Stelle {word} war gesperrt.", f"Ort: {word} 5") == []


# ---- rule e: districts -----------------------------------------------------------------


def test_rule_e_accepts_the_districts_of_the_sites() -> None:
    assert _check(_with("Betroffen waren Landstraße, Neubau und die Donaustadt.")) == []
    # A district that only a description names counts too.
    sites = [*SITES, _site("Lerchenfelder Gürtel 1", date(2026, 3, 1), date(2026, 3, 31), "", "Straßenbau",
                           "Die Umleitung führt durch Ottakring.")]
    text = _with("Eine Umleitung führte durch Ottakring.").replace("sind 4 Baustellen", "sind 5 Baustellen")
    assert _check(text, sites) == []
    assert _check(text.replace("Ottakring", "Hernals"), sites) == ["Bezirk nicht in den Daten: Hernals"]


@pytest.mark.parametrize("name", ["Favoriten", "Innere Stadt", "Rudolfsheim-Fünfhaus", "Liesing"])
def test_rule_e_rejects_a_district_that_is_not_in_the_data(name: str) -> None:
    assert _check(_with(f"Auch der Bezirk {name} war betroffen.")) == [f"Bezirk nicht in den Daten: {name}"]


def test_rule_e_reads_a_district_name_only_as_a_word_of_its_own() -> None:
    # "Wiedner Hauptstraße" is in the data; Wieden, the 4th district, is not named by it.
    assert _check(_with("Die Wiedner Hauptstraße war betroffen.")) == []
    assert _check(_with("Auch Wieden war betroffen.")) == ["Bezirk nicht in den Daten: Wieden"]
    for form, name in (("in Wien-Liesing", "Liesing"), ("in der Inneren Stadt", "Innere Stadt"), ("im Bezirk Wien-Favoriten", "Favoriten")):
        assert _check(_with(f"Auch {form} wurde gebaut.")) == [f"Bezirk nicht in den Daten: {name}"]


def test_rule_e_knows_the_23_districts_by_number() -> None:
    for number, name in enumerate(DISTRICT_NAMES, 1):
        site = _site("Ort 1", date(2026, 3, 1), date(2026, 3, 31), str(number), "Straßenbau")
        other = _site("Ort 1", date(2026, 3, 1), date(2026, 3, 31), str(number % 23 + 1), "Straßenbau")
        text = f"Gearbeitet wurde im Bezirk {name}."
        assert yr._district_reasons(text, [site], yr.source_text([site])) == []
        assert yr._district_reasons(text, [other], yr.source_text([other])) == [f"Bezirk nicht in den Daten: {name}"]


# ---- rule f: months --------------------------------------------------------------------


def test_rule_f_accepts_a_month_a_period_touches() -> None:
    assert _check(_with("Im Mai und im Dezember wurde gearbeitet.")) == []


@pytest.mark.parametrize("month", ["Jänner", "Januar"])
def test_rule_f_rejects_a_month_outside_all_periods(month: str) -> None:
    assert _check(_with(f"Im {month} wurde gearbeitet.")) == [f"Monat außerhalb aller Zeiträume: {month}"]


def test_rule_f_knows_every_month_name() -> None:
    one = [_site("x", date(2026, 6, 1), date(2026, 6, 30), "3", "y")]
    for name in (*by.MONTH_NAMES, "Januar", "Feber"):
        reasons = yr._month_reasons(f"Im {name} wurde gearbeitet.", one, YEAR)
        assert reasons == ([] if name == "Juni" else [f"Monat außerhalb aller Zeiträume: {name}"])
    # Part of another word is no month.
    assert yr._month_reasons("Die Maifeier und der Augustinerplatz.", one, YEAR) == []


# ---- rule g: voice ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "marker"),
    [
        ("Das fasse ich zusammen.", "ich"),
        ("Ich fasse das zusammen.", "ich"),
        ("Hier sehen wir viele Arbeiten.", "wir"),
        ("Wir fassen das zusammen.", "wir"),
        ("Sie sollten mehr Zeit einplanen.", "Sie sollten"),
        ("Planen Sie bitte mehr Zeit ein.", "bitte"),
        ("Bitte mehr Zeit einplanen.", "bitte"),
    ],
)
def test_rule_g_rejects_first_person_address_and_advice(sentence: str, marker: str) -> None:
    assert _check(_with(sentence)) == [f"Ich-Form, Anrede oder Rat im Text: {marker}"]


def test_rule_g_reads_its_markers_as_words() -> None:
    assert _check(_with("Die Arbeiten wirkten sich gleich aus, und sie zeigten Wirkung.")) == []
    assert [label for label, _ in yr._VOICE_MARKERS] == ["ich", "wir", "du", "Sie sollten", "bitte"]


def test_the_guard_reports_every_broken_rule() -> None:
    text = _with("Bitte beachten Sie im Jänner die **77** Baustellen in Favoriten und in der Zieglergasse.")
    assert _check(text) == [
        "Formatierungszeichen im Text: *",
        "Zahl oder Kennung nicht in den Daten: 77",
        "Straßenname nicht in den Daten: Zieglergasse",
        "Bezirk nicht in den Daten: Favoriten",
        "Monat außerhalb aller Zeiträume: Jänner",
        "Ich-Form, Anrede oder Rat im Text: bitte",
    ]
