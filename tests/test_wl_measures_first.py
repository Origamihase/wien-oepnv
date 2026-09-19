"""The 180-character extract kept the announcement and dropped the measures.

WL writes its full notices in one shape — an announcement, a period, a
``Zeitraum:`` sentence, then one measure per line. The extract takes the
first sentence and the second only if both fit in 180 characters. For the
seven-line demonstration notice of 2026-09-19 that produced::

    Wegen einer Demonstration im Bereich Schwarzenbergplatz und Ring kommt
    es zu folgenden Verkehrsmaßnahmen.

``folgenden`` — and nothing followed. 20 of the 37 notices in the cache
carry a measure block. ``_prefer_measures`` puts the block first: the
announcement goes (the title names the reason), ``Zeitraum:`` goes (the
item's own date line renders the period), an introduction with content
of its own stays, and the measures are joined with ``;`` so the cut at 180
lands inside the list with an ellipsis instead of after the first line.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.build_feed import _prefer_measures
from src.feed_types import FeedItem


def _render(raw_title: str, raw_desc: str) -> str:
    """The description a subscriber ends up seeing, timeframe included."""
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "test",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 9, 18, 0, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 19, 21, 30, tzinfo=UTC),
    )
    return formatted.desc_text_truncated


# The cache's HTML, shortened.
_DEMO_HTML = (
    "<h2>Demonstration</h2> <p>Wegen einer Demonstration im Bereich "
    "Schwarzenbergplatz und Ring kommt es zu folgenden "
    "Verkehrsma&szlig;nahmen.</p> <p><span><strong>Zeitraum:</strong></span>"
    "<br />Samstag, 19. September 2026, ab ca. 12:00 Uhr bis ca. 21:30 Uhr."
    "</p> <p><span><strong>Ma&szlig;nahmen:</strong></span><br />"
    "<strong>Linie D:</strong> Derzeit kein Betrieb zwischen B&ouml;rse und "
    "Quartier Belvedere.<br /><strong>Linie 1:</strong> Umleitung in beiden "
    "Richtungen zwischen Kliebergasse und Hintere Zollamtsstra&szlig;e "
    "&uuml;ber Landstra&szlig;e S U und Hauptbahnhof S U (Strecke Linien O "
    "und 18).<br /><strong>Linie 2:</strong> Kein Betrieb zwischen Parlament, "
    "U Volkstheater und Heinestra&szlig;e. Weiterfahrt ab Heinestra&szlig;e "
    "bis Praterstern S U.<br /><strong>Linie 2A:</strong> Kein Betrieb "
    "m&ouml;glich.</p>"
)

_KURZFUEHRUNG_HTML = (
    "<h2>Bauarbeiten</h2> <p>Wegen Instandsetzungsarbeiten in der "
    "R&ouml;ntgengasse wird die Linie 44A kurzgef&uuml;hrt.</p> "
    "<p><strong>Zeitraum:</strong><br />Montag, 3. August 2026, bis Ende "
    "September 2026.</p> <p><strong>Linie 44A:</strong> Kein Betrieb "
    "zwischen Heuberg und Mitterberg.</p>"
)


# ---------------- the published notice, end to end ----------------


def test_the_measures_reach_the_display() -> None:
    desc = _render("1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026", _DEMO_HTML)

    assert desc.startswith(
        "Linie D: Derzeit kein Betrieb zwischen Börse und Quartier Belvedere; Linie 1: Umleitung in beiden Richtungen"
    ), desc
    assert "folgenden Verkehrsmaßnahmen" not in desc
    assert "Zeitraum:" not in desc
    assert "Maßnahmen:" not in desc


def test_the_cut_lands_inside_the_list_with_an_ellipsis() -> None:
    """One line's measure alone would be thin; the ellipsis says there is more."""
    desc = _render("1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026", _DEMO_HTML)
    summary = desc.split(" [")[0]

    assert summary.endswith("…"), summary
    assert len(summary) <= 180
    assert "Linie 1:" in summary


def test_an_introduction_with_content_of_its_own_stays() -> None:
    desc = _render("44A: Kurzführung", _KURZFUEHRUNG_HTML)

    assert desc.startswith(
        "Wegen Instandsetzungsarbeiten in der Röntgengasse wird die Linie 44A "
        "kurzgeführt. Linie 44A: Kein Betrieb zwischen Heuberg und Mitterberg."
    ), desc
    assert "Zeitraum:" not in desc


# ---------------- the helper, piece by piece ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Wegen einer Demonstration kommt es zu folgenden Verkehrsmaßnahmen. "
            "Zeitraum: Samstag, 19. September 2026, ab ca. 12:00 Uhr. "
            "Maßnahmen: Linie D: Kein Betrieb zwischen Börse und Quartier "
            "Belvedere. Linie 1: Umleitung über Landstraße.",
            "Linie D: Kein Betrieb zwischen Börse und Quartier Belvedere; Linie 1: Umleitung über Landstraße.",
        ),
        # Several lines in one measure, ``und`` and ``/`` joined.
        (
            "Wegen Bauarbeiten kommt es zu folgenden Einschränkungen. "
            "Linien 56A und 56B: Umleitung über Lainzer Straße. "
            "Linie 58A/58B: Kein Halt in Wattmanngasse.",
            "Linien 56A und 56B: Umleitung über Lainzer Straße; Linie 58A/58B: Kein Halt in Wattmanngasse.",
        ),
        # An introduction that says something stays in front.
        (
            "Wegen Instandsetzungsarbeiten wird die Linie 44A kurzgeführt. "
            "Zeitraum: bis Ende September 2026. "
            "Linie 44A: Kein Betrieb zwischen Heuberg und Mitterberg.",
            "Wegen Instandsetzungsarbeiten wird die Linie 44A kurzgeführt. Linie 44A: Kein Betrieb zwischen Heuberg und Mitterberg.",
        ),
    ],
)
def test_prefer_measures(text: str, expected: str) -> None:
    assert _prefer_measures(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # No measure block: nothing to prefer, not even the announcement goes.
        "Wegen Bauarbeiten kommt es zu folgenden Einschränkungen. Zeitraum: bis Ende September 2026.",
        # A display ticker's own ``Linie 2:`` prefix at the very start is a
        # measure block of one — the text comes back as it was.
        "Linie 2: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "Kein Betrieb zwischen Gersthof und Herbeckstraße. Grund: Gleisschaden.",
        "",
    ],
)
def test_texts_without_a_measure_block_are_untouched(text: str) -> None:
    assert _prefer_measures(text) == text


def test_the_measure_patterns_reference_the_shared_line_token() -> None:
    """Reuse, not a fourth copy — asserted on the source, not the pattern."""
    source = pathlib.Path(build_feed.__file__).read_text(encoding="utf-8")
    wanted = {"_WL_MEASURE_START_RE", "_WL_MEASURE_JOIN_RE"}
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        targets: list[ast.expr]
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)} & wanted
        if names:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            assert "_WL_DESC_LINE_TOKEN" in segment, segment
            found |= names
    assert found == wanted


def test_the_extract_and_the_helper_split_sentences_the_same_way() -> None:
    """Both must agree on what a sentence end is, or the helper keeps a
    ``Zeitraum:`` fragment the extract then shows."""
    text = (
        "Wegen Bauarbeiten in der Maxingstraße werden die Linien umgeleitet. "
        "Zeitraum: bis Ende September dieses Jahres. "
        "Linie 56A: Umleitung über Lainzer Straße."
    )
    assert build_feed._SENTENCE_SPLIT_RE.split(text) == [
        "Wegen Bauarbeiten in der Maxingstraße werden die Linien umgeleitet.",
        "Zeitraum: bis Ende September dieses Jahres.",
        "Linie 56A: Umleitung über Lainzer Straße.",
    ]
    # A period after a digit is no sentence end for either of them —
    # ``2026. Linie`` stays one sentence, and the helper still finds the
    # measure block by its own pattern.
    assert (
        _prefer_measures("Zeitraum: bis Ende September 2026. Linie 56A: Umleitung über Lainzer Straße.")
        == "Linie 56A: Umleitung über Lainzer Straße."
    )
