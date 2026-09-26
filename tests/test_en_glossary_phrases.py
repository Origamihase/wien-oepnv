"""EN glossary phrases from the audit of 2026-09-25 (A.3, A.4, A.9) and the ÖBB replacement bus.

Seen in ``docs/feed.en.xml`` and the translation cache (``data/first_seen.json``)::

    A.3  Due to a switch fault, trains to expected 11:00 are only limited …
         Duration: From 22 September 2026, approx. 08:00 until expected end of October 2027
    A.4  … in both directions via track 2. expected duration: 10:10 …
    A.9  Removing traffic restrictions: St. Pölten Hauptbahnhof
         S80: ÖBB-replacement bus

Only the English feed is affected (rank 3). The German feed is unchanged.

Mutations checked against this file (each one caught, by the test named):

* the "bis voraussichtlich" entry is removed → ``test_bis_voraussichtlich_is_one_phrase``.
* the record normalisation is removed → ``test_the_record_says_until_approx``.
* the "ÖBB-Ersatzbus" entries are removed → ``test_the_obb_replacement_bus_has_no_hyphen``.
* the all-clear phrases move to the base glossary → ``test_the_all_clear_phrases_are_obb_only``.
* the capitaliser is not applied when rendering →
  ``test_a_cached_value_ships_with_the_capital``.
* the capitaliser ignores the sentence end → ``test_mid_sentence_terms_stay_small``.
* the abbreviation guard is removed → ``test_mid_sentence_terms_stay_small``.
* the epoch stays at 17 → ``test_the_cache_epoch_was_bumped``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _apply_domain_glossary,
    _capitalise_glossary_after_stop,
    _capitalise_title_body,
    _render_label_record,
    _unmask_entities,
)


def _glossed(text: str, source: str) -> tuple[str, dict[str, str]]:
    out, mapping = _apply_domain_glossary(text, source=source, category="Störung")
    return _unmask_entities(out, mapping), mapping


def test_bis_voraussichtlich_is_one_phrase() -> None:
    out, mapping = _glossed("Der Zugverkehr ist bis voraussichtlich 11:00 Uhr eingeschränkt.", "ÖBB")
    assert "until approx." in out
    assert list(mapping.values()) == ["until approx."]  # one placeholder, not "bis" plus "expected"


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Die Sperre dauert voraussichtlich zwei Wochen.", "expected"),
        ("Voraussichtliche Dauer: 22:00", "expected duration"),
        ("voraussichtliches Ende: 22:00", "expected end"),
    ],
)
def test_the_other_voraussichtlich_entries_are_unchanged(german: str, english: str) -> None:
    assert english in _glossed(german, "Wiener Linien")[0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bis voraussichtlich 22:00 Uhr", "Until approx. 22:00"),
        (
            "Ab 22. September 2026, etwa 08:00 Uhr bis voraussichtlich Ende Oktober 2027",
            "From 22 September 2026, approx. 08:00 until approx. end of October 2027",
        ),
    ],
)
def test_the_record_says_until_approx(value: str, expected: str) -> None:
    rendered = _render_label_record(f"Dauer: {value} Grund: Bauarbeiten", source="Wiener Linien", category="Hinweis")
    assert rendered == f"Duration: {expected} Reason: construction works"


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("S80: ÖBB-Ersatzbus", "S80: ÖBB replacement bus"),
        ("Bhf. Hütteldorf ÖBB-Ersatzbusse", "Bhf. Hütteldorf ÖBB replacement buses"),
    ],
)
def test_the_obb_replacement_bus_has_no_hyphen(german: str, english: str) -> None:
    assert _glossed(german, "Wiener Linien")[0] == english


def test_a_plain_replacement_bus_is_unchanged() -> None:
    assert _glossed("Ersatzbus ab Hütteldorf", "Wiener Linien")[0].startswith("replacement bus")


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Aufhebung Verkehrseinschränkung: St. Pölten Hauptbahnhof", "traffic restriction lifted: St. Pölten Hauptbahnhof"),
        ("Aufhebung Streckenunterbrechung: Wien Handelskai", "line closure lifted: Wien Handelskai"),
    ],
)
def test_an_all_clear_is_a_state(german: str, english: str) -> None:
    out = _glossed(german, "ÖBB")[0]
    assert out == english
    assert _capitalise_title_body(out) == english[0].upper() + english[1:]


def test_the_all_clear_phrases_are_obb_only() -> None:
    assert "lifted" not in _glossed("Aufhebung Verkehrseinschränkung: Karlsplatz", "Wiener Linien")[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Line U1 runs in both directions via track 2. expected duration: 10:10.",
            "Line U1 runs in both directions via track 2. Expected duration: 10:10.",
        ),
        (
            "diversion to Gersthof over lines 42 and 9. diversion to Gersthof",
            "diversion to Gersthof over lines 42 and 9. Diversion to Gersthof",
        ),
        (
            "Tram stops? service obstruction continues! replacement bus runs.",
            "Tram stops? Service obstruction continues! Replacement bus runs.",
        ),
    ],
)
def test_a_glossary_term_opening_a_sentence_gets_a_capital(text: str, expected: str) -> None:
    assert _capitalise_glossary_after_stop(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "There is a service obstruction on line 5.",  # mid-sentence
        "Trains run approx. every 10 minutes.",  # not a glossary term
        "Delays of approx. 5 minutes. The diversion ends at 22:00.",  # already capitalised
        "expected duration: 22:00",  # the text start is _capitalise_sentence_start's job
        "Meidlinger Hauptstr. opp 197",  # an abbreviation, no sentence end
        "Delay of ca. service obstruction",
    ],
)
def test_mid_sentence_terms_stay_small(text: str) -> None:
    assert _capitalise_glossary_after_stop(text) == text


def _fc(title: str) -> build_feed.FormattedContent:
    return build_feed.FormattedContent(
        guid="g",
        link="https://example.invalid/",
        title_cdata=title,
        desc_text_truncated="",
        desc_cdata="",
        raw_desc="",
        title_out=title,
        desc_html="",
    )


def test_a_cached_value_ships_with_the_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    """Applied when rendering: the 115 cached values need no epoch bump for A.4."""

    def _never(text: str, **_kw: Any) -> str:
        raise AssertionError(f"the model must not run for a cache hit: {text!r}")

    monkeypatch.setattr(build_feed, "_translate_text_attempt", _never)
    ident = "wl|U1|x"
    state: dict[str, dict[str, Any]] = {
        ident: {
            "translations": {
                "epoch": build_feed._TRANSLATION_CACHE_EPOCH,
                "en": {
                    "title": "U1: Track change",
                    "summary": "Line U1 runs via track 2. expected duration: 10:10.",
                },
            }
        }
    }
    out = build_feed._apply_lang_overlay(
        _fc("U1: Gleiswechsel"), "Die Linie U1 fährt über Gleis 2. Voraussichtliche Dauer: 10:10.", "", ident, "en", state
    )
    assert "track 2. Expected duration: 10:10." in out.desc_text_truncated
    assert state[ident]["translations"]["en"]["summary"].endswith("2. expected duration: 10:10.")  # the cache is kept


def test_the_cache_epoch_was_bumped() -> None:
    # Cached under 17: "S80: ÖBB-replacement bus", "Removing traffic restrictions: …"
    # and records reading "until expected"; their source digests did not change.
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 18
