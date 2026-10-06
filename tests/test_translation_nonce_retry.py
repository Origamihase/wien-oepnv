"""A second model pass under a fresh placeholder nonce (audit 2026-09-25, A.5).

Since 2026-10-06 the first pass shows the model the short placeholders
(``XENT3X``, see ``test_translation_short_placeholders.py``); the second one
still goes under a fresh nonce. The tests below mangle the first pass.

Whether Marian mangles a placeholder depends on the nonce's SentencePiece
split, and a bad nonce hits several texts of one build. Live 2026-09-26
17:01, nonce ``c3ed7873665b7570``: three summaries left a residual
placeholder, among them WL's stock sentence, which other builds had
translated for other lines. They stood in German in the EN feed until a later
build drew another nonce.

``_translate_text_attempt`` now tries once more under a fresh nonce when the
model mangles or drops a placeholder. Only the model sees the fresh nonce;
masks and mapping keep the build's, and the model's intact placeholders are
mapped back before anything is checked or unmasked.

Mutations checked against this file (each one caught, by the test named):

* no second pass → ``test_a_bad_nonce_gets_a_second_pass``.
* the second pass reuses the build's nonce → ``test_a_bad_nonce_gets_a_second_pass``.
* the fresh placeholders are not mapped back → ``test_a_bad_nonce_gets_a_second_pass``.
* a dropped entity is not retried → ``test_a_dropped_entity_gets_a_second_pass``.
* a pipeline error is retried → ``test_a_pipeline_error_is_not_retried``.

Running the debris repair before the nonce is mapped back is equivalent:
``_unmask_entities`` repairs the debris once more.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

import src.build_feed as build_feed

_PLACEHOLDER_NONCE = build_feed._PLACEHOLDER_NONCE

# 60A on 2026-09-26 17:01, one of the three summaries that failed.
SUMMARY = "Unregelmäßige Intervalle in beiden Richtungen. Grund: Rettungseinsatz."
# A line and a stop: entity placeholders that must come back verbatim.
WITH_ENTITIES = "Züge halten bei Linie 18 in Richtung Burggasse"

_PLACEHOLDER = re.compile(r"(?:XENT|XGLO)([0-9a-f]{16})X\d+X")

Model = Callable[[str], str]


def _nonces(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def _install(monkeypatch: pytest.MonkeyPatch, model: Model) -> list[str]:
    """Replace the pipeline with *model*; return the texts it was given."""
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": model(text)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


def _mangles_the_first_pass(text: str) -> str:
    """The model on a bad first pass: it lower-cases the short placeholders' prefix."""
    if _nonces(text):
        return "EN " + text
    return "EN " + text.replace("XGLO", "XGLo")


def test_a_bad_nonce_gets_a_second_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _mangles_the_first_pass)
    out = build_feed._translate_text_attempt(SUMMARY, ident="60A")
    assert len(calls) == 2
    assert _nonces(calls[0]) == set()
    (fresh,) = _nonces(calls[1])
    assert fresh != _PLACEHOLDER_NONCE
    assert out is not None
    assert "Reason:" in out
    assert "XGLO" not in out.upper()


def test_a_good_nonce_needs_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda text: "EN " + text)
    out = build_feed._translate_text_attempt(SUMMARY, ident="60A")
    assert len(calls) == 1
    assert out is not None and "Reason:" in out


def test_two_bad_passes_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A model that mangles every nonce: two passes, then the German source.
    calls = _install(monkeypatch, lambda text: "EN " + text.replace("XGLO", "XGLo"))
    assert build_feed._translate_text_attempt(SUMMARY, ident="60A") is None
    assert len(calls) == 2


def test_a_dropped_entity_gets_a_second_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def _drops_in_the_first_pass(text: str) -> str:
        if not _nonces(text):
            return "EN " + re.sub(r"XENT0X", "", text)
        return "EN " + text

    calls = _install(monkeypatch, _drops_in_the_first_pass)
    out = build_feed._translate_text_attempt(WITH_ENTITIES, ident="62")
    assert len(calls) == 2
    assert out is not None
    assert "18" in out and "Burggasse" in out


def test_a_pipeline_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        raise RuntimeError("model crashed")

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    assert build_feed._translate_text_attempt(SUMMARY, ident="60A") is None
    assert len(calls) == 1


def test_a_doubled_closing_x_under_the_fresh_nonce_is_repaired(monkeypatch: pytest.MonkeyPatch) -> None:
    # The 14AX leak of 2026-09-18, this time on the second pass.
    def _model(text: str) -> str:
        if not _nonces(text):
            return "EN " + text.replace("XENT", "XENt")
        return "EN " + re.sub(r"(XENT[0-9a-f]{16}X\d+X)", r"\1X", text)

    calls = _install(monkeypatch, _model)
    out = build_feed._translate_text_attempt(WITH_ENTITIES, ident="62")
    assert len(calls) == 2
    assert out is not None
    assert "18X" not in out and "BurggasseX" not in out
    assert "18" in out and "Burggasse" in out
