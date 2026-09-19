"""WL repeats the same sentence once per affected line — the feed showed it twice.

Wiener Linien attributes a multi-line disruption by listing every line in
ONE description, each segment carrying its own ``Linie X:`` prefix. Raw
from the cache::

    Linie 5B: Unregelmäßige Intervalle in beiden Richtungen.
    Linie 49A: Unregelmäßige Intervalle in beiden Richtungen.
    Linie 50A: Unregelmäßige Intervalle in beiden Richtungen.
    Grund: Verkehrsstörung.

``_strip_wl_description_line_prefix`` removes only the FIRST prefix — the
title already attributes the lines — so the published item read::

    5B/49A/50A: Verkehrsstörung
    Unregelmäßige Intervalle in beiden Richtungen. Linie 49A:
    Unregelmäßige Intervalle in beiden Richtungen. …

The same sentence twice, one arbitrary line out of three named as if it
were special, and ``Grund: Verkehrsstörung.`` — the only part that says
anything new — cut away by the 180-character limit. 13 of 250 published
German items looked like this.

Two boundaries matter more than the fix itself:

* **Segments whose sentences differ stay whole, prefix included.** There
  the ``Linie X:`` attribution is the entire point; the 46/49/52 track
  works below are the guard.
* **Only the repeated leading sentence of a segment goes.** Whatever
  follows stays in place. Measured over 267 revisions of the WL cache,
  that remainder is always the global ``Grund: …`` field and never
  line-specific text — so dropping the sentence never orphans a
  line-specific instruction.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _collapse_repeated_wl_line_segments,
    _post_filter_wl,
    _strip_wl_description_line_prefix,
    _tidy_wl_dangling_location,
    _truncate_summary_180,
)


def _published(desc: str) -> str:
    """Run the three WL description rules in their production order."""
    return _tidy_wl_dangling_location(
        _strip_wl_description_line_prefix(_collapse_repeated_wl_line_segments(desc))
    )


def _stoerung(title: str, description: str) -> dict[str, Any]:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "",
        "guid": "t",
    }


# ---------------- the cases that reached the display ----------------


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        (
            "Linie 5B: Unregelmäßige Intervalle in beiden Richtungen. "
            "Linie 49A: Unregelmäßige Intervalle in beiden Richtungen. "
            "Linie 50A: Unregelmäßige Intervalle in beiden Richtungen. "
            "Grund: Verkehrsstörung.",
            "Unregelmäßige Intervalle in beiden Richtungen. "
            "Grund: Verkehrsstörung.",
        ),
        (
            "Linie 1A: Derzeit ist ein Betrieb nicht möglich. "
            "Linie 2A: Derzeit ist ein Betrieb nicht möglich. "
            "Linie 3A: Derzeit ist ein Betrieb nicht möglich. "
            "Grund: Veranstaltung im Bereich Ringstraße.",
            "Derzeit ist ein Betrieb nicht möglich. "
            "Grund: Veranstaltung im Bereich Ringstraße.",
        ),
        (
            "Linie 79B: Unregelmäßige Intervalle in beiden Richtungen. "
            "Linie 79A: Unregelmäßige Intervalle in beiden Richtungen. "
            "Grund: Verkehrsunfall.",
            "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsunfall.",
        ),
    ],
)
def test_each_published_case(cached: str, expected: str) -> None:
    assert _published(cached) == expected


def test_the_reason_survives_the_180_limit() -> None:
    """The point of the fix: the item finally says WHY.

    Seven of the thirteen cases were long enough that the 180-character
    limit cut the ``Grund: …`` field off entirely — the repeats crowded
    out the only informative part.
    """
    cached = (
        "Linie 51B: Unregelmäßige Intervalle in beiden Richtungen. "
        "Linie 16A: Unregelmäßige Intervalle in beiden Richtungen. "
        "Linie 51A: Unregelmäßige Intervalle in beiden Richtungen. "
        "Linie 53A: Unregelmäßige Intervalle in beiden Richtungen. "
        "Grund: Verkehrsüberlastung."
    )
    before = _truncate_summary_180(_strip_wl_description_line_prefix(cached))
    after = _truncate_summary_180(_published(cached))

    assert before.endswith("…"), before
    assert "Grund" not in before
    assert after == (
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsüberlastung."
    )


# ---------------- what must NOT be touched ----------------


def test_segments_that_say_different_things_stay_whole() -> None:
    """The ``Linie X:`` prefix is the point when the texts differ.

    This is the guard that keeps the rule from eating real per-line
    instructions. Taken verbatim from the cache.
    """
    cached = (
        "Linie 46: Wird ab Joachimsthalerplatz über die Strecke der Linien 10 "
        "und 49 nach Hütteldorf, Bujattigasse verlängert. "
        "Linie 49: Kein Betrieb. "
        "Linie 52: Wird ab Westbahnhof über die Strecke der Linien 18 und 49 "
        "nach Parlament, U Volkstheater verlängert. "
        "Weitere Alternativen: U3, 9, 12A. "
        "Dauer: Bis 30.10.2026 Betriebsschluss. "
        "Grund: Gleisbauarbeiten im Bereich Märzstraße # Huglgasse."
    )
    out = _collapse_repeated_wl_line_segments(cached)

    assert out == cached
    assert "Linie 49: Kein Betrieb." in out
    assert "Linie 52:" in out


def test_a_date_is_never_a_segment_boundary() -> None:
    """``Bis 30.10.2026 Betriebsschluss`` must not split anything.

    The split only fires in front of a ``Linie X:`` prefix, so no general
    sentence splitting — and no abbreviation or date can confuse it.
    """
    cached = "Linie 49: Kein Betrieb. Dauer: Bis 30.10.2026 Betriebsschluss."
    assert _collapse_repeated_wl_line_segments(cached) == cached


@pytest.mark.parametrize(
    "cached",
    [
        "Linie 42: Fahrtbehinderung in Richtung Antonigasse. Grund: Falschparker.",
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsunfall.",
        "Achtung: Sperre wegen Bauarbeiten. Information: Umleitung.",
        "",
    ],
)
def test_without_two_line_segments_nothing_changes(cached: str) -> None:
    assert _collapse_repeated_wl_line_segments(cached) == cached


def test_a_repeat_without_a_line_prefix_is_left_alone() -> None:
    """The rule is about WL's per-line attribution, not about prose.

    A sentence that happens to repeat without a ``Linie X:`` in front of
    it is somebody's writing, not a mechanical restatement, and this rule
    has no business deciding it is redundant.
    """
    cached = "Kein Betrieb. Kein Betrieb."
    assert _collapse_repeated_wl_line_segments(cached) == cached


# ---------------- the empty location WL leaves behind ----------------


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        (
            "Grund: Verkehrsüberlastung im Bereich .",
            "Grund: Verkehrsüberlastung.",
        ),
        (
            "Grund: Schadhaftes Fahrzeug im Bereich .",
            "Grund: Schadhaftes Fahrzeug.",
        ),
    ],
)
def test_an_empty_location_slot_is_dropped(cached: str, expected: str) -> None:
    assert _tidy_wl_dangling_location(cached) == expected


def test_a_named_location_keeps_its_preposition() -> None:
    """Only the stray space goes — ``im Haltestellenbereich`` stays."""
    assert _tidy_wl_dangling_location(
        "Grund: Polizeieinsatz im Haltestellenbereich Atzgersdorfer Straße ."
    ) == "Grund: Polizeieinsatz im Haltestellenbereich Atzgersdorfer Straße."

    assert _tidy_wl_dangling_location(
        "Grund: Falschparker im Bereich Kreuzgasse 56."
    ) == "Grund: Falschparker im Bereich Kreuzgasse 56."


def test_a_stray_space_before_the_full_stop_goes() -> None:
    assert _tidy_wl_dangling_location(
        "Unregelmäßige Intervalle in Richtung Karlsplatz . Grund: Betriebsstörung."
    ) == "Unregelmäßige Intervalle in Richtung Karlsplatz. Grund: Betriebsstörung."


# ---------------- the rules must actually run ----------------


def test_the_rules_are_wired_into_the_wl_post_filter() -> None:
    """Isolated unit tests cannot tell whether anything calls these.

    Removing either call from ``_post_filter_wl`` leaves every test above
    passing — the same gap that slipped through in PR #1842.
    """
    out = _post_filter_wl(
        [
            _stoerung(
                "5B/49A/50A: Verkehrsstörung",
                "Linie 5B: Unregelmäßige Intervalle in beiden Richtungen. "
                "Linie 49A: Unregelmäßige Intervalle in beiden Richtungen. "
                "Grund: Verkehrsüberlastung im Bereich .",
            )
        ]
    )

    assert len(out) == 1
    desc = out[0]["description"]
    assert desc == (
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsüberlastung."
    ), desc
    assert "Linie 49A" not in desc
    assert "im Bereich" not in desc


@pytest.mark.parametrize(
    "token", ["5B", "49A", "U6", "D", "N20", "10", "N", "WLB", "Ersatzbus"]
)
def test_the_split_agrees_with_the_prefix_patterns(token: str) -> None:
    """What the prefix patterns call a line code, the split must too.

    This is the property the shared ``_WL_DESC_LINE_TOKEN`` exists to
    guarantee. If the two ever disagree, a segment the prefix stripper
    recognises stops being a boundary (or the other way round) and the
    collapse either misses a repeat or cuts in the wrong place.
    ``WLB`` and ``Ersatzbus`` are in the list because neither is a line
    code by that definition — the agreement has to hold for "no" as well
    as for "yes".
    """
    is_prefix = bool(
        build_feed._WL_DESC_LINIE_PREFIX_RE.match(f"Linie {token}: Kein Betrieb.")
    )
    is_boundary = (
        len(
            build_feed._WL_DESC_LINE_SEGMENT_SPLIT_RE.split(
                f"Kein Betrieb. Linie {token}: Kein Betrieb."
            )
        )
        == 2
    )
    assert is_prefix == is_boundary


def test_the_segment_split_references_the_shared_line_token() -> None:
    """Reuse, not a third copy — asserted on the source, not the pattern.

    A copy of the token text compiles to the identical pattern, so no
    behavioural test can tell the two apart today; the damage only shows
    up later, when one definition is changed and the other is not. So
    this one reads the assignment itself.
    """
    import ast

    source = pathlib.Path(build_feed.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        targets: list[ast.expr]
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "_WL_DESC_LINE_SEGMENT_SPLIT_RE" in names:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            assert "_WL_DESC_LINE_TOKEN" in segment, segment
            return
    pytest.fail("_WL_DESC_LINE_SEGMENT_SPLIT_RE nicht gefunden")
