"""EN audit 2026-10-03: every DE/EN pair of the published feeds compared.

The 6,561 versions of ``docs/feed.en.xml`` since 2026-05-20 were laid next to
their German counterparts, item by item (4,957 distinct pairs). Each test
below pins one error class that was still live under translation epoch 19,
with the published rendering it replaces. The examples are the real source
texts; the model is replaced by a stub, because what is tested is what the
pipeline hands the model and what it does without one.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _mask_entities,
    _normalise_for_translation,
    _render_label_record,
    _split_label_record,
    _unmask_entities,
)

_WL = "Wiener Linien"
_OEBB = "ÖBB"


def _echo(seen: list[str] | None = None) -> Any:
    def echo(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        if seen is not None:
            seen.append(text)
        return [{"translation_text": text}]

    return echo


def _shielded(text: str) -> list[str]:
    _masked, mapping = _mask_entities(text)
    return sorted(mapping.values())


# ---------------- WL reason vocabulary ----------------


@pytest.mark.parametrize(
    ("title", "english", "was_published_as"),
    [
        ("43: Fremder Verkehrsunfall", "43: Third-party traffic accident", "43: External traffic accident"),
        ("6: Stromstörung", "6: Power failure", "6: Current disturbance"),
        ("59A: Beschädigte Oberleitung", "59A: Damaged overhead line", "59A: Corrupted overhead line"),
        ("16A/17A/67A: Störung an einem Bahnübergang", "16A/17A/67A: Level crossing fault", "… Interference at a level crossing"),
        ("2: Weichenschaden", "2: Switch damage", "2: Soft damage"),
        ("48A: Gasrohrgebrechen", "48A: Burst gas pipe", "48A: Gas pipe fractures"),
    ],
)
def test_wl_reason_titles_need_no_model(
    monkeypatch: Any, title: str, english: str, was_published_as: str
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _echo(seen))
    out = build_feed._translate_title_attempt(title, source=_WL)
    assert out is not None
    assert build_feed._capitalise_title_body(out) == english, was_published_as
    assert seen == [], "a fully glossed title must not reach the model"


def test_ticker_verbs_are_glossed() -> None:
    glossed, mapping = build_feed._apply_domain_glossary(
        "Kein Betrieb; Züge halten in Schleife, Wipplingerstr 39.", source=_WL
    )
    rendered = _unmask_entities(glossed, mapping)
    assert rendered == "no service; trains stop in the loop, Wipplingerstr 39."


# ---------------- label records: the reason ----------------


def test_the_qualifier_travels_with_its_label() -> None:
    prose, record = _split_label_record(
        "Fahrtbehinderung in Richtung Rodaun. Voraussichtliche Dauer: 11:50 Uhr. "
        "Grund: Falschparker im Bereich Geßlgasse 10."
    )
    assert prose == "Fahrtbehinderung in Richtung Rodaun."
    assert record.startswith("Voraussichtliche Dauer:")


def test_a_known_reason_renders_without_the_model(monkeypatch: Any) -> None:
    """Published 2026-10-03 11:31: "Duration: 13:50. Reason: Fremder traffic
    accident in the area of Hernalser Hauptstraße"."""
    seen: list[str] = []
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _echo(seen))
    out = _render_label_record(
        "Voraussichtliche Dauer: 13:50 Uhr. Grund: Fremder Verkehrsunfall im "
        "Bereich Hernalser Hauptstraße.",
        source=_WL,
        category=None,
    )
    assert out == (
        "Expected duration: 13:50. Reason: third-party traffic accident in the "
        "area of Hernalser Hauptstraße."
    )
    assert seen == []


def test_the_stop_area_is_english(monkeypatch: Any) -> None:
    """Published 2026-10-03 10:23: "Reason: rescue operation im
    Haltestellenbereich Taubstummengasse"."""
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", _echo)
    out = _render_label_record(
        "Voraussichtliche Dauer: 12:30 Uhr. Grund: Rettungseinsatz im "
        "Haltestellenbereich Taubstummengasse.",
        source=_WL,
        category=None,
    )
    assert out == (
        "Expected duration: 12:30. Reason: rescue operation in the stop area of "
        "Taubstummengasse."
    )


def test_an_unknown_reason_goes_through_the_model(monkeypatch: Any) -> None:
    """Before: "Reason: Tiere im Gleis im Haltestellenbereich Reumannplatz"
    with nothing translated, because the record never saw the model."""
    seen: list[str] = []
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _echo(seen))
    out = _render_label_record(
        "Dauer: 14:00 Uhr. Grund: Hitzeschäden im Haltestellenbereich Reumannplatz.",
        source=_WL,
        category=None,
    )
    assert out is not None and out.startswith("Duration: 14:00. Reason:")
    assert len(seen) == 1
    assert "Hitzeschäden" in seen[0]
    assert "Dauer" not in seen[0], "only the reason may reach the model"


def test_a_failed_reason_fails_the_record(monkeypatch: Any) -> None:
    def dropping(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": re.sub(r"XENT\w+?X\d+X", "", text, count=1)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: dropping)
    assert build_feed._translate_text_attempt(
        "Fahrtbehinderung. Voraussichtliche Dauer: 14:00 Uhr. Grund: Hitzeschäden "
        "im Haltestellenbereich Reumannplatz.",
        source=_WL,
    ) is None


def test_relocation_distance_is_english() -> None:
    out = _render_label_record(
        "Haltestelle: Konstanziagasse Von: Erzherzog-Karl-Straße 135 Nach: ca. 30 "
        "Meter gegen die Fahrtrichtung Dauer: Ab …",
        source=_WL,
        category=None,
    )
    assert out == (
        "Stop: Konstanziagasse From: Erzherzog-Karl-Straße 135 To: Approx. 30 "
        "metres against the direction of travel Duration: From …"
    )


# ---------------- clock times and stock phrases ----------------


@pytest.mark.parametrize(
    ("german", "normalised"),
    [
        ("Voraussichtliche Dauer: 13 Uhr.", "Voraussichtliche Dauer: 13:00."),
        ("bis 13.50 Uhr", "bis 13:50"),
        ("um 9 Uhr", "um 9:00"),
        ("bis 15:13 Uhr", "bis 15:13"),
        ("von 02.11.2026 bis 03.11.2026", "von 02.11.2026 bis 03.11.2026"),
    ],
)
def test_bare_hours_become_clock_times(german: str, normalised: str) -> None:
    assert _normalise_for_translation(german) == normalised


def test_the_referral_loses_its_separable_aus() -> None:
    assert _normalise_for_translation(
        "Weichen Sie ersatzweise auf die Linien S40, U2, 71 und 40A. aus. Grund: X."
    ) == "Weichen Sie ersatzweise auf die Linien S40, U2, 71 und 40A. Grund: X."


def test_a_stop_named_after_a_line(monkeypatch: Any) -> None:
    """Published 2026-10-03: "Buses stop at N71"."""
    seen: list[str] = []
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _echo(seen))
    out = build_feed._translate_text_attempt("Busse halten bei Haltestelle N71.", source=_WL)
    assert out == "buses stop at the stop of line N71."
    assert seen == []
    assert _normalise_for_translation("bei Haltestelle Schwedenplatz") == (
        "bei Haltestelle Schwedenplatz"
    )


# ---------------- names ----------------


@pytest.mark.parametrize(
    ("text", "name", "was_published_as"),
    [
        ("vor Schloßhofer Straße", "Schloßhofer Straße", "Schlosshofer Straße"),
        ("zwischen Keplerplatz und Südtiroler Platz", "Südtiroler Platz", "South Tyrolean square"),
        ("Züge halten Währinger Gürtel 164", "Währinger Gürtel", "keep moving belt"),
        ("über die Simmeringer Hauptstraße", "Simmeringer Hauptstraße", "—"),
        ("Richtung Burggasse, Stadthalle", "Burggasse, Stadthalle", "Burggasse, city hall"),
        ("in Wien Hbf (U)", "Wien Hbf", "Vienna Hbf"),
        ("in St.Pölten Hbf", "St.Pölten Hbf", "—"),
    ],
)
def test_names_are_shielded(text: str, name: str, was_published_as: str) -> None:
    assert name in _shielded(text), was_published_as


@pytest.mark.parametrize(
    "phrase", ["Dieser Platz ist gesperrt", "Der Weg", "Jeder Weg", "Über Weg", "Große Straße"]
)
def test_determiners_stay_translatable(phrase: str) -> None:
    assert _shielded(phrase) == []


def test_the_stadium_keeps_its_name() -> None:
    glossed, mapping = build_feed._apply_domain_glossary(
        "im Nahbereich des Allianz Stadions"
    )
    assert _unmask_entities(glossed, mapping) == "im Nahbereich des Allianz Stadion"


# ---------------- ÖBB templates ----------------


@pytest.fixture
def cause_stub(monkeypatch: Any) -> list[str]:
    """The model as far as ÖBB templates need it: only the cause reaches it."""
    seen: list[str] = []
    causes = {
        "Wegen eines Polizeieinsatzes": "Due to a police operation",
        "Wegen einer ": "Due to a ",
        "Wegen ": "Due to ",
    }

    def model(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        for german, english in causes.items():
            if text.startswith(german):
                return [{"translation_text": english + text[len(german):]}]
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: model)
    return seen


@pytest.mark.parametrize(
    ("german", "english"),
    [
        (
            "Wegen eines Polizeieinsatzes waren zwischen Wien Meidling Bahnhof (U) und "
            "Wien Liesing Bahnhof bis 20:37 Uhr nur eingeschränkt Fahrten möglich.",
            "Due to a police operation, trains could only run to a limited extent "
            "between Wien Meidling station (U) and Wien Liesing station until 20:37.",
        ),
        (
            "Wegen eines Polizeieinsatzes sind zwischen Wien Meidling Bahnhof (U) und "
            "Wien Liesing Bahnhof bis voraussichtlich 20:30 Uhr keine Fahrten möglich.",
            "Due to a police operation, no trains can run between Wien Meidling "
            "station (U) and Wien Liesing station until approx. 20:30.",
        ),
        (
            "Wegen einer Weichenstörung sind in Wien Stadlau Bahnhst (U) Zugfahrten "
            "bis voraussichtlich 21:30 Uhr nur eingeschränkt möglich.",
            "Due to a switch fault, trains can only run to a limited extent at "
            "Wien Stadlau station (U) until approx. 21:30.",
        ),
        (
            "Wegen einer Weichenstörung sind zwischen Wien Hbf (U) und Wien Simmering "
            "Bahnhof derzeit keine Fahrten möglich.",
            "Due to a switch fault, no trains can run between Wien Hbf (U) and "
            "Wien Simmering station at present.",
        ),
        (
            "Wegen Bauarbeiten können von 02.11.2026 bis 03.11.2026 zwischen Wien "
            "Franz-Josefs-Bahnhof und St.Andrä-Wördern Bahnhof keine R 40-Züge fahren.",
            "Due to construction works, no R 40 trains can run between Wien "
            "Franz-Josefs-Bahnhof and St.Andrä-Wördern station from 02.11.2026 to "
            "03.11.2026.",
        ),
    ],
)
def test_oebb_sentences_are_rendered_from_their_slots(
    cause_stub: list[str], german: str, english: str
) -> None:
    assert build_feed._translate_text_attempt(german, source=_OEBB) == english
    assert all(text.startswith("Wegen ") for text in cause_stub)


@pytest.mark.parametrize(
    ("german", "english"),
    [
        # Real feed sentences (fourth check 2026-10-03): the date in front of
        # the time and "voraussichtlich bis" belonged in the station slot.
        (
            "Wegen eines Unfalles sind zwischen Wien Hbf (U) und Gramatneusiedl "
            "Bahnhof Zugfahrten bis voraussichtlich 27.08.2026, 23:59 Uhr nur "
            "eingeschränkt möglich.",
            "trains can only run to a limited extent between Wien Hbf (U) and "
            "Gramatneusiedl station until approx. 27.08.2026, 23:59.",
        ),
        (
            "Wegen eines Schadens am Gleis sind zwischen Wien Hetzendorf Bahnhst und "
            "Wien Atzgersdorf Bahnhst Zugfahrten voraussichtlich bis 13:00 Uhr nur "
            "eingeschränkt möglich.",
            "trains can only run to a limited extent between Wien Hetzendorf "
            "station and Wien Atzgersdorf station until approx. 13:00.",
        ),
        (
            "Wegen einer Stellwerkstörung am Bahnhof waren in Hinterstoder Bahnhof "
            "[in St.Pankraz] bis 16:30 Uhr keine Fahrten möglich.",
            "no trains could run at Hinterstoder station [in St.Pankraz] until 16:30.",
        ),
    ],
)
def test_oebb_template_variants_from_the_feed(
    cause_stub: list[str], german: str, english: str
) -> None:
    """Everything after the cause comes from the slots (the stub knows few causes)."""
    out = build_feed._translate_text_attempt(german, source=_OEBB)
    assert out is not None and out.split(", ", 1)[1] == english
    assert all(text.startswith("Wegen ") for text in cause_stub)


@pytest.mark.parametrize(
    "german",
    [
        (
            "Wegen einer Stellwerkstörung am Bahnhof sind in Wien Hbf (U) bzw Wien "
            "Meidling Zugfahrten erneut nur eingeschränkt möglich."
        ),
        (
            "Wegen eines Schadens am Gleis sind zwischen Wien Meidling Bahnhof (U) und "
            "Liesing (Wien) Zugfahrten voraussichtlich 22:00 Uhr nur eingeschränkt "
            "möglich."
        ),
        (
            "Wegen Bauarbeiten können zwischen Wien Hütteldorf Bahnhof (U) und Wien "
            "Handelskai Bahnhst (U) am 01.11.2026 (von 01:10 Uhr bis 04:10 Uhr) keine "
            "S45-Züge fahren."
        ),
    ],
)
def test_a_variant_without_template_never_lands_in_a_station_slot(
    cause_stub: list[str], german: str
) -> None:
    """Off-template sentences go to the model whole, not German inside an English frame."""
    assert build_feed._render_oebb_sentence(german, "x", _OEBB, None) == ""
    build_feed._translate_text_attempt(german, source=_OEBB)
    # One model call with the whole sentence (masked), not just the cause.
    assert len(cause_stub) == 1
    assert cause_stub[0].endswith(("möglich.", "fahren."))


def test_a_station_name_with_in_der_is_still_a_name(cause_stub: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen eines Rettungseinsatzes waren zwischen Wien Wolf in der Au Bahnhst "
        "und Wien Hadersdorf Bahnhst bis 14:55 Uhr keine Fahrten möglich.",
        source=_OEBB,
    )
    assert out is not None and out.endswith(
        "no trains could run between Wien Wolf in der Au station and Wien "
        "Hadersdorf station until 14:55."
    )


def test_oebb_template_mixed_with_prose(cause_stub: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen einer Weichenstörung sind in St.Pölten Hbf derzeit keine Fahrten "
        "möglich. Wir bitten um Entschuldigung.",
        source=_OEBB,
    )
    assert out == (
        "Due to a switch fault, no trains can run at St.Pölten Hbf at present. "
        "Wir bitten um Entschuldigung."
    )
    assert cause_stub[-1] == "Wir bitten um Entschuldigung."


def test_oebb_templates_are_oebb_only(cause_stub: list[str]) -> None:
    text = (
        "Wegen einer Weichenstörung sind in St.Pölten Hbf derzeit keine Fahrten möglich."
    )
    out = build_feed._translate_text_attempt(text, source=_WL)
    # WL's own "Wegen …" rendering may try first (the stub leaves "Deshalb"
    # German, so it falls back); ÖBB's frame never applies.
    assert out is not None and "no trains can run" not in out
    assert cause_stub[-1].startswith("Wegen ") and "keine Fahrten" in cause_stub[-1]


def test_a_failed_cause_fails_the_text(monkeypatch: Any) -> None:
    def broken(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        raise RuntimeError("model down")

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: broken)
    assert build_feed._translate_text_attempt(
        "Wegen einer Weichenstörung sind in St.Pölten Hbf derzeit keine Fahrten möglich.",
        source=_OEBB,
    ) is None


def test_untemplated_oebb_text_takes_the_ordinary_path(cause_stub: list[str]) -> None:
    text = "Die Fernverkehrszüge werden umgeleitet."
    build_feed._translate_text_attempt(text, source=_OEBB)
    assert cause_stub == [text]


def test_translation_cache_epoch_was_bumped() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 20
