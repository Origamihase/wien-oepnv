"""The EN feed must never serve a translation of a headline the DE feed dropped.

Observed live on 2026-09-13 in the published feeds — same GUID, same item,
two different statements:

    docs/feed.xml    '44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz'
    docs/feed.en.xml '44: event Trains stop Rosensteingasse on line 9 direction
                      Westbahnhof'

The English was a faithful translation — of the German title the item carried
*earlier that day*. Wiener Linien rewords a live disruption as the situation
develops while keeping its identity, and the translation cache is keyed
``(ident, field)``: nothing recorded which German text a cached translation had
been made from. The one comparison that existed, ``cached != text``, compares an
English string against a German one and is therefore true for essentially every
item, so the first translation was served for the item's whole lifetime.

That is worse than an untranslated item. An untranslated item is visibly German;
this one is fluent, plausible English about something that is no longer
happening.

``_TRANSLATION_CACHE_EPOCH`` could not catch it — it invalidates when *our*
glossary or masking changes and knows nothing about upstream edits. The fix adds
a per-field fingerprint of the German source (``_SOURCE_DIGEST_KEY``); the two
mechanisms keep one job each.

This module also covers the capitalisation of EN titles (audit C.3), the other
thing an English subscriber sees on every single item.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed


def _fc(title: str) -> build_feed.FormattedContent:
    """Minimal FormattedContent carrying just the title under test."""
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


@pytest.fixture()
def recording_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the NMT call with a recorder; returns the list of translated texts."""
    calls: list[str] = []

    def _fake(
        text: str,
        *,
        ident: str | None = None,
        source: str | None = None,
        category: str | None = None,
    ) -> str:
        calls.append(text)
        return f"EN<{text}>"

    monkeypatch.setattr(build_feed, "_translate_text_attempt", _fake)
    return calls


# --- source-drift guard -----------------------------------------------------


def test_reworded_source_invalidates_the_cached_translation(
    recording_pipeline: list[str],
) -> None:
    """The live bug: same identity, new German wording, stale English.

    Pre-fix the second call returned ``EN<Veranstaltung A>`` — the translation
    of a headline the German feed had already replaced.
    """
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    first, ok = build_feed._cached_translation("Veranstaltung A", "title", ident, state)
    assert (first, ok) == ("EN<Veranstaltung A>", True)

    second, ok = build_feed._cached_translation(
        "Betrieb ab Berger-Platz", "title", ident, state
    )
    assert ok is True
    assert second == "EN<Betrieb ab Berger-Platz>"
    assert recording_pipeline == ["Veranstaltung A", "Betrieb ab Berger-Platz"]


def test_unchanged_source_is_still_a_cache_hit(
    recording_pipeline: list[str],
) -> None:
    """The guard must not turn every build into a full re-translation.

    This is the cost side of the fix: the cache still has to absorb the
    common case, or each build pays the NMT pipeline for every item.
    """
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    build_feed._cached_translation("Veranstaltung A", "title", ident, state)
    for _ in range(3):
        out, ok = build_feed._cached_translation(
            "Veranstaltung A", "title", ident, state
        )
        assert (out, ok) == ("EN<Veranstaltung A>", True)

    assert recording_pipeline == ["Veranstaltung A"], "cache stopped absorbing re-reads"


def test_digest_is_stamped_next_to_the_translation(
    recording_pipeline: list[str],
) -> None:
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    build_feed._cached_translation("Veranstaltung A", "title", ident, state)

    translations = state[ident]["translations"]
    assert translations["en"]["title"] == "EN<Veranstaltung A>"
    assert translations[build_feed._SOURCE_DIGEST_KEY]["title"] == (
        build_feed._source_digest("Veranstaltung A")
    )


def test_fields_drift_independently(recording_pipeline: list[str]) -> None:
    """Rewording the title must not discard a still-valid summary."""
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    build_feed._cached_translation("Titel A", "title", ident, state)
    build_feed._cached_translation("Zusammenfassung A", "summary", ident, state)
    recording_pipeline.clear()

    build_feed._cached_translation("Titel B", "title", ident, state)
    build_feed._cached_translation("Zusammenfassung A", "summary", ident, state)

    assert recording_pipeline == ["Titel B"], "summary was re-translated unnecessarily"


def test_failed_translation_stamps_no_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed run must leave nothing behind that a later build would trust.

    Mirrors the existing "Sticky German" contract: no translation is cached,
    so no digest may be cached either — otherwise the next build could pair a
    matching digest with a missing string.
    """
    monkeypatch.setattr(
        build_feed,
        "_translate_text_attempt",
        lambda text, **_kw: None,
    )
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    out, ok = build_feed._cached_translation("Veranstaltung A", "title", ident, state)

    assert (out, ok) == ("Veranstaltung A", False)
    translations = state[ident]["translations"]
    assert "title" not in translations.get("en", {})
    assert "title" not in translations.get(build_feed._SOURCE_DIGEST_KEY, {})


def test_legacy_entry_without_digest_is_trusted(
    recording_pipeline: list[str],
) -> None:
    """Entries cached before the digest existed stay served.

    Deliberate: making "no digest" a miss would duplicate what the epoch bump
    to 6 already does once, and would break the contract that a current-epoch
    cache is served without touching the pipeline.
    """
    ident = "wl|44|abc"
    state: dict[str, dict[str, Any]] = {
        ident: {
            "translations": {
                "en": {"title": "Cached English"},
                "epoch": build_feed._TRANSLATION_CACHE_EPOCH,
            }
        }
    }

    out, ok = build_feed._cached_translation("Deutscher Titel", "title", ident, state)

    assert (out, ok) == ("Cached English", True)
    assert recording_pipeline == []


def test_epoch_bump_rolls_the_digest_out() -> None:
    """The epoch bump is what stamps a digest onto pre-existing entries."""
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 6


def test_eviction_drops_the_digest_with_the_strings() -> None:
    """A digest must never outlive the translation it validates."""
    ident = "wl|44|abc"
    state: dict[str, dict[str, Any]] = {
        ident: {
            "translations": {
                "en": {"title": "Cached English"},
                "epoch": 1,
                build_feed._VERBATIM_FIELDS_KEY: ["title"],
                build_feed._SOURCE_DIGEST_KEY: {"title": "deadbeefdeadbeef"},
            }
        }
    }

    build_feed._evict_stale_translations(ident, state)

    translations = state[ident]["translations"]
    assert "en" not in translations
    assert build_feed._VERBATIM_FIELDS_KEY not in translations
    assert build_feed._SOURCE_DIGEST_KEY not in translations


def test_overlay_serves_the_reworded_title(recording_pipeline: list[str]) -> None:
    """End-to-end through the overlay: the EN title follows the German."""
    state: dict[str, dict[str, Any]] = {}
    ident = "wl|44|abc"

    first = build_feed._apply_lang_overlay(
        _fc("Veranstaltung A"), "", "", ident, "en", state
    )
    assert first.title_out == "EN<Veranstaltung A>"

    second = build_feed._apply_lang_overlay(
        _fc("Betrieb ab Berger-Platz"), "", "", ident, "en", state
    )
    assert second.title_out == "EN<Betrieb ab Berger-Platz>"


# --- EN title capitalisation (audit C.3) ------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # German capitalises every noun; the English common nouns come back
        # lower-cased from the model and read like a typo after the line code.
        ("44: event Trains stop Rosensteingasse", "44: Event Trains stop Rosensteingasse"),
        ("86A/86AR/87A: event", "86A/86AR/87A: Event"),
        ("3A: buses stop Kärntner Ring 5", "3A: Buses stop Kärntner Ring 5"),
        ("17A: construction works", "17A: Construction works"),
        ("46/49/52: track construction works", "46/49/52: Track construction works"),
    ],
)
def test_title_body_is_capitalised(raw: str, expected: str) -> None:
    assert build_feed._capitalise_title_body(raw) == expected


@pytest.mark.parametrize(
    "title",
    [
        "49A/50B: Hüttergasse",                      # already capitalised
        "Wien Hauptbahnhof ↔ Gramatneusiedl",        # no line prefix
        "Märzstraße 49 to junction Huglgasse",       # no line prefix, capitalised
        "3A: 46/49 replacement",                     # body starts with a digit
        "S1: ÖBB service",                           # non-ASCII capital
        "",
    ],
)
def test_title_left_alone_when_no_change_is_warranted(title: str) -> None:
    assert build_feed._capitalise_title_body(title) == title


def test_capitalisation_does_not_lowercase_the_rest() -> None:
    """``str.capitalize`` would wreck line codes, stations and acronyms."""
    out = build_feed._capitalise_title_body("13A: buses stop Kärntner Ring, ÖBB U4")
    assert out == "13A: Buses stop Kärntner Ring, ÖBB U4"


def test_overlay_capitalises_the_shipped_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capitalisation reaches the string that actually ships.

    Drives the real overlay with a pipeline that returns exactly what the NMT
    model produced live for ``44: Veranstaltung``.
    """
    monkeypatch.setattr(
        build_feed,
        "_translate_text_attempt",
        lambda text, **_kw: "44: event trains stop Rosensteingasse",
    )
    state: dict[str, dict[str, Any]] = {}

    out = build_feed._apply_lang_overlay(
        _fc("44: Veranstaltung Züge halten Rosensteingasse"),
        "",
        "",
        "wl|44|x",
        "en",
        state,
    )

    assert out.title_out == "44: Event trains stop Rosensteingasse"
    assert out.title_cdata.find("44: Event") != -1, "the rendered CDATA must match"
