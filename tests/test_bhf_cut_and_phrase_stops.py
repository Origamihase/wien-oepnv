"""Two feed text classes of 06.11.2026 (Wächter quality check, classes 15 and 16).

15: the cut behind a whole sentence took the period of "Bhf. Meidling" for a
sentence end; the N8 summary of 30.10.2026 would have ended "… zwischen
Bhf.". The same rule now serves every step (``tests/test_abbreviation_periods.py``).
16: a stop named like a phrase ("Am Bahnhof") shielded the phrase in running
text; ÖBB's "Stellwerkstörung am Bahnhof" read "interlocking fault am
Bahnhof" in the EN feed.
"""

from __future__ import annotations

import pytest

from src.feed_build.masking import _mask_entities
from src.feed_build.title_words import _last_sentence_end, _lead_with_fragment

N8_SUMMARY = (
    "Die Linie N8 wird in beiden Richtungen zwischen Bhf. Meidling S U, Schedifkaplatz und "
    "Hans-Mandl-Berufsschule über Eichenstraße und Längenfeldgasse umgeleitet."
)


def test_the_n8_summary_does_not_end_on_bhf() -> None:
    text = _lead_with_fragment("Betrieb ab Bhf. Meidling", N8_SUMMARY)
    assert not text.rstrip(" .…").endswith("Bhf")
    # Not even WL's first sentence fits behind the consequence: the text is cut
    # at a word like any other summary instead of losing all of WL's words.
    assert text == (
        "Betrieb ab Bhf. Meidling. Die Linie N8 wird in beiden Richtungen zwischen Bhf. Meidling S U, "
        "Schedifkaplatz und Hans-Mandl-Berufsschule über Eichenstraße und Längenfeldgasse …"
    )


def test_a_summary_sentence_that_fits_is_still_cut_whole() -> None:
    summary = (
        "Weichen Sie auf die Linien U3, 48A aus. Voraussichtliche Dauer: bis 22:00 Uhr. Grund: Veranstaltung "
        "im Bereich der Neubaugasse, der Burggasse und der Lerchenfelder Straße bis zum Ring und darüber hinaus."
    )
    assert _lead_with_fragment("Busse halten bei der Linie 14A", summary) == (
        "Busse halten bei der Linie 14A. Weichen Sie auf die Linien U3, 48A aus. Voraussichtliche Dauer: bis 22:00 Uhr."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Umleitung zwischen Bhf. Meidling und Schedifkaplatz",
        "Langobardenstraße 10 Hlst. Li. 95A",
        "Ersatzhaltestelle Hst. Siebenhirten",
        "Fahrt bis Wien Hbf. Richtung Süden",
        "Rotensterngasse geg. ONr. 35",
        "Hietzinger Hauptstraße 70, prov. Haltestelle",
    ],
)
def test_a_word_without_a_vowel_or_a_known_abbreviation_ends_no_sentence(text: str) -> None:
    assert _last_sentence_end(text, len(text)) is None


@pytest.mark.parametrize(
    ("text", "cut_after"),
    [
        ("Umleitung der Linien 25, 26 und S80. Die Linie 27 fährt", "S80."),
        ("Umleitung der Linie N20. Zeitraum: ab heute", "N20."),
        ("Die Züge fallen aus. Ersatz: Linie 5", "aus."),
    ],
)
def test_lines_and_words_still_end_a_sentence(text: str, cut_after: str) -> None:
    end = _last_sentence_end(text, len(text))
    assert end is not None and text[:end].endswith(cut_after)


def _shielded(text: str) -> list[str]:
    return sorted(_mask_entities(text)[1].values())


def test_a_phrase_in_running_text_is_no_stop() -> None:
    text = "Wegen einer Stellwerkstörung am Bahnhof sind in Wien Hbf derzeit keine Fahrten möglich."
    assert "am Bahnhof" not in _shielded(text)
    assert "Wien Hbf" in _shielded(text)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Aufgelassene Haltestelle: Am Bahnhof (Stadlauer Straße)", "Am Bahnhof"),
        ("Umleitung zwischen Am Platz und Preyergasse", "Am Platz"),
        ("Linie 10: Fahrtbehinderung in Richtung Unter St. Veit, Hummelgasse.", "Unter St. Veit, Hummelgasse"),
        ("Die Haltestelle STADION U ist über die U2 erreichbar.", "STADION"),
        ("Ersatzhaltestelle 1. Haidequerstraße Mitte", "1. Haidequerstraße Mitte"),
        ("Umleitung über Ölhafen und Lobau", "Ölhafen"),
        ("Züge nach Šatov fallen aus.", "Šatov"),
    ],
)
def test_a_stop_written_as_a_name_stays_shielded(text: str, name: str) -> None:
    assert name in _shielded(text)


@pytest.mark.parametrize("phrase", ["ölhafen", "šatov", "české velenice"])
def test_a_name_in_lower_case_is_no_stop(phrase: str) -> None:
    # The lowercase form of every first letter of the directory, not only a-z.
    assert phrase not in _shielded(f"Die Züge fahren bis {phrase} und zurück.")
