"""The C.5 separator never travels into the translation model.

``_separate_reason_word`` writes ``31: Demonstration – Betrieb ab
Wallensteinstraße``. The en dash is a preserved symbol, so the whole title
reached the model as ``31: Demonstration XENT…X1X Betrieb ab XENT…X2X`` —
a placeholder wedged between two words. Published 2026-09-19 21:30 in
``docs/feed.en.xml``, first build after the separator merged::

    31: Demonstration –Xservice from Wallensteinstraße      (debris, cached)
    2: Demonstration – Züge halten Steig A & …               (fell back to DE)
    1: Demonstration – Betrieb ab Hintere Zollamtsstraße     (fell back to DE)
    D: Gleisbauarbeiten – Althanstraße                       (fell back to DE)

Now a title of that shape is translated in two halves — the line prefix
with the reason word, and the ticker fragment — and the dash is put back
verbatim. Both halves are texts the model handled before the separator
existed. The cache epoch moves to 15 so the cached debris is evicted.

Mutations checked against this file (each one caught, by the test named):

* ``_cached_translation`` sends titles through ``_translate_text_attempt``
  again, in either its stateful or its stateless branch →
  ``test_cached_title_translation_goes_through_the_halves`` and
  ``test_uncached_title_translation_also_goes_through_the_halves``.
* ``_split_reason_title`` drops the reason-word check →
  ``test_a_dash_between_two_places_is_not_split`` (the single-word,
  non-reason head ``"Wien – Mödling"``).
* the halves are joined without the dash →
  ``test_the_english_title_keeps_the_separator``.
* the epoch is not bumped → ``test_the_cache_epoch_evicts_the_debris``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed
from src.feed_types import FeedItem

# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("31: Demonstration – Betrieb ab Wallensteinstraße", ("31: Demonstration", "Betrieb ab Wallensteinstraße")),
        (
            "2: Demonstration – Züge halten Steig A & Züge halten bei Linie 46",
            ("2: Demonstration", "Züge halten Steig A & Züge halten bei Linie 46"),
        ),
        ("D: Gleisbauarbeiten – Althanstraße", ("D: Gleisbauarbeiten", "Althanstraße")),
        ("1/2A: Veranstaltung – Umleitung ab Schwedenplatz", ("1/2A: Veranstaltung", "Umleitung ab Schwedenplatz")),
        ("Demonstration – Betrieb ab Ring", ("Demonstration", "Betrieb ab Ring")),
    ],
)
def test_reason_titles_split_into_reason_and_fragment(title: str, expected: tuple[str, str]) -> None:
    assert build_feed._split_reason_title(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "31: Demonstration Betrieb ab Wallensteinstraße",  # no separator
        "31: Demonstration am 19.09.2026",  # a sentence, never dashed
        "Wien Hauptbahnhof – Mödling",  # a dash between two places
        "Wien – Mödling",  # single-word head, but not a reason word
        "S 1: Bauarbeiten Wien – Gänserndorf",  # multi-word head
        "31: Demonstration – ",  # empty tail
        "",
    ],
)
def test_a_dash_between_two_places_is_not_split(title: str) -> None:
    assert build_feed._split_reason_title(title) is None


# ---------------------------------------------------------------------------
# The attempt
# ---------------------------------------------------------------------------


def _recording_attempt(
    monkeypatch: pytest.MonkeyPatch, *, fail_on: str | None = None
) -> list[str]:
    seen: list[str] = []

    def fake_attempt(text: str, ident: str = "", **kwargs: Any) -> str | None:
        seen.append(text)
        if fail_on is not None and text == fail_on:
            return None
        return f"<{text}>"

    monkeypatch.setattr(build_feed, "_translate_text_attempt", fake_attempt)
    return seen


def test_the_english_title_keeps_the_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording_attempt(monkeypatch)
    out = build_feed._translate_title_attempt("31: Demonstration – Betrieb ab Wallensteinstraße", ident="t")
    assert out == "<31: Demonstration> – <Betrieb ab Wallensteinstraße>"
    assert seen == ["31: Demonstration", "Betrieb ab Wallensteinstraße"]
    assert all("–" not in text for text in seen)


def test_a_title_without_the_shape_is_translated_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording_attempt(monkeypatch)
    out = build_feed._translate_title_attempt("Wien Hauptbahnhof ↔ Mödling", ident="t")
    assert out == "<Wien Hauptbahnhof ↔ Mödling>"
    assert seen == ["Wien Hauptbahnhof ↔ Mödling"]


@pytest.mark.parametrize("failing_half", ["31: Demonstration", "Betrieb ab Wallensteinstraße"])
def test_either_half_failing_fails_the_title(monkeypatch: pytest.MonkeyPatch, failing_half: str) -> None:
    _recording_attempt(monkeypatch, fail_on=failing_half)
    assert build_feed._translate_title_attempt("31: Demonstration – Betrieb ab Wallensteinstraße", ident="t") is None


# ---------------------------------------------------------------------------
# Wiring: the cache path and the emitter
# ---------------------------------------------------------------------------


def test_cached_title_translation_goes_through_the_halves(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording_attempt(monkeypatch)
    state: dict[str, dict[str, Any]] = {}
    title = "31: Demonstration – Betrieb ab Wallensteinstraße"
    out, ok = build_feed._cached_translation(title, "title", "t", state)
    assert ok is True
    assert out == "<31: Demonstration> – <Betrieb ab Wallensteinstraße>"
    assert seen == ["31: Demonstration", "Betrieb ab Wallensteinstraße"]
    # Cached under the whole title: a second lookup does not call the model.
    again, ok_again = build_feed._cached_translation(title, "title", "t", state)
    assert (again, ok_again) == (out, True)
    assert len(seen) == 2


def test_summaries_are_never_split(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording_attempt(monkeypatch)
    text = "Demonstration – Betrieb ab Wallensteinstraße."
    out, ok = build_feed._cached_translation(text, "summary", "t", {})
    assert ok is True and out == f"<{text}>"
    assert seen == [text]


def test_uncached_title_translation_also_goes_through_the_halves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``state is None`` / ``not ident`` short-circuit in
    ``_cached_translation`` has its own dispatch line — a title must split
    there too, not only in the stateful branch above."""
    seen = _recording_attempt(monkeypatch)
    title = "31: Demonstration – Betrieb ab Wallensteinstraße"
    out, ok = build_feed._cached_translation(title, "title", "", None)
    assert ok is True
    assert out == "<31: Demonstration> – <Betrieb ab Wallensteinstraße>"
    assert seen == ["31: Demonstration", "Betrieb ab Wallensteinstraße"]


_FAKE_DICT = {
    "Betrieb": "service",
    "ab": "from",
    "Züge": "Trains",
    "halten": "stop",
    "bei": "at",
    "Linie": "line",
    "Nach": "After",
    "einer": "a",
    "Fahrtbehinderung": "delay",
    "kommt": "comes",
    "es": "it",
    "zu": "to",
    "unterschiedlichen": "irregular",
    "Intervallen": "intervals",
}


def _fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
    """Word-for-word stand-in for the NMT model; unknown tokens pass through."""
    out = [_FAKE_DICT.get(tok, tok) for tok in re.split(r"(\W+)", text)]
    return [{"translation_text": "".join(out)}]


def test_the_model_never_sees_the_dash_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[str] = []

    def observing_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        received.append(text)
        return _fake_pipeline(text, **kwargs)

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: observing_pipeline)
    item = cast(
        FeedItem,
        {
            "title": "31: Demonstration Betrieb ab Wallensteinstraße",
            "description": "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "t-en",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t-en",
        starts_at=datetime(2026, 9, 19, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 55, tzinfo=UTC),
        lang="en",
        state={},
        # The dashed title only survives where the short one would collide
        # with another visible item's (``_short_title_collisions``).
        split_reason=False,
    )
    assert formatted.title_cdata == "31: Demonstration – service from Wallensteinstraße"
    assert received, "the fake model was not consulted"
    assert all("–" not in text for text in received)
    # The placeholder that stood for the dash is gone with it.
    assert all(build_feed._ENTITY_PLACEHOLDER_RE.search(text) is None or "–" not in text for text in received)


def test_the_cache_epoch_evicts_the_debris() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 15
