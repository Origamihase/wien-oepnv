"""Helpers of the tests of the yearly Baustellen ledger.

A cache event, its row, the ledger file and its records, and what the tests
of the hook in ``scripts/update_baustellen_cache.py`` need to run ``main()``.
Used by ``tests/test_baustellen_ledger.py``, its siblings
``tests/test_baustellen_ledger_*.py`` and ``tests/test_baustellen_rules.py``.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache
from src.utils.stats import VIENNA_TZ
from src.utils.stats_baustellen import (
    BAUSTELLEN_HEADER,
    append_baustellen_rows,
    baustelle_row,
)
from tests.test_update_baustellen_cache._support import _real_cache_file, _site


REAL_STATS_DIR = Path(__file__).resolve().parents[1] / "data" / "stats"
LEDGER_LOGGER = "utils.stats_baustellen"
SCRIPT_LOGGER = "update_baustellen_cache"

# Tuesday, 10 November 2026, 17:44:05 in Vienna (CET, +01:00).
NOW = datetime(2026, 11, 10, 17, 44, 5, tzinfo=VIENNA_TZ)
GUID = "1642f76cdb2a921752f24384fa1ad815176094f31dfa2f9d5130723b697b3205"
HEADER_LINE = ",".join(BAUSTELLEN_HEADER)
# An end in 2027, for a site that is entered in the files of 2026 and of 2027.
ENDS_2027 = "2027-03-31T00:00:00+02:00"
# A well-formed data line; ``{guid}`` is filled in by the tests.
LINE = "2026-11-10T17:44:05+01:00,Di,17,{guid},Titel,2026-07-28,,7,Straßenbau,Text"


def _event(**changes: Any) -> dict[str, Any]:
    """A cache event as ``serialize_for_cache`` leaves it (dates as ISO strings)."""
    event: dict[str, Any] = {
        "source": "Stadt Wien – Baustellen", "category": "Baustelle", "pubDate": "2026-07-28T00:00:00+02:00",
        "link": "https://www.wien.gv.at/verkehr/baustellen",
        "location": {"coordinates": {"lat": 48.2037, "lon": 16.3442}},
        "title": "Burggasse 67",
        "description": (
            "Die Haltestelle wird von Burggasse ONr. 67 nach Burggasse ONr. 69 verlegt. \n"
            "Beginn: 28.07.2026 00:00 Uhr \nGeplant bis: 30.10.2026 00:00 Uhr \n"
            "Maßnahme: Straßenbau \nBezirk: 7"
        ),
        "guid": GUID,
        "starts_at": "2026-07-28T00:00:00+02:00",
        "ends_at": "2026-10-30T00:00:00+01:00",
        "context": {"district": "7", "measure": "Straßenbau"},
    }
    event.update(changes)
    return event


def _row(event: dict[str, Any], seen_at: datetime = NOW) -> dict[str, str]:
    row = baustelle_row(event, seen_at=seen_at)
    assert row is not None
    return dict(zip(BAUSTELLEN_HEADER, row, strict=True))


def _ledger(folder: Path, year: int = 2026) -> Path:
    return folder / f"baustellen_{year}.csv"


def _records(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _raise_runtime_error(*_args: Any) -> bool:
    raise RuntimeError("Platte voll\nzweite Zeile")


def _real_ledgers() -> list[tuple[str, int, int]]:
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in REAL_STATS_DIR.glob("baustellen_*"))


_FEATURES = [
    _site("Rennweg von 33A bis 37", "Der linke Fahrstreifen wird gesperrt."),
    _site("Hauptstraße", "Die Buslinie 7A wird über die Rothenhofgasse umgeleitet."),
    _site("Gasse 1", "Die Haltestelle der Linie 13A wird verlegt."),
]


class _Run:
    """What one ``main()`` did: the cache writes and the ledger calls, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: list[str] = []
        self.cached: list[list[dict[str, Any]]] = []
        self.ledger: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        monkeypatch.setattr(update_baustellen_cache, "write_cache", self._write_cache)
        monkeypatch.setattr(update_baustellen_cache, "append_baustellen_rows", self._append)

    def _write_cache(self, _provider: str, items: list[dict[str, Any]], **_kw: Any) -> None:
        self.order.append("cache")
        self.cached.append(items)

    def _append(self, *args: Any, **kwargs: Any) -> int:
        """Record the call and let the real writer run (into ``tmp_path``, at ``NOW``, see ``_hook_at_now``)."""
        self.order.append("ledger")
        self.ledger.append((args, kwargs))
        return append_baustellen_rows(*args, now=NOW, **kwargs)


def _hook_at_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook's writer reads the clock; ``NOW`` keeps a result from depending on the day or year the test runs in."""
    monkeypatch.setattr(update_baustellen_cache, "append_baustellen_rows", lambda events: append_baustellen_rows(events, now=NOW))


def _live_answer(monkeypatch: pytest.MonkeyPatch, features: list[dict[str, Any]]) -> None:
    payload = {"type": "FeatureCollection", "features": features}
    monkeypatch.setattr(update_baustellen_cache, "_fetch_layers", lambda *_a: update_baustellen_cache._collect_events(payload))


def _written_ledger(tmp_path: Path) -> list[dict[str, str]]:
    """The records of the one ledger file ``main()`` wrote: the one of the year of ``NOW``."""
    [path] = (tmp_path / "stats").iterdir()
    assert path.name == "baustellen_2026.csv"
    return _records(path)


def _no_live_answer(monkeypatch: pytest.MonkeyPatch, *, cache_exists: bool, fallback: str) -> None:
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", lambda *_a: None)
    monkeypatch.setattr(update_baustellen_cache, "_log_endpoint_diagnostic", lambda *a, **k: None)
    monkeypatch.setattr(update_baustellen_cache, "_cache_exists", lambda: cache_exists)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", fallback)


def _real_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")
    _real_cache_file(monkeypatch, target)
    return target


__all__ = [
    "ENDS_2027",
    "GUID",
    "HEADER_LINE",
    "LEDGER_LOGGER",
    "LINE",
    "NOW",
    "REAL_STATS_DIR",
    "SCRIPT_LOGGER",
    "_FEATURES",
    "_Run",
    "_event",
    "_hook_at_now",
    "_ledger",
    "_live_answer",
    "_no_live_answer",
    "_raise_runtime_error",
    "_real_cache",
    "_real_ledgers",
    "_records",
    "_row",
    "_warnings",
    "_written_ledger",
]
