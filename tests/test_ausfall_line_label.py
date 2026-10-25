"""A train number in the cancellation ledger counts as "ohne Linienangabe"."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.generate_markdown_stats import NO_LINE_LABEL, ausfall_line_label

ROOT = Path(__file__).resolve().parents[1]


def test_train_numbers_are_no_line() -> None:
    for name in ("S28700", "CJX2932", "REX2277", "S27016"):
        assert ausfall_line_label(name) == NO_LINE_LABEL
    for line in ("S1", "S45", "R81", "REX92", "CJX9", "R3"):
        assert ausfall_line_label(line) == line


def test_ledger_2026_holds_one_train_number() -> None:
    """Ten of the eleven train numbers got their line from the timetable (26.10.2026)."""
    with (ROOT / "data" / "stats" / "ausfaelle_2026.csv").open(encoding="utf-8") as fh:
        names = [row["line"] for row in csv.DictReader(fh)]
    corrected = {"S27016", "S28449", "S28280", "S28044", "S28809", "REX2277",
                 "CJX2904", "CJX2917", "CJX2932", "CJX2946"}
    assert not corrected & set(names)
    assert "S28700" in names, "the unclear one keeps the name VAO gave"


def test_site_translates_the_label() -> None:
    js = (ROOT / "docs" / "assets" / "site.js").read_text(encoding="utf-8")
    assert f'l === "{NO_LINE_LABEL}"' in js
