"""ÖBB's section labels read the same in English every time.

ÖBB heads the sections of a long notice in capitals ("TEILAUSFALL:"); the
German feed writes them "Teilausfall:" (``_oebb_section_labels_cased``,
2026-10-17). The model rendered that label "Partial failure:" (RJ 263,
18.-19.10.2026). The labels now have fixed English names
(``_OEBB_SECTION_LABELS_EN``); the model still reads the whole sentence, and
only its words in front of the colon are replaced.

Mutations checked against this file (each one caught, by the test named):

* the model's label is kept → ``test_the_model_label_is_replaced``.
* a colon deep in the model's answer is taken for the label's → ``test_a_model_answer_without_its_label_falls_back_to_the_body``.
* "bzw." ends a sentence again → ``test_bzw_does_not_end_the_labelled_sentence``.
* the second part of a joined label is not looked up → ``test_joined_labels_name_each_part``.
* a capitalised word before a colon that is no label is taken as one → ``test_other_heads_take_the_ordinary_path``.
* a templated body is no longer rendered → ``test_a_templated_body_keeps_its_template``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed as bf

_OEBB = "ÖBB"

# Every capitals label of the ÖBB texts in the cache history (926 notices,
# 2025-09 to 2026-10), as the German feed writes it.
_REAL_LABELS = {
    "Achtung": "Please note",
    "Ausfall": "Cancellation",
    "Ersatzbeförderung": "Replacement service",
    "Geänderte Fahrzeiten": "Timetable changes",
    "Haltausfall": "Cancelled stops",
    "Hinweis": "Note",
    "Hinweise": "Notes",
    "Teilausfall": "Partial cancellation",
    "Teilausfall/Ersatzbeförderung": "Partial cancellation/replacement service",
    "Teilausfall/Schienenersatzverkehr": "Partial cancellation/rail replacement service",
}


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """An echo model with a cause rule; records what reaches it."""
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        if text.startswith("Wegen "):
            text = "Due to " + text[len("Wegen "):]
        return [{"translation_text": text}]

    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: pipe)
    return seen


@pytest.mark.parametrize(("label", "english"), sorted(_REAL_LABELS.items()))
def test_every_real_label_has_its_name(label: str, english: str) -> None:
    assert bf._oebb_section_label_en(label) == english


@pytest.mark.parametrize("raw", ["TEILAUSFALL", "GEÄNDERTE FAHRZEITEN", "TEILAUSFALL/SCHIENENERSATZVERKEHR"])
def test_the_cased_label_is_the_one_looked_up(raw: str) -> None:
    cased = bf._oebb_section_labels_cased(f"{raw}: x")
    match = bf._OEBB_LABELLED_SENTENCE_RE.fullmatch(cased)
    assert match is not None
    assert bf._oebb_section_label_en(match["label"]) is not None


def test_the_model_label_is_replaced(model: list[str]) -> None:
    sentence = (
        "Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 zwischen "
        "Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren."
    )
    out = bf._translate_text_attempt(sentence, "oebb-901477", source=_OEBB)
    assert out is not None
    assert out.startswith("Partial cancellation: Von 18.10.2026")
    assert "Teilausfall" not in out
    # The model reads the sentence as before, label included.
    assert any(text.startswith("XGLO") or "Teilausfall" in text for text in model)


def test_a_model_answer_without_its_label_falls_back_to_the_body(monkeypatch: Any) -> None:
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        if text.startswith("Teilausfall"):
            return [{"translation_text": "Some trains cannot run, see U1: Wien Hbf."}]
        return [{"translation_text": text}]

    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: pipe)
    out = bf._translate_text_attempt("Teilausfall: Einige Züge fahren nicht.", source=_OEBB)
    assert out == "Partial cancellation: Einige Züge fahren nicht."
    assert seen[-1] == "Einige Züge fahren nicht."


def test_bzw_does_not_end_the_labelled_sentence(model: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Geänderte Fahrzeiten: Zwischen Budapest-Keleti und Wien Hbf bzw. Salzburg Hbf "
        "fahren die Züge EC 462 mit geänderten Fahrzeiten.",
        source=_OEBB,
    )
    assert out is not None
    assert out.startswith("Timetable changes: Zwischen Budapest-Keleti und Wien Hbf bzw. Salzburg Hbf fahren")
    assert not any(text.rstrip().endswith("bzw.") for text in model)


def test_joined_labels_name_each_part(model: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Teilausfall/Schienenersatzverkehr: Zwischen Tullnerbach-Pressbaum Bahnhof "
        "und Maria Anzbach Bahnhof fahren Busse.",
        source=_OEBB,
    )
    assert out is not None
    assert out.startswith("Partial cancellation/rail replacement service: ")


def test_a_label_after_the_first_sentence(model: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Ein Schienenersatzverkehr wird eingerichtet. Achtung: Anschlussverbindungen "
        "können nicht gewährleistet werden.",
        source=_OEBB,
    )
    assert out is not None
    assert "Please note: Anschlussverbindungen" in out


def test_a_templated_body_keeps_its_template(model: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Ausfall: Wegen Bauarbeiten können von 02.11.2026 bis 03.11.2026 keine R 40-Züge fahren.",
        source=_OEBB,
    )
    assert out == "Cancellation: Due to construction works, no R 40 trains can run from 02.11.2026 to 03.11.2026."


@pytest.mark.parametrize(
    "text",
    [
        "Zeitraum: 02.11.2026 bis 03.11.2026.",
        "Wien Hbf: Die Züge fahren von Bahnsteig 3.",
        "Teilausfall/Zeitraum: 02.11.2026.",
    ],
)
def test_other_heads_take_the_ordinary_path(text: str) -> None:
    assert bf._render_oebb_sentence(text, "x", _OEBB, None) == ""


def test_other_sources_keep_the_model(model: list[str]) -> None:
    out = bf._translate_text_attempt("Hinweis: Die Linie fährt.", source="Wiener Linien")
    assert out is not None
    assert not out.startswith("Note:")


def test_a_cached_model_label_is_translated_again() -> None:
    de = "Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 nicht fahren."
    old = "Partial failure: From 18.10.2026 to 19.10.2026, the RJ 263 train cannot run."
    new = "Partial cancellation: From 18.10.2026 to 19.10.2026, the RJ 263 train cannot run."
    assert bf._oebb_section_label_defect(de, old) == "section label"
    assert bf._oebb_section_label_defect(de, new) is None
    # No label, nothing to heal.
    assert bf._oebb_section_label_defect("Wegen Bauarbeiten fahren keine Züge.", "x") is None


def test_the_heal_is_wired_into_the_cache_for_oebb_only(model: list[str]) -> None:
    de = "Teilausfall: Einige Züge fahren nicht."
    state: dict[str, dict[str, Any]] = {
        "id": {
            "translations": {
                "en": {"summary": "Partial failure: Some trains do not run."},
                bf._SOURCE_DIGEST_KEY: {"summary": bf._source_digest(de)},
            }
        }
    }
    out, ok = bf._cached_translation(de, "summary", "id", state, source=_OEBB)
    assert ok
    assert out.startswith("Partial cancellation:")
    state["id"]["translations"]["en"]["summary"] = "Partial failure: Some trains do not run."
    out, ok = bf._cached_translation(de, "summary", "id", state, source="Wiener Linien")
    assert out == "Partial failure: Some trains do not run."
