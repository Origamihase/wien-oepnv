"""A train number in the cancellation ledger counts as "ohne Linienangabe"."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import update_stammstrecke_hbf as monitor
from scripts.generate_markdown_stats import NO_LINE_LABEL, ausfall_line_label
from scripts.update_stammstrecke_status import _PendingTrip

ROOT = Path(__file__).resolve().parents[1]
VIENNA = ZoneInfo("Europe/Vienna")


def _trip(name: str, hhmm: str, *, cancelled: bool) -> _PendingTrip:
    when = datetime(2026, 7, 7, int(hhmm[:2]), int(hhmm[3:]), tzinfo=VIENNA)
    return _PendingTrip(
        direction="Praterstern",
        name=name,
        scheduled=when,
        latest_delay_minutes=0.0,
        last_seen_at=when,
        cancelled=cancelled,
    )


def _ledger_2026() -> list[dict[str, str]]:
    with (ROOT / "data" / "stats" / "ausfaelle_2026.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_train_numbers_are_no_line() -> None:
    for name in ("S28700", "CJX2932", "REX2277", "S27016"):
        assert ausfall_line_label(name) == NO_LINE_LABEL
    for line in ("S1", "S45", "R81", "REX92", "CJX9", "R3"):
        assert ausfall_line_label(line) == line


def test_train_number_twin_of_a_line_entry_is_the_same_train() -> None:
    """07.07.2026 14:03: VAO listed "CJX9" and "CJX2932", both cancelled."""
    trips = [
        _trip("CJX9", "14:03", cancelled=True),
        _trip("CJX2932", "14:03", cancelled=True),
        _trip("S2", "14:12", cancelled=False),
    ]
    assert [t.name for t in monitor._without_train_number_twins(trips)] == ["CJX9", "S2"]


def test_running_line_entry_outweighs_a_cancelled_twin() -> None:
    """06.07.2026 05:57: "S2" ran, its twin "S28700" said cancelled."""
    trips = [_trip("S28700", "05:57", cancelled=True), _trip("S2", "05:57", cancelled=False)]
    kept = monitor._without_train_number_twins(trips)
    assert [(t.name, t.cancelled) for t in kept] == [("S2", False)]


def test_train_number_without_twin_stays() -> None:
    trips = [_trip("CJX2904", "07:03", cancelled=True), _trip("S2", "07:12", cancelled=True)]
    assert monitor._without_train_number_twins(trips) == trips


def test_ledger_2026_holds_one_cancellation_per_departure() -> None:
    """The nine train-number twins left the ledger, two got their line (26.10.2026)."""
    rows = _ledger_2026()
    per_departure = Counter((row["timestamp"], row["direction"]) for row in rows)
    assert [key for key, count in per_departure.items() if count > 1] == []
    names = {row["line"] for row in rows}
    assert not {name for name in names if ausfall_line_label(name) == NO_LINE_LABEL}
    for stamp, line in (("2026-07-04T00:45:00+02:00", "S2"), ("2026-07-04T07:03:00+02:00", "CJX9")):
        assert any(r["timestamp"] == stamp and r["line"] == line for r in rows)


def test_site_translates_the_label() -> None:
    js = (ROOT / "docs" / "assets" / "site.js").read_text(encoding="utf-8")
    assert f'l === "{NO_LINE_LABEL}"' in js
