"""The joint between reason word and ticker fragment gets an en dash.

WL's display-board tickers put the reason in front of the consequence with
nothing between them: ``31: Demonstration Betrieb ab Wallensteinstraße``.
Read from a distance that is two fragments laid end to end, and word for
word in English it becomes ``Demonstration service from Wallensteinstraße``
— as if „Demonstration service" were a kind of service (C.5, audit of
2026-09-17). Over 300 published revisions 26 of 188 distinct titles had
this shape; the longest is 99 characters against a limit of 256, so the
worry that a separator could push titles over the limit is settled: none
would.

The dash is applied to the finished German title, after the duplicate
checks that compare summaries against the title body — those must keep
seeing the text WL wrote. The English title is translated from the result
and inherits the dash.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed
from src.build_feed import _separate_reason_word
from src.feed_types import FeedItem


def _format(raw_title: str, raw_desc: str) -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "t",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 9, 19, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 55, tzinfo=UTC),
    )
    return formatted.title_out, formatted.desc_text_truncated


# ---------------- the published titles ----------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "31: Demonstration Betrieb ab Wallensteinstraße",
            "31: Demonstration – Betrieb ab Wallensteinstraße",
        ),
        ("1A: Veranstaltung Kein Betrieb", "1A: Veranstaltung – Kein Betrieb"),
        (
            "2: Demonstration Züge halten Steig A & Züge halten bei Linie 46",
            "2: Demonstration – Züge halten Steig A & Züge halten bei Linie 46",
        ),
        (
            "40/41/9/42: Veranstaltung Linien 40 und 41 Umleitung über Linien 9 und 42",
            "40/41/9/42: Veranstaltung – Linien 40 und 41 Umleitung über Linien 9 und 42",
        ),
        ("D: Gleisbauarbeiten Althanstraße", "D: Gleisbauarbeiten – Althanstraße"),
        # Without a line prefix the body is the whole title.
        ("Falschparker Umleitung bis Gersthof", "Falschparker – Umleitung bis Gersthof"),
    ],
)
def test_the_joint_gets_a_dash(title: str, expected: str) -> None:
    assert _separate_reason_word(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        # A sentence, not a fragment: the next word is not capitalised.
        "1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026",
        "27A/28A/29A: Veranstaltung am 27.09.2026",
        # The reason word alone.
        "37A: Bauarbeiten",
        "44A: Kurzführung",
        # No reason word in front.
        "2: Züge halten in der Mühlfeldgasse",
        "12A: Betrieb ab Johnstraße U",
        "S 45: Wien Hütteldorf ↔ Wien Handelskai",
        "72A: Kraftwerk Simmering",
        "",
    ],
)
def test_everything_else_is_left_alone(title: str) -> None:
    assert _separate_reason_word(title) == title


def test_it_is_idempotent() -> None:
    once = _separate_reason_word("31: Demonstration Betrieb ab Wallensteinstraße")
    assert _separate_reason_word(once) == once


# ---------------- end to end: the checks still see WL's text ----------------


def test_the_duplicate_check_still_empties_the_restated_ticker() -> None:
    """Outcome, not order: the title gets its dash AND the restated ticker
    is still emptied. (``_drop_category_word`` tolerates a dash behind the
    reason word, so applying the dash earlier would pass this too — the
    "after the checks" placement is a design choice, not something a test
    can pin.)"""
    title, desc = _format("1A: Veranstaltung Kein Betrieb", "Veranstaltung\nKein Betrieb")

    assert title == "1A: Veranstaltung – Kein Betrieb"
    assert desc == "[Am 19.09.2026]"


def test_the_published_item_end_to_end() -> None:
    # The ``Linie 31:`` prefix is stripped at the provider stage
    # (``_post_filter_wl``); the formatter sees the text without it.
    title, desc = _format(
        "31: Demonstration Betrieb ab Wallensteinstraße",
        "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
    )

    assert title == "31: Demonstration – Betrieb ab Wallensteinstraße"
    assert desc.startswith("Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.")


def test_a_sentence_title_keeps_its_shape_end_to_end() -> None:
    title, _ = _format("1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026", "<p>Wegen einer Demonstration.</p>")
    assert title == "1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026"


# ---------------- the English title inherits the dash ----------------

_FAKE_DICT = {"Betrieb": "service", "ab": "from", "Kein": "No"}


def _fake_translation(text: str, **kwargs: Any) -> list[dict[str, str]]:
    """Word-for-word stand-in for the NMT model; unknown tokens pass through."""
    out = [_FAKE_DICT.get(tok, tok) for tok in re.split(r"(\W+)", text)]
    return [{"translation_text": "".join(out)}]


def test_the_english_title_reads_as_reason_and_consequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Demonstration service from …`` was the C.5 complaint; with the dash
    the English reads ``Demonstration – service from …``."""
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _fake_translation)
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
    state: dict[str, dict[str, Any]] = {}
    formatted = build_feed._format_item_content(
        item,
        ident="t-en",
        starts_at=datetime(2026, 9, 19, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 55, tzinfo=UTC),
        lang="en",
        state=state,
    )

    assert formatted.title_out == "31: Demonstration – service from Wallensteinstraße", formatted.title_out
