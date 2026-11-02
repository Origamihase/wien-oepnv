"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_stammstrecke_direction_health.py.
"""

from __future__ import annotations

import csv
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pytest
from zoneinfo import ZoneInfo

from src.utils import stats as stats_mod
from src.utils.stats import (
    STAMMSTRECKE_HEADER,
    WEEKDAY_LABELS,
)

VIENNA = ZoneInfo("Europe/Vienna")


def _bound_stub(real: Callable[..., Any], result: Any) -> Callable[..., Any]:
    """A stand-in for a health check that takes only what the real one takes."""
    signature = inspect.signature(real)

    def stub(*args: Any, **kwargs: Any) -> Any:
        signature.bind(*args, **kwargs)
        return result

    return stub

DIRECTIONS = ("Meidling", "Praterstern")

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=VIENNA)


def _write_ledger(stats_dir: Path, rows: list[tuple[datetime, str]]) -> None:
    """Write a ``stammstrecke_<year>.csv`` containing *rows* (timestamp, direction)."""
    stats_dir.mkdir(parents=True, exist_ok=True)
    by_year: dict[int, list[tuple[datetime, str]]] = {}
    for ts, direction in rows:
        by_year.setdefault(ts.year, []).append((ts, direction))
    for year, year_rows in by_year.items():
        path = stats_dir / f"stammstrecke_{year:04d}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(STAMMSTRECKE_HEADER)
            for ts, direction in sorted(year_rows):
                writer.writerow(
                    [
                        ts.isoformat(),
                        WEEKDAY_LABELS[ts.weekday()],
                        f"{ts.hour:02d}",
                        direction,
                        "0.0",
                    ]
                )


def _ticks(end: datetime, count: int, direction: str, *, step_min: int = 30) -> list[tuple[datetime, str]]:
    """``count`` rows for *direction*, every ``step_min`` minutes, ending at *end*."""
    return [(end - timedelta(minutes=step_min * i), direction) for i in range(count)]


@pytest.fixture()
def stats_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "stats"
    folder.mkdir()
    monkeypatch.setattr(stats_mod, "DEFAULT_STATS_DIR", folder)
    return folder


# --- monitor-script integration --------------------------------------------


def _degraded_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """DEGRADED lines captured on the monitor's logger.

    Uses ``record.getMessage()`` rather than ``record.message``: the latter is
    only populated once some handler has formatted the record, which depends on
    what the rest of the session left attached to the root logger. Interpolating
    here is deterministic regardless of handler state.
    """
    return [
        rec.getMessage()
        for rec in caplog.records
        if "DEGRADED" in rec.getMessage()
    ]


# --- published-figure transparency (audit B.1, Aufgabe 1.2) -----------------


def _sm_row(ts: datetime, direction: str, delay: float = 0.0) -> Any:
    from scripts.generate_markdown_stats import StammstreckeRow

    return StammstreckeRow(
        timestamp=ts,
        weekday=WEEKDAY_LABELS[ts.weekday()],
        hour=ts.hour,
        direction=direction,
        delay_minutes=delay,
    )


def _peer_run(now: datetime, direction: str, count: int, *, step_minutes: int = 20) -> list[Any]:
    """*count* journeys in *direction*, newest ``step_minutes`` before *now*."""
    return [
        _sm_row(now - timedelta(minutes=step_minutes * (i + 1)), direction)
        for i in range(count)
    ]


__all__ = [
    "DIRECTIONS",
    "NOW",
    "VIENNA",
    "_bound_stub",
    "_degraded_records",
    "_peer_run",
    "_sm_row",
    "_ticks",
    "_write_ledger",
    "stats_dir",
]
