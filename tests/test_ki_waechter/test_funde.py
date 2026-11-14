# ruff: noqa: S603, S607
"""Mechanische Prüfung der Funde (``verify``): wörtliche Zitate, Dubletten, Zeitzeile,
Kürzungen, derselbe Fund nur einmal.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import ki_waechter as kw
from src.ki_waechter.inputs import MAX_SOURCE_CHARS

from ._support import (
    _answer,
    _history,
    _write_feeds,
    _write_repo,
)


def test_a_verbatim_quote_counts(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "closure of the algae path",
        "erwartet": "closure of Algenweg", "begruendung": "Straßenname übersetzt.",
    }), prep)
    assert [(f.nr, f.klasse) for f in verdict.findings] == [(2, "F6")]
    assert not verdict.rejected


@pytest.mark.parametrize(
    ("finding", "reason"),
    [
        ({"nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "closure of the seaweed path"}, "nicht wörtlich"),
        ({"nr": 2, "klasse": "F6", "feed": "DE", "feed_zitat": "Algenwegs"}, "nur für den englischen"),
        ({"nr": 2, "klasse": "F3", "feed": "DE", "feed_zitat": "Streckensperre", "quelle_zitat": "Gleisschaden"}, "Quellzitat"),
        ({"nr": 1, "klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten"}, "zweite"),
        ({"nr": 7, "klasse": "F2", "feed": "DE", "feed_zitat": "Busse"}, "Meldungsnummer"),
        ({"nr": 1, "klasse": "F9", "feed": "DE", "feed_zitat": "Busse halten"}, "gibt es nicht"),
        ({"nr": 1, "klasse": "F5", "feed": "DE", "feed_zitat": "Bu"}, "nicht wörtlich"),
        ({"nr": 1, "klasse": "F4", "feed": "EN", "feed_zitat": "Construction works"}, "nur für den deutschen"),
        ({"nr": 1, "klasse": "F5", "feed": "DE", "feed_zitat": "Busse halten …"}, "Kürzung"),
        ({"nr": 1, "klasse": "F2", "feed": "DE", "feed_zitat": "Busse halten"}, "vollständigen Satz"),
        ({"nr": 1, "klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten", "nr2": 2, "feed_zitat2": "Streckensperre"},
         "verschiedene Linien"),
    ],
)
def test_an_unproven_finding_is_rejected(prep: kw.Prepared, finding: dict[str, object], reason: str) -> None:
    verdict = kw.verify(_answer({"erwartet": "x", "begruendung": "y", **finding}), prep)
    assert not verdict.findings
    assert reason in verdict.rejected[0].grund


def test_a_duplicate_needs_both_quotes(prep: kw.Prepared) -> None:
    base = {"nr": 1, "klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße 39",
            "nr2": 3, "erwartet": "eine Meldung", "begruendung": "dieselbe Haltestelle"}
    ok = kw.verify(_answer({**base, "feed_zitat2": "Liechtensteinstraße 42 → Liechtensteinstraße 38"}), prep)
    assert [(f.nr, f.nr2) for f in ok.findings] == [(1, 3)]
    # The same pair named from the other item (the second answer, or both items of one answer) is one finding.
    both = kw.verify(_answer(
        {**base, "feed_zitat2": "Liechtensteinstraße 42 → Liechtensteinstraße 38"},
        {**base, "nr": 3, "nr2": 1, "feed_zitat": "Liechtensteinstraße 42 → Liechtensteinstraße 38",
         "feed_zitat2": "Busse halten Liechtensteinstraße 39"},
    ), prep)
    assert [(f.nr, f.nr2) for f in both.findings] == [(1, 3)] and not both.rejected
    bad = kw.verify(_answer({**base, "feed_zitat2": "Liechtensteinstraße 99"}), prep)
    assert not bad.findings and "zweiten Meldung" in bad.rejected[0].grund
    # A pair has no text to expect; the script fills the field (run 288, 05.11.2026). Other classes still need it.
    empty = kw.verify(_answer({**base, "erwartet": "", "feed_zitat2": "Liechtensteinstraße 42 → Liechtensteinstraße 38"}), prep)
    assert [f.erwartet for f in empty.findings] == ["nur eine Meldung"] and not empty.rejected
    other = kw.verify(_answer({"nr": 1, "klasse": "F5", "feed": "DE", "feed_zitat": "Busse halten",
                               "erwartet": " ", "begruendung": "b"}), prep)
    assert not other.findings and "erwarteter Text" in other.rejected[0].grund


def test_a_wrong_fact_needs_a_verbatim_source_quote_and_feed_words_no_source_has(prep: kw.Prepared) -> None:
    fact = {"nr": 2, "klasse": "F3", "feed": "DE", "feed_zitat": "einer Streckensperre",
            "quelle_zitat": "angeordneten Streckensperre", "erwartet": "x", "begruendung": "y"}
    assert len(kw.verify(_answer(fact), prep).findings) == 1
    # The feed's words standing in a source are the source's fact: an F3 needs a source that reads differently.
    same = kw.verify(_answer({**fact, "feed_zitat": "Streckensperre des Algenwegs"}), prep)
    assert not same.findings and "steht so in einer Quelle" in same.rejected[0].grund


def test_the_same_finding_counts_once(prep: kw.Prepared) -> None:
    """One finding per item, class and feed: the same quote twice, or the same error quoted at another length."""
    one = {"nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path", "erwartet": "Algenweg", "begruendung": "b"}
    assert len(kw.verify(_answer(one, dict(one)), prep).findings) == 1
    longer = {**one, "feed_zitat": "closure of the algae path, line 89A", "erwartet": "closure of the Algenweg, line 89A"}
    verdict = kw.verify(_answer(one, longer), prep)
    assert [f.feed_zitat for f in verdict.findings] == ["algae path"] and not verdict.rejected
    # Another class of the same item is another finding.
    assert len(kw.verify(_answer(one, {**one, "klasse": "F5"}), prep).findings) == 2


def test_model_text_is_scrubbed(prep: kw.Prepared) -> None:
    verdict = kw.verify(_answer({
        "nr": 2, "klasse": "F6", "feed": "EN", "feed_zitat": "algae path",
        "erwartet": "Algenweg ![x](https://example.org/p.png)",
        "begruendung": "sk-ant-oat01-abcdefghijklmnop AIzaSyA1234567890abcdefghijk <script>",
    }), prep)
    finding = verdict.findings[0]
    assert "sk-ant" not in finding.begruendung and "AIza" not in finding.begruendung and "<" not in finding.begruendung
    assert "![" not in finding.erwartet and "](" not in finding.erwartet


def test_a_quote_from_the_time_line_and_a_cut_behind_the_measure_are_rejected(tmp_path: Path) -> None:
    """The time line is the code's, not a finding (run 302); a cut behind the measure is decided (runs 296, 302)."""
    _write_repo(tmp_path)
    rows = [("7: Bauarbeiten", "Wegen Bauarbeiten kommt es zu Umleitungen bei der Linie 7. Maßnahmen: Linie 7 … [Ab Di 27.10.]", "g1"),
            ("S1: Bauarbeiten", "Wegen Bauarbeiten können am 27.10. der S1-Zug … [Am Di 27.10.]", "g2")]
    _write_feeds(tmp_path, rows, rows)
    read, listing = kw.working_tree_reader(tmp_path)
    prep = kw.prepare([kw.Snapshot("abc1234", "", read, listing)], "abc1234")
    base = {"feed": "DE", "erwartet": "x", "begruendung": "y"}
    cut = kw.verify(_answer({**base, "nr": 1, "klasse": "F2", "feed_zitat": "Linie 7 …"}, geprueft=(1, 2)), prep)
    assert not cut.findings and "Kernaussage „Umleitungen“" in cut.rejected[0].grund
    day = kw.verify(_answer({**base, "nr": 1, "klasse": "F3", "feed_zitat": "Di 27.10.", "quelle_zitat": "Bauarbeiten"},
                            geprueft=(1, 2)), prep)
    assert not day.findings and "Zeitzeile" in day.rejected[0].grund
    # A text cut before its measure stays F2; a day that also stands in the body is no time-line quote.
    core = kw.verify(_answer({**base, "nr": 2, "klasse": "F2", "feed_zitat": "der S1-Zug …"}, geprueft=(1, 2)), prep)
    assert [f.klasse for f in core.findings] == ["F2"]
    body = kw.verify(_answer({**base, "nr": 2, "klasse": "F3", "feed_zitat": "am 27.10.", "quelle_zitat": "Streckensperre"},
                             geprueft=(1, 2)), prep)
    assert [f.klasse for f in body.findings] == ["F3"]


def test_a_cut_source_is_marked_and_a_quote_at_the_cut_is_at_most_f2(tmp_path: Path) -> None:
    """A source text ends with „[…]“ after MAX_SOURCE_CHARS; a feed quote ending at the „…“ is only F2 (05.11.2026)."""
    _write_repo(tmp_path)
    rows = [("U4: Schadhaftes Fahrzeug", "Wegen eines Polizeieinsatzes kommt es zu …", "g1")]
    _write_feeds(tmp_path, rows, rows)
    long_text = " ".join(f"Wort{i}" for i in range(600))
    (tmp_path / "cache" / "wl_x" / "events.json").write_text(
        json.dumps([{"guid": "g1", "title": "U4: Schadhaftes Fahrzeug", "description": long_text}]), encoding="utf-8")
    read, listing = kw.working_tree_reader(tmp_path)
    prep = kw.prepare([kw.Snapshot("abc1234", "", read, listing)], "abc1234")
    source = prep.sources[prep.items[0].own_sources[0]]["text"]
    assert source.endswith(" […]") and len(source) == MAX_SOURCE_CHARS + 4 and "[…]" in kw.render_input(prep)
    cut = {"nr": 1, "feed": "DE", "feed_zitat": "kommt es zu …", "erwartet": "x", "begruendung": "y"}
    assert [f.klasse for f in kw.verify(_answer({**cut, "klasse": "F2"}, geprueft=(1,)), prep).findings] == ["F2"]
    rejected = kw.verify(_answer({**cut, "klasse": "F5"}, geprueft=(1,)), prep).rejected
    assert "Kürzung" in rejected[0].grund


def test_a_duplicate_must_have_stood_in_the_feed_at_the_same_time(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    base = {"klasse": "F1", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße 39",
            "feed_zitat2": "Busse halten Liechtensteinstraße 39 nicht", "erwartet": "x", "begruendung": "y"}
    together = kw.verify(_answer({**base, "nr": 2, "nr2": 3}), prep)
    assert [(f.nr, f.nr2) for f in together.findings] == [(2, 3)]
    apart = kw.verify(_answer({**base, "nr": 1, "nr2": 3}), prep)
    assert not apart.findings and "nie gleichzeitig" in apart.rejected[0].grund
    same_guid = kw.verify(_answer({**base, "nr": 1, "nr2": 2, "feed_zitat2": "Seit 14:00"}), prep)
    assert not same_guid.findings


def test_one_error_in_two_texts_of_an_item_counts_once(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    one = {"klasse": "F5", "feed": "DE", "feed_zitat": "Busse halten Liechtensteinstraße", "erwartet": "x",
           "begruendung": "y"}
    verdict = kw.verify(_answer({**one, "nr": 1}, {**one, "nr": 2}), prep)
    assert len(verdict.findings) == 1
