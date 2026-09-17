"""A WL short message must not reach the feed as a headline over nothing.

``_strip_summary_category_prefix`` was written for the WL *Hinweise*, whose
HTML puts a ``<h2>Gleisbauarbeiten</h2>`` in front of the prose. There the
word is a leaked heading, the sentence continues behind it, and dropping it
loses nothing.

The display-ticker *Störungen* carry the same shape and mean something else::

    T: 12A: Betrieb ab Johnstraße U
    D: Gleisbauarbeiten
       Betrieb ab Johnstraße U

Here ``Gleisbauarbeiten`` is the reason, and the only thing the title does
not already say. Stripping it leaves exactly the title body,
``_summary_duplicates_title`` empties that, and the item reached the feed on
2026-09-17 as a headline over a bare ``[16.09.2026 – 17.09.2026]`` — never
telling the reader why. 11 of the 75 cached items were in that state.

The rule tested here reaches for the reason only when the body would
otherwise be empty. Where a sentence survives the strip there is nothing to
rescue, and the stripping itself is untouched — the tests in
``test_heading_leak_strip.py`` and ``test_title_category_dedup_v2.py`` pin
that half.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.feed_types import FeedItem


def _render(raw_title: str, raw_desc: str) -> str:
    """The description a subscriber ends up seeing, timeframe included."""
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 9, 16, 4, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 17, 21, 59, tzinfo=UTC),
    )
    return formatted.desc_text_truncated


@pytest.mark.parametrize(
    ("title", "desc", "reason"),
    [
        # The live items, verbatim from cache/wl_9d709a/events.json.
        ("12A: Betrieb ab Johnstraße U",
         "Gleisbauarbeiten\nBetrieb ab Johnstraße U", "Gleisbauarbeiten"),
        ("11A: Einstieg bei Vorgartenstraße vor Meiereistraße",
         "Bauarbeiten\nEinstieg bei Vorgartenstraße vor Meiereistraße",
         "Bauarbeiten"),
        ("66A: Busse halten Salvatorianerplatz",
         "Bauarbeiten\nBusse halten Salvatorianerplatz", "Bauarbeiten"),
        ("25: Linie 26E hält Donaufelder Straße 148",
         "Gleisbauarbeiten\nLinie 26E hält Donaufelder Straße 148",
         "Gleisbauarbeiten"),
    ],
)
def test_the_reason_reaches_the_reader(title: str, desc: str, reason: str) -> None:
    """Without this the item says what changed, never why."""
    out = _render(title, desc)

    assert f"Grund: {reason}." in out
    # And it must not have come back as the old duplicate: the title body
    # may not be restated underneath the title.
    body = title.split(": ", 1)[1]
    assert body not in out


def test_the_title_body_is_still_never_repeated() -> None:
    """The duplicate check keeps doing its job — only its leftovers changed."""
    out = _render(
        "62A: Busse halten Breitenfurter Straße 236-238",
        "Bauarbeiten Busse halten Breitenfurter Straße 236-238",
    )

    assert "Busse halten Breitenfurter" not in out
    assert out.startswith("Grund: Bauarbeiten.")


def test_a_surviving_sentence_is_left_alone() -> None:
    """Nothing to rescue where the prose continues past the heading word.

    This is the WL *Hinweis* shape the stripping was built for. The reason
    fallback must not fire here, or the informative sentence would be
    replaced by a two-word stub.
    """
    out = _render(
        "11A: Bauarbeiten bis 05.06.2026",
        "Gleisbauarbeiten Wegen Fortschreiten der Gleisbauarbeiten für die "
        "Verlängerung der Linie 18 wird die Haltestelle verlegt.",
    )

    assert out.startswith("Wegen Fortschreiten der Gleisbauarbeiten")
    assert "Grund:" not in out


def test_a_wrapped_fragment_is_not_mistaken_for_a_reason() -> None:
    """The newline in a ticker text is a line break, not a separator.

    ``"Ersatzbus ab\\nFloridsdorf <"`` and ``"Züge halten in\\nTokiostraße"``
    are single phrases the display wrapped mid-sentence — their first line is
    no reason at all. Only the curated ``_CATEGORY_PREFIX_WORDS`` may become
    a ``Grund:``; everything else stays empty rather than inventing one.
    """
    for title, desc in (
        ("27: Ersatzbus ab Floridsdorf", "Ersatzbus ab\nFloridsdorf <"),
        ("26: Züge halten in Tokiostraße", "Züge halten in\nTokiostraße"),
        ("26E: Busse halten Hoßplatz 11", "Busse halten\nHoßplatz 11"),
    ):
        out = _render(title, desc)
        assert "Grund:" not in out, f"invented a reason for {title!r}: {out!r}"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("Gleisbauarbeiten", "Grund: Gleisbauarbeiten."),
        ("Bauarbeiten", "Grund: Bauarbeiten."),
        # WL sometimes trails the heading with punctuation; one period only.
        ("Bauarbeiten.", "Grund: Bauarbeiten."),
        ("Falschparker", "Grund: Falschparker."),
        ("", ""),
    ],
)
def test_reason_only_summary(word: str, expected: str) -> None:
    assert build_feed._reason_only_summary(word) == expected


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Gleisbauarbeiten Betrieb ab Johnstraße U", "Gleisbauarbeiten"),
        # Case is WL's to choose; the match is not.
        ("BAUARBEITEN Busse halten", "BAUARBEITEN"),
        ("Ersatzbus ab Floridsdorf", ""),
        ("Wegen Bauarbeiten wird umgeleitet", ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_leading_category_word(summary: str, expected: str) -> None:
    assert build_feed._leading_category_word(summary) == expected
