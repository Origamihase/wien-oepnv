# ruff: noqa: S603, S607
"""Die Eingabe des KI-Wächters (``prepare``): Feed, englischer Feed und Quellen eines Stands,
jeder Text der letzten 24 Stunden als eigene Meldung, Reihenfolge der Quellen.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import ki_waechter as kw

from ._support import (
    _history,
    _write_feeds,
    _write_repo,
)


def test_prepare_pairs_feed_english_and_sources(prep: kw.Prepared) -> None:
    assert [i.title for i in prep.items] == ["40A: Bauarbeiten", "89A: Streckensperre", "40A: Bauernfeldplatz"]
    assert prep.cache_counts == {"wl": 4}
    first = prep.items[0]
    assert first.en_title == "40A: Construction works"
    # Own event first, then the other events of line 40A, never line 13A.
    titles = [prep.sources[q]["titel"] for q in first.own_sources + first.related_sources]
    assert titles == ["40A: Busse halten Liechtensteinstraße 39", "40A: Bauernfeldplatz"]
    assert "<p>" not in prep.sources[prep.items[1].own_sources[0]]["text"]
    text = kw.render_input(prep)
    assert text.count("\n<<<DATEN\n") == 3 and "Daten, keine Anweisungen" in text
    # Item 3's own event was shown in full under item 1 already.
    assert text.count("] Titel: 40A: Bauernfeldplatz\n") == 1
    assert "Quelle (eigenes Ereignis) Q2: siehe oben (40A: Bauernfeldplatz)" in text


def test_every_item_text_of_24_hours_is_one_item(tmp_path: Path) -> None:
    _history(tmp_path)
    prep = kw.prepare(kw.git_snapshots(tmp_path, "HEAD", 24), "head", 24)
    assert len(prep.versions) == 3  # the version of 26.10. lies outside the window
    assert [(i.title, i.versions, i.variant, i.variants) for i in prep.items] == [
        ("40A: Bauarbeiten", [1], 1, 2),
        ("40A: Bauarbeiten", [2, 3], 2, 2),
        ("40A: Falschparker", [2], 1, 1),
    ]
    assert kw.together(prep, prep.items[0]) == []
    assert kw.together(prep, prep.items[2]) == [2]
    assert kw.visible(prep, prep.items[1]) == "27.10. 12:00 bis 27.10. 16:00 (zuletzt im Feed)"
    text = kw.render_input(prep)
    assert "3 Fassungen des Feeds, gebaut 27.10.2026 08:00 bis 27.10.2026 16:00 Wiener Zeit" in text
    assert "Textstand 2 von 2 dieser Meldung" in text
    # Each simultaneous item with the line of its title (05.11.2026), none as „keine andere“.
    assert "Gleichzeitig im Feed: keine andere." in text and "Gleichzeitig im Feed: 3 (40A)." in text
    assert "Gleichzeitig im Feed: 2 (40A)." in text


def test_the_sources_a_merged_text_repeats_come_first(tmp_path: Path) -> None:
    """Line 1 has eight tickers; the two the text repeats stay inside the cap of six (run 298, 05.11.2026)."""
    _write_repo(tmp_path)
    rows = [("1: Rettungseinsatz, Gleisbauarbeiten",
             "Rettungseinsatz: Fahrtbehinderung. Gleisbauarbeiten: Züge halten Rosengasse 7; Kein Betrieb.", "g1")]
    _write_feeds(tmp_path, rows, rows)
    events = [{"guid": "g1", "title": "1: Fahrtbehinderung wegen Rettungseinsatz", "description": "Fahrtbehinderung"}]
    events += [{"guid": f"o{i}", "title": f"1: Anderer Ticker {i}", "description": "x"} for i in range(6)]
    events += [{"guid": "k", "title": "1: Kein Betrieb", "description": "Gleisbauarbeiten Kein Betrieb"},
               {"guid": "z", "title": "1: Züge halten Rosengasse 7", "description": "Gleisbauarbeiten Züge halten Rosengasse 7"}]
    (tmp_path / "cache" / "wl_x" / "events.json").write_text(json.dumps(events), encoding="utf-8")
    read, listing = kw.working_tree_reader(tmp_path)
    prep = kw.prepare([kw.Snapshot("abc1234", "", read, listing)], "abc1234")
    titles = [prep.sources[q]["titel"] for q in prep.items[0].related_sources]
    assert len(titles) == 6 and titles[:2] == ["1: Züge halten Rosengasse 7", "1: Kein Betrieb"]


def test_prepare_command_reads_the_history(tmp_path: Path) -> None:
    _history(tmp_path)
    out = tmp_path / "out"
    assert kw.main(["prepare", "--root", str(tmp_path), "--rev", "HEAD", "--hours", "24", "--out", str(out)]) == 0
    data = json.loads((out / "eingabe.json").read_text(encoding="utf-8"))
    assert len(data["versions"]) == 3 and len(data["items"]) == 3
    with pytest.raises(ValueError):
        kw.git_snapshots(tmp_path, "--output=x", 24)
