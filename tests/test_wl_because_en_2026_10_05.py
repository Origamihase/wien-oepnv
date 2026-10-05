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
    text = "Wegen Bauarbeiten ist die Station Keplerplatz gesperrt."
    build_feed._translate_text_attempt(text, source=_WL)
    assert model[-1].startswith("Wegen ") and model[-1].endswith(" gesperrt.")


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
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 22
