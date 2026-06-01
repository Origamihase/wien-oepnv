"""Regression guards for the EN-feed entity-placeholder leak.

The Helsinki opus-mt-de-en (Marian/SentencePiece) translation model can MANGLE
the opaque masking placeholders it is handed — observed in cached output:
dropping a hex char from the per-process nonce, lower-casing the ``XGLO``
prefix to ``XGLo``, translating German-looking nonce fragments to English
(``…de…`` -> ``…en…``, a stray ``from``), and truncating the trailing index.
Any of these defeats the EXACT-nonce unmask sweep, so the raw sentinel leaked
into ``docs/feed.en.xml`` titles and was then cached and served indefinitely.

These tests pin the three defences:
  * the nonce-agnostic residual detector ``_RESIDUAL_PLACEHOLDER_RE``,
  * the entity-only model bypass (``_is_non_translatable_content``) that keeps
    all-entity titles out of the model entirely,
  * the post-unmask safety net (translation -> ``None`` -> German fallback),
  * the cache self-heal that re-translates a poisoned cache hit.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src import build_feed as bf


# --------------------------------------------------------------------------- #
# Residual-placeholder detector                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "leaked",
    [
        # dropped hex char in the nonce (15 instead of 16)
        "XENT440c384b5ae3ae8X1X/XENT440c384b5ae3ae8X2X: XENT440c384b5ae3ae8X0X",
        # prefix lower-cased by the model
        "stop XGLoe736dbb52a8f8cfX0X here",
        # German-looking nonce fragments translated to English (non-hex letters)
        "ab XENT7473from307en9ecebX1X cd",
        # trailing index truncated
        "Barawitzkagasse XENT451aac69e31fe947X",
    ],
)
def test_residual_detector_catches_every_mangled_form(leaked: str) -> None:
    assert bf._RESIDUAL_PLACEHOLDER_RE.search(leaked) is not None


@pytest.mark.parametrize(
    "clean",
    [
        "86A/87A: Wiedgasse",
        "U6: Karlsplatz",
        # real-ish German words that merely START like the prefixes but lack the
        # disambiguating trailing ``X`` — must NOT trip the detector
        "Xentenplatz und Xentgasse",
        "Stop change of lines 10A and 39A towards Heiligenstadt",
        "ÖBB Wiener Linien VOR Praterstern",
    ],
)
def test_residual_detector_no_false_positive(clean: str) -> None:
    assert bf._RESIDUAL_PLACEHOLDER_RE.search(clean) is None


# --------------------------------------------------------------------------- #
# Entity-only bypass predicate                                                #
# --------------------------------------------------------------------------- #
def _ent(index: int) -> str:
    return bf._ENTITY_PLACEHOLDER_FORMAT.format(index=index)


def _glo(index: int) -> str:
    return bf._GLOSSARY_PLACEHOLDER_FORMAT.format(index=index)


def test_non_translatable_entity_only_title() -> None:
    masked = f"{_ent(1)}/{_ent(2)}: {_ent(0)}"
    assert bf._is_non_translatable_content(masked) is True


def test_non_translatable_false_when_prose_present() -> None:
    masked = f"{_ent(0)}: Aufzug außer Betrieb"
    assert bf._is_non_translatable_content(masked) is False


def test_non_translatable_false_when_glossary_present() -> None:
    # XGLO placeholders stand in for German jargon that STILL needs the model.
    masked = f"{_ent(0)}: {_glo(0)}"
    assert bf._is_non_translatable_content(masked) is False


def test_non_translatable_false_for_blank() -> None:
    assert bf._is_non_translatable_content("   ") is False


# --------------------------------------------------------------------------- #
# _translate_text_attempt: bypass + safety net                                #
# --------------------------------------------------------------------------- #
def test_entity_only_title_bypasses_model(monkeypatch: pytest.MonkeyPatch) -> None:
    nonce = bf._PLACEHOLDER_NONCE
    masked = f"XENT{nonce}X1X/XENT{nonce}X2X: XENT{nonce}X0X"
    mapping = {
        f"XENT{nonce}X0X": "Wiedgasse",
        f"XENT{nonce}X1X": "86A",
        f"XENT{nonce}X2X": "87A",
    }
    monkeypatch.setattr(bf, "_apply_domain_glossary", lambda t, **k: (t, {}))
    monkeypatch.setattr(bf, "_mask_entities", lambda t: (masked, mapping))
    pipe = MagicMock()
    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: pipe)

    result = bf._translate_text_attempt("86A/87A: Wiedgasse", ident="bypass-1")

    assert result == "86A/87A: Wiedgasse"
    pipe.assert_not_called()  # model never invoked for an all-entity title


def test_residual_after_unmask_discards_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = bf._PLACEHOLDER_NONCE
    masked = f"XENT{nonce}X0X Stoerung"
    mapping = {f"XENT{nonce}X0X": "Praterstern"}
    monkeypatch.setattr(bf, "_apply_domain_glossary", lambda t, **k: (t, {}))
    monkeypatch.setattr(bf, "_mask_entities", lambda t: (masked, mapping))
    # The model mangles the placeholder (drops the last nonce char) so the
    # exact-nonce unmask cannot restore it.
    mangled = f"XENT{nonce[:-1]}X0X disruption"
    monkeypatch.setattr(
        bf, "_get_translation_pipeline",
        lambda: (lambda *a, **k: [{"translation_text": mangled}]),
    )

    result = bf._translate_text_attempt("Praterstern Stoerung", ident="resid-1")

    assert result is None  # signalled as failed -> caller falls back to German


def test_clean_translation_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    nonce = bf._PLACEHOLDER_NONCE
    masked = f"XENT{nonce}X0X Stoerung"
    mapping = {f"XENT{nonce}X0X": "Praterstern"}
    monkeypatch.setattr(bf, "_apply_domain_glossary", lambda t, **k: (t, {}))
    monkeypatch.setattr(bf, "_mask_entities", lambda t: (masked, mapping))
    # Model echoes the placeholder intact -> unmask restores it cleanly.
    good = f"XENT{nonce}X0X disruption"
    monkeypatch.setattr(
        bf, "_get_translation_pipeline",
        lambda: (lambda *a, **k: [{"translation_text": good}]),
    )

    result = bf._translate_text_attempt("Praterstern Stoerung", ident="ok-1")

    assert result == "Praterstern disruption"


# --------------------------------------------------------------------------- #
# _cached_translation: self-heal vs. clean hit                                #
# --------------------------------------------------------------------------- #
def test_cache_self_heals_poisoned_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    poisoned = "XENT440c384b5ae3ae8X1X: XENT440c384b5ae3ae8X0X"
    state: dict[str, dict[str, Any]] = {
        "poison-id": {"translations": {"en": {"title": poisoned}}}
    }
    calls: list[str] = []

    def fake_attempt(text: str, ident: str = "", **_: Any) -> str:
        calls.append(text)
        return "U1: Stephansplatz"

    monkeypatch.setattr(bf, "_translate_text_attempt", fake_attempt)

    out, ok = bf._cached_translation("U1: Stephansplatz", "title", "poison-id", state)

    assert ok is True
    assert out == "U1: Stephansplatz"
    assert calls == ["U1: Stephansplatz"]  # re-translated, not served from cache
    # poisoned value overwritten with the clean re-translation
    assert state["poison-id"]["translations"]["en"]["title"] == "U1: Stephansplatz"


def test_clean_cache_hit_served_without_retranslation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cached EN must DIFFER from the DE source, otherwise the pre-existing
    # "Sticky-German" guard (cached == source -> stale -> retry) fires first.
    state: dict[str, dict[str, Any]] = {
        "clean-id": {"translations": {"en": {"title": "U6: Elevator out of order"}}}
    }

    def boom(*_a: Any, **_k: Any) -> str:
        raise AssertionError("a clean cache hit must not be re-translated")

    monkeypatch.setattr(bf, "_translate_text_attempt", boom)

    out, ok = bf._cached_translation("U6: Aufzug außer Betrieb", "title", "clean-id", state)

    assert (out, ok) == ("U6: Elevator out of order", True)
