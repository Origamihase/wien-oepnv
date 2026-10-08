"""A cause reads "Due to …" everywhere in the EN feed (2026-10-08).

Operator decision of 2026-10-05: "Due to", always. The model renders
"Wegen" as "Because of" as often as "Due to"; the EN feed opened 72 of its
309 distinct cause sentences since July with "Because of" (63A "Because of
crane works …", 29B "Because of roadworks in the Giefinggasse …"), and the
ÖBB and WL cause renderers took either. The synonyms of "due to" now become
"due to" after every model pass and on every cache hit.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed


def _install(monkeypatch: Any, replies: dict[str, str]) -> list[str]:
    """A model that answers by the start of its input; echoes otherwise."""
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        for german, english in replies.items():
            if text.startswith(german):
                return [{"translation_text": english + text[len(german):]}]
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: pipe)
    return seen


@pytest.mark.parametrize(
    ("english", "expected"),
    [
        (
            "Because of crane works in the area of Kerschensteinergasse, line 63A is redirected.",
            "Due to crane works in the area of Kerschensteinergasse, line 63A is redirected.",
        ),
        (
            "The bus lines are being redirected because of roadworks.",
            "The bus lines are being redirected due to roadworks.",
        ),
        ("46: service obstruction because of police operation", "46: service obstruction due to police operation"),
        ("Owing to a demonstration, trams stop.", "Due to a demonstration, trams stop."),
        ("On account of a fault, no service.", "Due to a fault, no service."),
        ("Closed as a result of the storm.", "Closed due to the storm."),
        # No cause preposition: left as the model wrote it.
        ("Due to an event, the following transport measures are taken.",
         "Due to an event, the following transport measures are taken."),
        ("As a result, line 5 is diverted.", "As a result, line 5 is diverted."),
        ("Because the platform is closed, line 5 is diverted.",
         "Because the platform is closed, line 5 is diverted."),
    ],
)
def test_every_synonym_of_due_to_becomes_due_to(english: str, expected: str) -> None:
    assert build_feed._cause_due_to(english) == expected


def test_a_model_pass_says_due_to(monkeypatch: Any) -> None:
    _install(monkeypatch, {"Wegen ": "Because of "})
    out = build_feed._translate_text_attempt(
        "Wegen Kranarbeiten im Bereich Kerschensteinergasse fährt der Bus.",
    )
    assert out is not None
    assert out.startswith("Due to "), out


def test_the_wl_cause_sentence_says_due_to(monkeypatch: Any) -> None:
    _install(monkeypatch, {"Deshalb wird die Linie": "Therefore, line", "Wegen ": "Because of "})
    out = build_feed._translate_text_attempt(
        "Wegen Bauarbeiten in der Giefinggasse wird die Linie 29B umgeleitet.",
        source="Wiener Linien",
    )
    assert out is not None
    assert out.startswith("Due to "), out
    assert "because of" not in out.casefold()


def test_the_oebb_cause_sentence_says_due_to(monkeypatch: Any) -> None:
    _install(monkeypatch, {"Wegen eines Polizeieinsatzes": "Because of a police operation"})
    out = build_feed._translate_text_attempt(
        "Wegen eines Polizeieinsatzes sind zwischen Wien Floridsdorf Bahnhof und "
        "Wien Jedlersdorf Bahnhof keine Fahrten möglich.",
        source="ÖBB",
    )
    assert out == (
        "Due to a police operation, no trains can run between Wien Floridsdorf station "
        "and Wien Jedlersdorf station."
    )


def test_a_cached_because_of_is_served_as_due_to(monkeypatch: Any) -> None:
    calls = _install(monkeypatch, {})
    german = "Wegen Bauarbeiten in der Giefinggasse wird die Linie 29B umgeleitet."
    cached = "Because of roadworks in the Giefinggasse, the 29B buses are redirected."
    translations: dict[str, Any] = {"en": {"summary": cached}}
    build_feed._record_source_digest(translations, "summary", german)
    state: dict[str, dict[str, Any]] = {"wl|29B": {"translations": translations}}
    out, ok = build_feed._cached_translation(
        german, "summary", "wl|29B", state, source="Wiener Linien"
    )
    assert ok
    assert out == "Due to roadworks in the Giefinggasse, the 29B buses are redirected."
    # Served from the cache: no model run, no re-translation.
    assert calls == []
