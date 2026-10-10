"""No placeholder leftover reaches the EN feed, whichever pass mangles it (2026-10-10).

The first model pass shows the model short placeholders (``XGLO0X``), the
second one a fresh nonce (``XGLO<16 hex>X0X``). Before 2026-10-10 a pass was
judged by ``_RESIDUAL_PLACEHOLDER_RE`` alone. A placeholder whose prefix the
model dropped left a fragment no shape of it matched, and a glossary
placeholder (``XGLO``) is not counted by ``_entities_dropped_by_translation``
either, so the leftover passed: "Nord8d74459316abX5X" (EN check 2026-10-08),
just as well "NordGLO0X", "XGLO0" or "Nord8d74459316ab". The cache refused
such a value only on the next build.

Since then a pass is judged by the rule the cache applies
(``_leftover_placeholder``), with two more debris shapes: the prefix letters
glued to the index, and a nonce fragment. The tests below mangle one
placeholder at a time in every way that leaves a trace of it, in either pass,
and require one of two outcomes: the translation a clean model gives, or
``None`` (the caller's fallback to the German source). A leftover in between
fails.

Mutations checked against this file (each one caught):

* the pass ignores ``_placeholder_debris`` → the hex and glued-prefix cases.
* without the ``ent|glo`` debris shape → ``NordGLO0``, ``XGLO0``.
* without the hex shape → ``Nord<nonce>``.
* an invented placeholder index is unmasked as before →
  ``test_an_invented_placeholder_fails_the_pass``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

import src.build_feed as build_feed

# Glossary placeholders only: the case no other check sees.
SUMMARY = "Unregelmäßige Intervalle in beiden Richtungen. Grund: Rettungseinsatz."
# A glossary term, a line and a stop.
WITH_ENTITIES = "Züge halten bei Linie 18 in Richtung Burggasse"
# A placeholder for a glyph that carries no word character.
WITH_GLYPH = "Zugausfall zwischen Wien Meidling ↔ Wien Floridsdorf wegen Bauarbeiten"

TEXTS = (SUMMARY, WITH_ENTITIES, WITH_GLYPH)

_NONCE_FORM = re.compile(r"(XENT|XGLO)([0-9a-f]{16})X(\d+)X")
_SHORT_FORM = re.compile(r"(XENT|XGLO)(\d+)X")

Mangle = Callable[[re.Match[str]], str]

# What the model makes of one short placeholder (groups: prefix, index).
SHORT_MANGLES: dict[str, Mangle] = {
    "opening X lost, glued": lambda m: "Nord" + m.group(1)[1:] + m.group(2) + "X",
    "opening X lost": lambda m: m.group(1)[1:] + m.group(2) + "X",
    "closing X lost": lambda m: m.group(1) + m.group(2),
    "both X lost, glued": lambda m: "Nord" + m.group(1)[1:] + m.group(2),
    "prefix lower-cased": lambda m: m.group(1).lower() + m.group(2) + "X",
    "prefix partly lower-cased": lambda m: m.group(1)[:3] + m.group(1)[3].lower() + m.group(2) + "X",
}

# What the model makes of one nonce placeholder (groups: prefix, nonce, index).
NONCE_MANGLES: dict[str, Mangle] = {
    "prefix lost, nonce cut, glued": lambda m: "Nord" + m.group(2)[:12] + "X" + m.group(3) + "X",
    "prefix and index lost, glued": lambda m: "Nord" + m.group(2)[:12],
    "prefix and index lost": lambda m: m.group(2),
    "nonce cut to six": lambda m: m.group(2)[-6:] + "X" + m.group(3) + "X",
    "opening X lost, glued": lambda m: "Nord" + m.group(1)[1:] + m.group(2) + "X" + m.group(3) + "X",
    "closing X lost": lambda m: m.group(1) + m.group(2) + "X" + m.group(3),
    "index lost": lambda m: m.group(1) + m.group(2) + "X",
    "separator lost": lambda m: m.group(1) + m.group(2) + m.group(3) + "X",
    "prefix lower-cased": lambda m: m.group(1).lower() + m.group(2) + "X" + m.group(3) + "X",
    "nonce char dropped": lambda m: m.group(1) + m.group(2)[1:] + "X" + m.group(3) + "X",
}


def _install(monkeypatch: pytest.MonkeyPatch, model: Callable[[str], str]) -> list[str]:
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": model(text)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


def _mangle_nth(pattern: re.Pattern[str], text: str, n: int, mangle: Mangle) -> str:
    count = -1

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return mangle(m) if count == n else m.group(0)

    return pattern.sub(_sub, text)


def _clean(monkeypatch: pytest.MonkeyPatch, text: str) -> tuple[str, int]:
    """The translation an echoing model gives, and how many placeholders it saw."""
    calls = _install(monkeypatch, lambda shown: "EN " + shown)
    out = build_feed._translate_text_attempt(text, ident="probe")
    assert out is not None and len(calls) == 1
    return out, len(_SHORT_FORM.findall(calls[0]))


def _cases() -> list[tuple[str, str]]:
    return [(text, name) for text in TEXTS for name in SHORT_MANGLES]


@pytest.mark.parametrize(("text", "name"), _cases())
def test_a_mangled_short_placeholder_never_leaks(
    monkeypatch: pytest.MonkeyPatch, text: str, name: str
) -> None:
    clean, count = _clean(monkeypatch, text)
    assert count, "the text must show the model a placeholder"
    for n in range(count):
        def _model(shown: str, n: int = n) -> str:
            if _NONCE_FORM.search(shown):
                return "EN " + shown  # the second pass comes back intact
            return "EN " + _mangle_nth(_SHORT_FORM, shown, n, SHORT_MANGLES[name])

        calls = _install(monkeypatch, _model)
        out = build_feed._translate_text_attempt(text, ident="probe")
        assert out == clean, (name, n, out)
        assert len(calls) == 2, (name, n)


@pytest.mark.parametrize(("text", "name"), [(t, n) for t in TEXTS for n in NONCE_MANGLES])
def test_a_mangled_nonce_placeholder_never_leaks(
    monkeypatch: pytest.MonkeyPatch, text: str, name: str
) -> None:
    _clean_out, count = _clean(monkeypatch, text)
    for n in range(count):
        def _model(shown: str, n: int = n) -> str:
            if _NONCE_FORM.search(shown):
                return "EN " + _mangle_nth(_NONCE_FORM, shown, n, NONCE_MANGLES[name])
            return "EN " + shown.replace("XGLO", "XGLo").replace("XENT", "XENt")

        calls = _install(monkeypatch, _model)
        out = build_feed._translate_text_attempt(text, ident="probe")
        assert out is None, (name, n, out)
        assert len(calls) == 2, (name, n)


def test_an_invented_placeholder_fails_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    # The model shifts an index: the unmask would delete the unknown
    # placeholder and the word with it.
    def _model(shown: str) -> str:
        shifted = _NONCE_FORM.sub(lambda m: f"{m.group(1)}{m.group(2)}X9{m.group(3)}X", shown)
        return "EN " + _SHORT_FORM.sub(lambda m: m.group(1) + "9" + m.group(2) + "X", shifted)

    calls = _install(monkeypatch, _model)
    assert build_feed._translate_text_attempt(SUMMARY, ident="probe") is None
    assert len(calls) == 2


def test_the_reported_shape_is_refused_from_the_cache() -> None:
    # A value an earlier build cached with the leftover is not served again.
    source = "Wegen Bauarbeiten im Bereich Nord kommt es zu Verspätungen."
    for cached in (
        "Due to construction work in the area Nord8d74459316abX5X there are delays.",
        "Due to construction work in the area Nord8d74459316ab there are delays.",
        "Due to construction work in the area NordGLO5X there are delays.",
        "Due to construction work in the area XGLO5 there are delays.",
    ):
        assert build_feed._cached_translation_defect(source, cached) == "residual placeholder", cached


def test_ordinary_english_is_no_leftover() -> None:
    # Words that end in "ent"/"glo" or are spelt in hex letters, and upper-case
    # line codes, next to a digit only with a space or in the German too.
    source = "Linie 44A: Unfall, Segment 2, Fahrzeugschaden 1a2b3c"
    for english in (
        "Line 44A: accident, segment 2, decade 2026, vehicle damage 1a2b3c",
        "43A/44A/N43: track construction works (phase 2)",
        "Facade work at Glockengasse 12, see agent 7",
    ):
        assert not build_feed._leftover_placeholder(source, english), english
