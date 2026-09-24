"""A WL line list repeated after the prefix is recognised and dropped.

Published in ``docs/feed.xml``:

* 2026-09-19/20, item 7: ``4A/80A/N29: 4A. 80A, N29: Wittelsbachstraße``
* 2026-09-24, item 2:    ``N66/N68R: N66, Rufbus N68: Quellenplatz``

WL writes the affected lines at the start of the title; the provider puts
the ``relatedLines`` prefix in front and ``_extract_prefix_lines`` is meant
to fold the title's own list into it. Two list shapes slipped through:

* ``. `` (period + space) as a separator — the parser split only on
  ``,``/``/``/``+``.
* a single code before ``Rufbus X`` — ``LINES_COMPLEX_PREFIX_RE`` needed
  two codes before the Rufbus token.

A third detail: ``relatedLines`` names the on-demand bus ``N68R`` while the
title says ``Rufbus N68``. Both are one line; folding the list must not
turn the prefix into ``N66/N68R/N68``.

Measured over 453 revisions of the WL cache (608 distinct titles): these
two titles are the only ones whose body opens with a colon-terminated
list of the prefix's own lines.

Mutations checked against this file (each one caught, by the test named):

* the period separator is dropped from the list regex →
  ``test_period_separated_list_is_folded``.
* the single-code-then-Rufbus alternative is dropped →
  ``test_single_code_then_rufbus_is_folded``.
* the Rufbus-twin check is dropped from ``_extract_prefix_lines`` →
  ``test_the_build_repairs_the_published_titles``.
* the Rufbus-twin check is dropped from ``_ensure_line_prefix`` →
  ``test_the_provider_renders_the_raw_titles_cleanly``.
* the whitespace after the period separator is made optional →
  ``test_non_line_text_before_a_colon_is_left_alone`` (``13.10:``,
  ``17.30:``).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.build_feed import _post_filter_wl
from src.providers.wl_lines import _ensure_line_prefix, _extract_prefix_lines


def test_period_separated_list_is_folded() -> None:
    assert _extract_prefix_lines("4A. 80A, N29: Wittelsbachstraße") == (
        "Wittelsbachstraße",
        ["4A", "80A", "N29"],
    )


def test_single_code_then_rufbus_is_folded() -> None:
    assert _extract_prefix_lines("N66, Rufbus N68: Quellenplatz") == ("Quellenplatz", ["N66", "N68"])


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        # Verbatim from cache/wl_9d709a/events.json.
        ("N66/N68R: N66, Rufbus N68: Quellenplatz", "N66/N68R: Quellenplatz"),
        ("4A/80A/N29: 4A. 80A, N29: Wittelsbachstraße", "4A/80A/N29: Wittelsbachstraße"),
        # The earlier fix for this family keeps working: no N61R in the
        # prefix, so the Rufbus line joins it.
        ("56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026", "56A/60A/N60/N61: Maurer Kirtag 2026"),
    ],
)
def test_the_build_repairs_the_published_titles(cached: str, expected: str) -> None:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Hinweis",
        "title": cached,
        "description": "Haltestellenverlegung.",
        "guid": "g",
    }
    out = _post_filter_wl([item])
    assert [i["title"] for i in out] == [expected]


@pytest.mark.parametrize(
    ("raw", "related", "expected"),
    [
        ("N66, Rufbus N68: Quellenplatz", ["N66", "N68R"], "N66/N68R: Quellenplatz"),
        ("4A. 80A, N29: Wittelsbachstraße", ["4A", "80A", "N29"], "4A/80A/N29: Wittelsbachstraße"),
        # relatedLines without the R-twin: the Rufbus line is added as before.
        ("N66, Rufbus N68: Quellenplatz", ["N66"], "N66/N68: Quellenplatz"),
    ],
)
def test_the_provider_renders_the_raw_titles_cleanly(raw: str, related: list[str], expected: str) -> None:
    assert _ensure_line_prefix(raw, related) == expected


def test_an_r_line_without_a_rufbus_mention_is_kept() -> None:
    # Both codes in the prefix, no "Rufbus" text: the twin rule only
    # applies to a line the title calls "Rufbus X", so both stay.
    assert _extract_prefix_lines("N68/N68R: Umleitung") == ("Umleitung", ["N68", "N68R"])
    assert _ensure_line_prefix("N68: Umleitung", ["N68R"]) == "N68R/N68: Umleitung"


@pytest.mark.parametrize(
    "title",
    [
        # A date or time before a colon is not a list of lines: the period
        # separator needs whitespace after it.
        "13.10: Sperre",
        "17.30: Verspätung",
        # Period + whitespace but a word: the strict token gate rejects it.
        "10. Bezirk: Sperre",
        "Achtung. Hinweis: Sperre",
    ],
)
def test_non_line_text_before_a_colon_is_left_alone(title: str) -> None:
    assert _extract_prefix_lines(title) == (title, [])
