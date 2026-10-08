"""WL's "Wegen <Ursache> <Verb> …" sentences in the EN feed (2026-10-05).

The model lost the sense on them: "Construction works in the area of
Wildbadgasse is redirected to line 20A." and, after the German text of the
U6 notice changed, "The U6 station stops at Neue Donau U only in the
direction of Siebenhirten U." with the cause dropped. The cause and the main
clause now go through the model apart, the main clause opened with
"Deshalb" so its verb stays in second place.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed

_WL = "Wiener Linien"


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """A model that knows the phrases these sentences need, word for word."""
    seen: list[str] = []
    phrases = {
        "Deshalb wird die Linie": "Therefore, line",
        "Deshalb hält die Linie": "Therefore, line",
        "Deshalb kommt es zu Verkehrseinschränkungen.": "Therefore, there are traffic restrictions.",
        "Deshalb muss": "That is why",
        "Wegen Sanierung des Bahnsteigs": "Due to the renovation of the platform",
        "Wegen des": "Due to the",
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


def test_the_cause_and_the_main_clause_go_through_the_model_apart(model: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen Bauarbeiten im Bereich Wildbadgasse wird die Linie 20A umgeleitet.",
        source=_WL,
    )
    assert out is not None
    assert out.startswith("Due to construction works in the area of Wildbadgasse, line 20A")
    assert any(text.startswith("Deshalb wird die Linie") for text in model)


def test_the_u6_notice_keeps_its_cause(model: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen Sanierung des Bahnsteigs hält die Linie U6 die Station Neue Donau U nur in "
        "Richtung Siebenhirten U ein. Sie erreichen die Station Neue Donau mit der U6 von "
        "Floridsdorf.",
        source=_WL,
    )
    assert out is not None
    assert out.startswith("Due to the renovation of the platform, line U6")
    # The second sentence takes the ordinary path on its own.
    assert model[-1].startswith("Sie erreichen")


def test_a_name_opening_the_main_clause_keeps_its_capital(model: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wegen des Erste Bank Vienna Night Run 2026 kommt es zu Verkehrseinschränkungen.",
        source=_WL,
    )
    assert out == "Due to the Erste Bank Vienna Night Run 2026, there are traffic restrictions."


def test_a_main_clause_the_model_opens_otherwise_takes_the_ordinary_path(
    model: list[str],
) -> None:
    # The stub leaves "Deshalb ist …" German: no "Therefore", so the whole
    # sentence goes through the model as before.
    text = "Wegen Bauarbeiten ist die Station Keplerplatz geschlossen."
    build_feed._translate_text_attempt(text, source=_WL)
    assert model[-1].startswith("Wegen ") and model[-1].endswith(" geschlossen.")


def test_a_cause_with_a_comma_takes_the_ordinary_path(model: list[str]) -> None:
    text = "Wegen der Arbeiten, die länger dauern, wird die Linie 5A umgeleitet."
    assert build_feed._render_wl_because_sentence(text, "x", _WL, None) == ""


def test_only_wiener_linien(model: list[str]) -> None:
    text = "Wegen Bauarbeiten im Bereich Wildbadgasse wird die Linie 20A umgeleitet."
    build_feed._translate_text_attempt(text, source="Stadt Wien – Baustellen")
    assert model == [text] or all(not m.startswith("Deshalb") for m in model)


def test_a_failed_main_clause_fails_the_text(monkeypatch: Any) -> None:
    def broken(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        raise RuntimeError("model down")

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: broken)
    assert build_feed._translate_text_attempt(
        "Wegen Bauarbeiten im Bereich Wildbadgasse wird die Linie 20A umgeleitet.",
        source=_WL,
    ) is None


def test_translation_cache_epoch_was_bumped() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 23


# First real run after the merge (05.10.2026 17:45): two more gaps.


def test_a_cause_the_model_renders_as_a_bare_noun_gets_due_to(monkeypatch: Any) -> None:
    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        if text.startswith("Deshalb hält die Linie"):
            return [{"translation_text": "Therefore, line" + text[len("Deshalb hält die Linie"):]}]
        if text.startswith("Wegen "):
            return [{"translation_text": "Renovation of the platform"}]
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: pipe)
    out = build_feed._translate_text_attempt(
        "Wegen Sanierung des Bahnsteigs hält die Linie U6 die Station Neue Donau U nur in "
        "Richtung Siebenhirten U ein.",
        source=_WL,
    )
    assert out is not None and out.startswith("Due to renovation of the platform, line U6")


def test_a_name_in_a_bare_cause_keeps_its_capital(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        build_feed,
        "_get_translation_pipeline",
        lambda: lambda text, **_k: [{"translation_text": "Vienna City Marathon"}],
    )
    assert build_feed._oebb_cause_en("des Vienna City Marathons", "x", _WL, None) == (
        "Due to Vienna City Marathon"
    )


def test_a_relocation_way_never_reaches_the_model(model: list[str]) -> None:
    out = build_feed._translate_text_attempt(
        "Wienerbergstraße 27b-27c → Wienerbergstraße 27a. Haltestellenverlegung der Linien 7A "
        "in Richtung Reumannplatz U. Hartäckerstraße 65 → etwa 50 Meter in Richtung "
        "Borkowskigasse.",
        source=_WL,
    )
    assert out is not None
    assert out.startswith("Wienerbergstraße 27b-27c → Wienerbergstraße 27a. ")
    assert out.endswith("Hartäckerstraße 65 → approx. 50 metres towards Borkowskigasse.")
    assert not any("→" in text or "Wienerbergstraße" in text for text in model)


@pytest.mark.parametrize(
    "way",
    [
        "1. Haidequerstraße 2 → 1. Haidequerstraße 510.",
        "Knotzenbachgasse ggü. 40 vor Parkanlage → Knotzenbachgasse 35-45, vor Dirmhirngasse.",
    ],
)
def test_a_number_or_abbreviation_does_not_end_the_way(model: list[str], way: str) -> None:
    sentences = build_feed._WL_SENTENCE_SPLIT_RE.split(f"{way} Grund: Bauarbeiten.")
    assert sentences == [way, "Grund: Bauarbeiten."]
