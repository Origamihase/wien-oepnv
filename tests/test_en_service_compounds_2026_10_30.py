"""EN for ``<Anlass>verkehr`` compounds of WL and ÖBB (Jules-Audit 2026-10-30).

The real model rendered "38B/39B: Allerheiligenverkehr 01.11.2026" as
"38B/39B: All Saints' Worship 01.11.2026". The compound is now rendered from
its parts: the occasion through the model alone, the head as "service". The
model here is a stub that knows a few occasions and echoes everything else,
so the assertions check what the rule writes and what the model is shown.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed

_WL = "Wiener Linien"
_OEBB = "ÖBB"
_BST = "Stadt Wien – Baustellen"

_OCCASIONS = {
    "Allerheiligen": "All Saints",
    "Feiertag": "Public holiday",
    "Silvester": "New Year's Eve",
    "Fronleichnam": "Corpus Christi",
    "Donauinselfest": "Danube Island Festival",
}


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """A stub model that knows the occasions above, recording its inputs."""
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": _OCCASIONS.get(text, text)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: pipe)
    return seen


@pytest.mark.parametrize(
    ("german", "english"),
    [
        (
            "38B/39B: Allerheiligenverkehr 01.11.2026",
            "38B/39B: All Saints service 01.11.2026",
        ),
        ("Feiertagsverkehr", "Public holiday service"),
        ("Fronleichnamsverkehr", "Corpus Christi service"),
        ("Silvesterverkehr", "New Year's Eve service"),
        ("Donauinselfest-Verkehr", "Danube Island Festival service"),
        # Inflected: the head keeps its English, the ending goes.
        ("des Allerheiligenverkehrs", "des All Saints service"),
    ],
)
def test_occasion_compound_rendered_from_its_parts(
    model: list[str], german: str, english: str
) -> None:
    assert build_feed._translate_text_attempt(german, source=_WL) == english
    # The model never sees the compound, only the occasion alone.
    assert not any("verkehr" in shown.casefold() for shown in model)


def test_oebb_text_takes_the_rule_too(model: list[str]) -> None:
    assert (
        build_feed._translate_text_attempt("Allerheiligenverkehr", source=_OEBB)
        == "All Saints service"
    )


@pytest.mark.parametrize(
    "german",
    [
        # Road traffic: "Verkehr" is traffic there.
        "Die Busse wenden im Kreisverkehr.",
        "Die Ringstraße wird für den Gesamtverkehr gesperrt.",
        "Fahrspur des Individualverkehrs",
        # Modes, areas and a time of day the model misreads alone.
        "Zwischen A und B wurde ein Pendelbusverkehr eingerichtet.",
        "dem verstärkten Straßenbahnverkehr am Gürtel",
        "Für die S-Bahnen und den Regionalverkehr",
        "Reservezug im Morgenverkehr",
    ],
)
def test_traffic_mode_and_area_compounds_keep_the_model(
    model: list[str], german: str
) -> None:
    build_feed._translate_text_attempt(german, source=_WL)
    # The compound went to the model inside its sentence, as before.
    assert len(model) == 1
    assert "verkehr" in model[0].casefold()


def test_inflected_glossary_compound_takes_the_glossary(model: list[str]) -> None:
    english = build_feed._translate_text_attempt(
        "Die Busse des Ersatzverkehrs halten in der Gasse.", source=_WL
    )
    assert english is not None
    assert "replacement service" in english
    assert "Ersatzverkehrs" not in english


def test_baustellen_texts_keep_the_model(model: list[str]) -> None:
    build_feed._translate_text_attempt("Allerheiligenverkehr", source=_BST)
    assert model == ["Allerheiligenverkehr"]


def test_occasion_with_german_letters_left_keeps_the_model(
    monkeypatch: Any,
) -> None:
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: pipe)
    build_feed._translate_text_attempt("Mariä-Empfängnis-Verkehr", source=_WL)
    # The occasion came back German, so the compound went to the model whole.
    assert seen[-1] == "Mariä-Empfängnis-Verkehr"


def test_cached_value_without_the_fixed_english_is_translated_again(
    model: list[str],
) -> None:
    german = "38B/39B: Allerheiligenverkehr 01.11.2026"
    state: dict[str, dict[str, Any]] = {
        "id": {
            "translations": {
                "en": {"title": "38B/39B: All Saints' Worship 01.11.2026"},
            }
        }
    }
    english, ok = build_feed._cached_translation(
        german, "title", "id", state, source=_WL, category="Hinweis"
    )
    assert ok
    assert english == "38B/39B: All Saints service 01.11.2026"
    # The new value is stable: the next build serves it from the cache.
    model.clear()
    again, ok = build_feed._cached_translation(
        german, "title", "id", state, source=_WL, category="Hinweis"
    )
    assert ok
    assert again == english
    assert model == []
