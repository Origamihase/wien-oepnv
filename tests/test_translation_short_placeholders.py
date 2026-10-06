"""The model sees short placeholders, ``XENT3X`` (EN fallback audit 2026-10-06).

Under the per-build nonce a placeholder is some ten SentencePiece pieces of
random hex, and opus-mt-de-en copied that badly: with the real model in CI, 227
of 972 passes over the feed's 243 distinct model inputs since 2026-09-20 lost
or mangled a placeholder (23 %), and the short form lost none. A text that
failed both passes stood in German in the EN feed for a tick, 66A/N66 at 17:23
on 2026-10-06 among them.

Only the model's copy is short. Masks, mapping and unmask keep the nonce, and
a source text that already carries the short shape is shown under the nonce,
so a planted ``XENT0X`` can never be mapped back as an entity.

Mutations checked against this file (each one caught, by the test named):

* the first pass under the build's nonce → ``test_the_first_pass_shows_short_placeholders``.
* no mapping back → ``test_the_short_placeholders_come_back_as_entities``.
* no collision check → ``test_a_planted_short_placeholder_is_not_an_entity``.
* the check misses a lower-case plant → ``test_the_collision_check_ignores_case``.
* a mangled short placeholder passes → ``test_a_mangled_short_placeholder_gets_a_second_pass``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

import src.build_feed as build_feed

_NONCE = re.compile(r"(?:XENT|XGLO)[0-9a-f]{16}X\d+X")

# 66A/N66, the text that fell back to German at 17:23 on 2026-10-06.
SECOND_SENTENCE = (
    "Haltestellenverlegung der Linien 66A in Richtung Reumannplatz U bzw. N66 …"
)


def _install(monkeypatch: pytest.MonkeyPatch, model: Callable[[str], str]) -> list[str]:
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": model(text)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


def _english(text: str) -> str:
    """A model that copies every placeholder and translates the rest."""
    return (
        text.replace("der Linien", "of lines")
        .replace("in Richtung", "towards")
        .replace("bzw.", "or")
    )


def test_the_first_pass_shows_short_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _english)
    build_feed._translate_text_attempt(SECOND_SENTENCE, ident="66A")
    assert len(calls) == 1
    assert not _NONCE.search(calls[0]), calls[0]
    assert re.search(r"XENT\dX", calls[0]) and re.search(r"XGLO\dX", calls[0])


def test_the_short_placeholders_come_back_as_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _english)
    out = build_feed._translate_text_attempt(SECOND_SENTENCE, ident="66A")
    assert out == "stop relocation of lines 66A towards Reumannplatz U or N66 …"


def test_a_planted_short_placeholder_is_not_an_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    # A source text that carries the short shape itself: shown under the
    # build's nonce, so the plant stays text and maps to nothing.
    calls = _install(monkeypatch, _english)
    out = build_feed._translate_text_attempt(
        "Haltestellenverlegung der Linien 66A XENT0X in Richtung Reumannplatz U",
        ident="plant",
    )
    assert build_feed._PLACEHOLDER_NONCE in calls[0]
    assert all("XENT0X" in call for call in calls)
    # The plant is no placeholder of ours, so it never unmasks to "66A"; it
    # trips the residual guard in both passes, as before the short form, and
    # the item falls back to German.
    assert out is None


def test_the_collision_check_ignores_case() -> None:
    nonce = build_feed._PLACEHOLDER_NONCE
    assert build_feed._has_short_placeholder_shape(f"XENT{nonce}X0X und xglo12x")
    assert not build_feed._has_short_placeholder_shape(
        f"XENT{nonce}X0X und XGLO{nonce}X12X in Richtung Xentenplatz"
    )


def test_a_mangled_short_placeholder_gets_a_second_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def _model(text: str) -> str:
        if not _NONCE.search(text):
            return _english(text).replace("XENT1X", "XEN1X")
        return _english(text)

    calls = _install(monkeypatch, _model)
    out = build_feed._translate_text_attempt(SECOND_SENTENCE, ident="66A")
    assert len(calls) == 2
    assert _NONCE.search(calls[1])
    assert out == "stop relocation of lines 66A towards Reumannplatz U or N66 …"


def test_round_trip_of_the_debris_shapes() -> None:
    """The model's known debris survives the mapping back and is repaired."""
    nonce = build_feed._PLACEHOLDER_NONCE
    back = build_feed._from_short_placeholders("lines 14A XENT4XX and XENT0X0X, fromXXENT2X")
    assert build_feed._normalise_placeholder_debris(back) == (
        f"lines 14A XENT{nonce}X4X and XENT{nonce}X0X, from XENT{nonce}X2X"
    )
    assert build_feed._to_short_placeholders(f"XGLO{nonce}X0X XENT{nonce}X12X") == "XGLO0X XENT12X"


# ---------------- "in Richtung … umgeleitet" ----------------


def test_a_diversion_keeps_its_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    # The model's real output for 11A in the CI probe of 2026-10-06.
    def _model(text: str) -> str:
        line, stop, a, _stop, street = re.findall(r"XENT\d+X", text)
        return f"Line {line} is redirected to {stop} U between {a} and {stop} U via {street}."

    _install(monkeypatch, _model)
    out = build_feed._translate_text_attempt(
        "Die Linie 11A wird in Richtung Stadion U zwischen Elderschplatz und Stadion U "
        "über die Vorgartenstraße umgeleitet.",
        ident="11A",
    )
    assert out is not None
    assert "redirected towards Stadion U between" in out, out


def test_only_a_german_direction_moves_the_preposition() -> None:
    masked = "Wegen XGLO0X wird die Linie XENT0X umgeleitet."
    english = "Because of roadworks, line 15A is redirected to the main road."
    assert build_feed._redirected_towards(masked, english) == english
    masked = "Die Linie XENT0X wird in Richtung XENT1X umgeleitet."
    assert build_feed._redirected_towards(masked, "Line 5 is diverted to Ring.") == (
        "Line 5 is diverted towards Ring."
    )
    assert build_feed._redirected_towards(masked, "Line 5 is redirected towards Ring.") == (
        "Line 5 is redirected towards Ring."
    )
