"""EN sense errors as classes (check of 2026-10-08).

Every distinct German text of the feed since July went through the real
model. These tests pin the structural rules built from that check, each
with the real WL / Stadt-Wien wording it came from. The model is a stub
that echoes its input (or knows the cause phrase), so the assertions check
what the model is shown and what the rules write without it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed

_WL = "Wiener Linien"
_BST = "Stadt Wien – Baustellen"


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """An echo model that renders WL's cause phrases, recording its inputs."""
    seen: list[str] = []
    phrases = {
        "Wegen einer": "Due to a",
        "Wegen eines": "Due to a",
        "Wegen ": "Due to ",
    }

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        for german, english in phrases.items():
            if text.startswith(german):
                return [{"translation_text": english + text[len(german):]}]
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: pipe)
    return seen


def _model_input(german: str) -> str:
    """What the model is shown for *german* (glossary terms resolved)."""
    glossed, glossary = build_feed._apply_domain_glossary(
        build_feed._normalise_for_translation(german), source=_WL
    )
    masked, entities = build_feed._mask_entities(glossed)
    for placeholder, english in glossary.items():
        masked = masked.replace(placeholder, f"<{english}>")
    for placeholder, surface in entities.items():
        masked = masked.replace(placeholder, f"[{surface}]")
    return masked


@pytest.mark.parametrize(
    ("german", "english"),
    [
        (
            "Wegen einer Demonstration kommt es zu Verkehrsmaßnahmen.",
            "Due to a demonstration, there are service changes.",
        ),
        (
            "Wegen einer Demonstration kommt es zu folgenden Verkehrsmaßnahmen.",
            "Due to a demonstration, the following service changes apply.",
        ),
        (
            "Wegen einer Veranstaltung im Bereich Landstraße-Wien Mitte kommt es zu "
            "folgenden Verkehrsmaßnahmen für die Linie 74A.",
            "Due to an event in the area of Landstraße-Wien Mitte, the following service "
            "changes apply to line 74A.",
        ),
        (
            "Wegen Demonstrationen im Bereich Innenstadt, Währinger Straße kommt es zu "
            "Verkehrsmaßnahmen.",
            "Due to Demonstrationen in the area of Innenstadt, Währinger Straße, there "
            "are service changes.",
        ),
    ],
)
def test_wl_measures_sentence_is_written_without_the_model(
    model: list[str], german: str, english: str
) -> None:
    # "transport is taking place" / "it comes to traffic measures" before;
    # only the cause goes through the model now.
    assert build_feed._translate_text_attempt(german, source=_WL) == english
    assert all("Verkehrsmaßnahmen" not in text for text in model)


def test_wl_measures_sentence_keeps_the_record_behind_it(model: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen eines Straßenfestes im Bereich der Wollzeile kommt es zu Verkehrsmaßnahmen. "
        "Zeitraum: Samstag, 05. September 2026, von Betriebsbeginn bis Betriebsschluss.",
        source=_WL,
    )
    assert out is not None
    assert out.startswith("Due to a street festival in the area of ")
    assert "Wollzeile, there are service changes. " in out
    assert "of of" not in out


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        # WL's second phrase for irregular intervals: "different intervals".
        (
            "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "<irregular intervals>",
        ),
        # Display-board shorthand left German or read as "Mostly".
        ("Züge halten bei Li. O Ri. Raxstraße.", "<trains stop> <at the stop of line> O Richtung"),
        ("Betrieb über Li. 40+9.", "über Linie"),
        ("38A: Hlst. verlegt nach Grinzinger Allee 47", "<stop moved to>"),
        ("12: Fahrleitungsgebr Einstieg bei Linie 31", "<overhead-line fault>"),
        ("40A: Fahrleitungs- gebrechen Einstieg bei 40A", "<overhead-line fault>"),
        # "for line 44": the stop of another line, as for the stop verbs.
        ("Einstieg bei Linie 18 Richtung Burggasse.", "<boarding> <at the stop of line> [18]"),
        (
            "10: Fremdunfall Einstieg Thaliastraße bei Linie 44",
            "[Thaliastraße] <at the stop of line> [44]",
        ),
        # A cross street behind "nach": "according to".
        (
            "13A: Busse halten Gumpendorfer Straße nach Kopernikusgasse",
            "<buses stop at> [Gumpendorfer Straße] <just after> [Kopernikusgasse]",
        ),
        # "über Gleis 2" → "via 2 track".
        ("Die Linie U1 fährt in beiden Richtungen über Gleis 2.", "<on track 2>"),
        # "Ascent A", "Climb A".
        ("Züge halten Steig A", "<trains stop at> <platform> A"),
        # "line 14A is short", "operating short-running".
        ("Wegen Bauarbeiten wird die Linie 14A kurz geführt.", "die Linie [14A] <is curtailed>"),
        # "Lock output", "Curfew".
        ("U1: Sperre Ausgang beim Keplerplatz", "<exit closure>"),
        ("Wegen dringend notwendigem Weichentausch", "<switch replacement>"),
        # "diversion to avoid Wattgasse".
        ("9: Fremdunfall Umleitung zur Wattgasse, 10A ausweichen", "<alternatively use line> [10A]"),
        ("Bitte auf nahegelegene Haltestellen ausweichen.", "<switch to nearby stops>"),
    ],
)
def test_wl_wording_reaches_the_model_in_a_form_it_renders(german: str, shown: str) -> None:
    assert shown in _model_input(german)


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        (
            "Die Haltestelle Spittelau kann derzeit nicht eingehalten werden.",
            "[Spittelau] <is not served> derzeit",
        ),
        ("38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten", "[Kahlenberg] <is not served>"),
        (
            "Die Haltestellen Stadionbrücke bis Stadion U können dadurch nicht eingehalten werden.",
            "<are not served> dadurch",
        ),
        (
            "Derzeit können die Züge der Linie U6 die Station Alser Straße in beiden "
            "Richtungen nicht einhalten.",
            "halten die Züge der Linie [U6] in beiden Richtungen nicht in der Station",
        ),
        (
            "Wegen Sanierung des Bahnsteigs hält die Linie U6 die Station Neue Donau U nur "
            "in Richtung Siebenhirten U ein.",
            "hält die Linie [U6] an der Station [Neue Donau] U nur in Richtung",
        ),
    ],
)
def test_a_stop_left_out_is_not_served(german: str, shown: str) -> None:
    # "cannot be maintained", "cannot comply with", "stops the station".
    assert shown in _model_input(german)


@pytest.mark.parametrize(
    ("german", "name"),
    [
        ("Wegen einer Demonstration am Ring", "[Ring]"),
        ("Betrieb nur zwischen Ring, Volkstheater U und Breitensee S.", "[Ring]"),
        ("Haltestellenverlegung der Linie N66 in Richtung Oper", "[Oper]"),
        ("Grund: Demonstration im Bereich Innere Stadt.", "[Innere Stadt]"),
        ("in Richtung Laaer Berg, Kurpark, Nordosteingang, sowie 68B", "[Kurpark, Nordosteingang]"),
        ("13A: Feuerwehreinsatz Umleitung über Gürtel bis Kolschitzkygasse", "[Gürtel]"),
        ("2: Schadhafter Zug Betrieb ab Julius Raab Platz", "[Julius Raab Platz]"),
        ("74A: Busse halten Victor Braun Platz 1", "[Victor Braun Platz]"),
        ("Verkehrsunfall im Bereich Erlaaer Schleife.", "[Erlaaer Schleife]"),
        ("Haltestellenverlegung der Linien 30 und 31 in Richtung Stammersdrof", "[Stammersdrof]"),
    ],
)
def test_place_names_stay_names(german: str, name: str) -> None:
    # "on the ring", "towards opera", "Inner City", "spa park", "belt",
    # "Julius Raab Square", "Erlaaer loop", "Stammersdrif".
    assert name in _model_input(german)


@pytest.mark.parametrize(
    "german",
    [
        "Die Auffahrt auf die A22 in Richtung Süden ist nicht möglich.",
        "Fahrtrichtung Zentrum",
        "Züge halten in Schleife, Wipplingerstr 39",
        "Wegen Bauarbeiten am Opernring",
        "Der Währinger Gürtel ist gesperrt.",
    ],
)
def test_ordinary_words_and_compounds_are_not_taken_for_names(german: str) -> None:
    shown = _model_input(german)
    assert "[Süden]" not in shown
    assert "[Zentrum]" not in shown
    assert "[Schleife]" not in shown
    assert "[Ring]" not in shown
    assert "[Gürtel]" not in shown


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        ("von Burggasse ONr.67 nach Burggasse ONr. 69 verlegt", "<No.> [67]"),
        ("Atzgersdorfer Straße in Höhe ONr.42", "[Atzgersdorfer Straße] <No.> [42]"),
        ("in der Nebenfahrbahn", "<service road>"),
        ("in der betriebslosen Zeit des öffentlichen Verkehrsmittels", "<outside operating hours>"),
        ("Die restlichen Fahrstreifen werden aufrechtgehalten.", "Fahrstreifen <are maintained>"),
        ("Der Fußgängerverkehr kann aufrecht gehalten werden.", "<can be maintained>"),
        ("Der linke Fahrstreifen wird gesperrt.", "<is closed>"),
        (
            "Der linke Fahrstreifen wird in Fahrtrichtung stadteinwärts gesperrt.",
            "Der linke Fahrstreifen <is closed> in Fahrtrichtung stadteinwärts",
        ),
    ],
)
def test_roadworks_vocabulary(german: str, shown: str) -> None:
    # "ONr.67", "sidecar", "busy time", "held upright", "is locked".
    assert shown in _model_input(german)


def test_kommt_es_bei_der_linie_stays_on_the_ordinary_path() -> None:
    # Only boarding is the stop of another line; "kommt es bei der Linie
    # N31 zu …" is the line itself.
    shown = _model_input(
        "Derzeit kommt es bei der Linie N31 in beiden Richtungen zu unterschiedlichen Intervallen."
    )
    assert "bei der Linie [N31]" in shown


def test_a_list_of_lines_before_ausweichen_is_left_alone() -> None:
    shown = _model_input("Bitte auf die Linien U2 und U3 ausweichen.")
    assert "alternatively" not in shown


def test_in_view_of_reads_due_to() -> None:
    assert build_feed._cause_due_to("In view of a demonstration, …") == "Due to a demonstration, …"


def test_night_line_intervals_are_written_without_the_model(model: list[str]) -> None:
    # "irregular intervals is used in both directions on the N31 line".
    out = build_feed._translate_text_attempt(
        "Derzeit kommt es bei der Linie N31 in beiden Richtungen zu unterschiedlichen "
        "Intervallen. Grund: Polizeieinsatz.",
        source=_WL,
    )
    assert out == (
        "Currently, there are irregular intervals on line N31 in both directions. "
        "Reason: police operation."
    )


@pytest.mark.parametrize(
    ("german", "english"),
    [
        (
            "Betrieb ab Anschützgasse. Nach einer Fahrtbehinderung kommt es zu "
            "unterschiedlichen Intervallen.",
            "After a service obstruction, there are irregular intervals.",
        ),
        (
            "Verspätungen: Derzeit kommt es bei der Linie N49 in beiden Richtungen zu "
            "unterschiedlichen Intervallen.",
            "Delays: Currently, there are irregular intervals on line N49 in both directions.",
        ),
    ],
)
def test_other_interval_sentences(model: list[str], german: str, english: str) -> None:
    out = build_feed._translate_text_attempt(german, source=_WL)
    assert out is not None and out.endswith(english)


@pytest.mark.parametrize(
    ("german", "shown"),
    [
        # A subject between auxiliary and participle stays in front.
        (
            "Deshalb werden die Busse der Linie 85A in Richtung Breitenlee kurzgeführt.",
            "die Busse der Linie [85A] in Richtung [Breitenlee] <are curtailed>",
        ),
        # A modal: "must be be closed" before.
        (
            "muss in der Station Keplerplatz der rechte Ausgang gesperrt werden.",
            "<must be closed> in der Station",
        ),
        ("Die restlichen Fahrstreifen bleiben aufrecht.", "<remain open>"),
    ],
)
def test_glossed_verbs_keep_auxiliary_and_participle_together(german: str, shown: str) -> None:
    assert shown in _model_input(german)


def test_a_crossing_is_not_taken_for_a_spaced_street_name() -> None:
    shown = _model_input("Nußdorfer Straße Kreuzung Währinger Straße und Spitalgasse")
    assert "Kreuzung [Währinger Straße]" in shown


def test_a_category_word_in_front_keeps_the_cause(model: list[str]) -> None:
    # 05.10.: "Refurbishing the platform The U6 line stops …", cause lost.
    out = build_feed._translate_text_attempt(
        "Ausgangssperre Wegen einer Demonstration kommt es zu Verkehrsmaßnahmen.",
        source=_WL,
    )
    assert out == "Exit closure Due to a demonstration, there are service changes."
