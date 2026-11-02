"""The changelog lives in day files under ``changelog/``; CHANGELOG.md is the index.

Since 2026-11-02 no file may have more than 700 lines
(``tests/test_file_line_limit.py``). The changelog grows by about 260 lines a
day, so every day gets its own file ``changelog/<JJJJ-MM-TT>.md`` (Michael
2026-11-02, like the daily audit files). Entries from May 2026 without a day
in their title live in ``2026-05-ohne-tag.md``, the old version sections in
``bis-2026-05.md``. ``CHANGELOG.md`` only lists the files. This test keeps
the index and the folder in step and stops entries from landing in the index.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INDEX = _ROOT / "CHANGELOG.md"
_DIR = _ROOT / "changelog"
_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")
_OTHER = re.compile(r"^(\d{4}-\d{2}-ohne-tag|bis-2026-05)\.md$")
_LINK_RE = re.compile(r"\]\(changelog/([^)]+)\)")
_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _links() -> list[str]:
    return _LINK_RE.findall(_INDEX.read_text(encoding="utf-8"))


def test_index_links_every_file_once_and_nothing_else() -> None:
    links = _links()
    assert len(links) == len(set(links)), "Eine Datei steht doppelt in CHANGELOG.md"
    on_disk = sorted(p.name for p in _DIR.glob("*.md"))
    assert sorted(links) == on_disk
    assert not [p.name for p in _DIR.iterdir() if p.suffix != ".md"]


def test_index_holds_no_entries() -> None:
    text = _INDEX.read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.startswith("* ")]
    assert not entries, "Einträge gehören in changelog/<JJJJ-MM-TT>.md des Tages:\n" + "\n".join(entries)


def test_file_names_are_days_and_days_are_listed_newest_first() -> None:
    days = []
    for name in _links():
        match = _DAY_RE.match(name)
        if match is None:
            assert _OTHER.match(name), f"Unerwarteter Dateiname: {name}"
            continue
        dt.date.fromisoformat(match[1])
        days.append((match[1], int(match[2] or 1)))
    assert days
    assert days == sorted(days, reverse=True), "Tage in CHANGELOG.md nicht absteigend"


def test_day_files_carry_their_weekday_heading() -> None:
    for path in _DIR.glob("*.md"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# Changelog "), path.name
        match = _DAY_RE.match(path.name)
        if match is not None:
            day = dt.date.fromisoformat(match[1])
            assert first.startswith(f"# Changelog {_WEEKDAYS[day.weekday()]}, {day.day}. "), path.name
