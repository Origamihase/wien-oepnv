"""An EN description starts with a capital letter.

Published 2026-09-24 19:31, ``docs/feed.en.xml``, 5 of 10 items::

    stop relocation of line 7A towards Meidling Hauptstraße U …
    stop closure of line N31 towards Schwedenplatz U …

The German opens with a capitalised noun ("Haltestellenverlegung …"); the
glossary renders it as a lower-case common noun because the same word
occurs mid-sentence, and the title fix of audit C.3
(``_capitalise_title_body``) never reached the description. Measured over
the 169 distinct EN descriptions published since 2026-09-10: 45 started
lower-case while their German source did not ("irregular intervals" 22
times, "stop relocation" 14).

Mutations checked against this file (each one caught, by the test named):

* the helper is not applied to the summary in ``_apply_lang_overlay`` →
  ``test_the_shipped_description_is_capitalised`` and
  ``test_a_cached_lower_case_description_ships_capitalised`` (the latter
  pins that a cached value is fixed at render time, without an epoch bump).
* ``str.capitalize`` is used instead of touching one letter →
  ``test_only_the_first_letter_changes``.
* the title helper stops delegating → ``test_the_title_helper_delegates``
  and the C.3 pins in ``tests/test_feed_translation_source_drift.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import _capitalise_sentence_start, _capitalise_title_body


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "stop relocation of line 7A towards Meidling Hauptstraße U",
            "Stop relocation of line 7A towards Meidling Hauptstraße U",
        ),
        ("stop closure of line N31 towards Schwedenplatz U", "Stop closure of line N31 towards Schwedenplatz U"),
        ("irregular intervals on line 6", "Irregular intervals on line 6"),
        ("étage fermé", "Étage fermé"),
    ],
)
def test_a_lower_case_start_is_raised(raw: str, expected: str) -> None:
    assert _capitalise_sentence_start(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Stop: Stammersdorf From: Brünnerstraße",  # a record, already capitalised
        "Due to the high passenger volume",
        "12A: buses stop",  # starts with a digit
        "… continued",  # starts with a symbol
        "ÖBB service",
        "",
    ],
)
def test_left_alone_when_no_change_is_warranted(text: str) -> None:
    assert _capitalise_sentence_start(text) == text


def test_only_the_first_letter_changes() -> None:
    out = _capitalise_sentence_start("stop relocation of line 7A towards Meidling Hauptstraße U, ÖBB U4")
    assert out == "Stop relocation of line 7A towards Meidling Hauptstraße U, ÖBB U4"


def test_the_title_helper_delegates() -> None:
    assert _capitalise_title_body("44: event trains stop") == "44: Event trains stop"
    assert _capitalise_title_body("3A: 46/49 replacement") == "3A: 46/49 replacement"


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


def test_the_shipped_description_is_capitalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drives the overlay with what the glossary path produced live for 7A."""
    replies = {
        "7A: Arthaberplatz": "7A: Arthaberplatz stop",
        "Haltestellenverlegung der Linie 7A in Richtung Meidling Hauptstraße U":
            "stop relocation of line 7A towards Meidling Hauptstraße U",
    }
    monkeypatch.setattr(build_feed, "_translate_text_attempt", lambda text, **_kw: replies[text])
    state: dict[str, dict[str, Any]] = {}

    out = build_feed._apply_lang_overlay(
        _fc("7A: Arthaberplatz"),
        "Haltestellenverlegung der Linie 7A in Richtung Meidling Hauptstraße U",
        "",
        "wl|7A|x",
        "en",
        state,
    )
    assert out.desc_text_truncated.startswith("Stop relocation of line 7A towards"), out.desc_text_truncated
    assert "Stop relocation of line 7A towards" in out.desc_html


def test_a_cached_lower_case_description_ships_capitalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix is applied when rendering, so the 45 cached values need no epoch bump."""

    def _never(text: str, **_kw: Any) -> str:
        raise AssertionError(f"the model must not run for a cache hit: {text!r}")

    monkeypatch.setattr(build_feed, "_translate_text_attempt", _never)
    state: dict[str, dict[str, Any]] = {
        "wl|7A|x": {
            "translations": {
                "epoch": build_feed._TRANSLATION_CACHE_EPOCH,
                "en": {
                    "title": "7A: Arthaberplatz stop",
                    "summary": "stop relocation of line 7A towards Meidling Hauptstraße U",
                },
            }
        }
    }
    out = build_feed._apply_lang_overlay(
        _fc("7A: Arthaberplatz"),
        "Haltestellenverlegung der Linie 7A in Richtung Meidling Hauptstraße U",
        "",
        "wl|7A|x",
        "en",
        state,
    )
    assert out.desc_text_truncated.startswith("Stop relocation of line 7A towards"), out.desc_text_truncated
    # The cache itself keeps the model's wording; only the rendering changes.
    assert state["wl|7A|x"]["translations"]["en"]["summary"].startswith("stop relocation")
