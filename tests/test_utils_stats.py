"""Tests for ``src.utils.stats``.

Covers the append-only CSV writers and the location-name extraction
heuristic. The writers are exercised against ``tmp_path`` so the
production CSV path is never touched.
"""
from __future__ import annotations

import csv
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import stats as stats_utils  # noqa: E402

VIENNA_TZ = ZoneInfo("Europe/Vienna")


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return ``(header, data_rows)`` from a CSV path."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], []
    return rows[0], rows[1:]


# ---- to_vienna ------------------------------------------------------------


def test_to_vienna_passes_through_aware_vienna_datetime() -> None:
    when = datetime(2026, 5, 4, 12, 0, tzinfo=VIENNA_TZ)
    assert stats_utils.to_vienna(when) == when


def test_to_vienna_converts_utc_to_vienna() -> None:
    when_utc = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    converted = stats_utils.to_vienna(when_utc)
    assert converted.tzinfo is not None
    # In May, Vienna is UTC+02:00 → 10:00 UTC = 12:00 Vienna
    assert converted.hour == 12


def test_to_vienna_localises_naive_datetime() -> None:
    naive = datetime(2026, 5, 4, 12, 0)
    converted = stats_utils.to_vienna(naive)
    assert converted.tzinfo is not None
    assert converted.year == 2026


# ---- Stammstrecke writer --------------------------------------------------


def test_append_stammstrecke_row_writes_header_on_first_call(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    ok = stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=5.5,
        stats_dir=tmp_path,
    )
    assert ok is True
    path = tmp_path / "stammstrecke_2026.csv"
    header, rows = _read_csv(path)
    assert tuple(header) == stats_utils.STAMMSTRECKE_HEADER
    assert rows == [["2026-05-04T07:30:00+02:00", "Mo", "07", "Meidling", "5.50"]]


def test_append_stammstrecke_row_appends_without_rewriting_header(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when, direction="Meidling", delay_minutes=5.5, stats_dir=tmp_path
    )
    stats_utils.append_stammstrecke_row(
        timestamp=when, direction="Floridsdorf", delay_minutes=12.0, stats_dir=tmp_path
    )
    path = tmp_path / "stammstrecke_2026.csv"
    header, rows = _read_csv(path)
    assert tuple(header) == stats_utils.STAMMSTRECKE_HEADER
    assert len(rows) == 2
    assert rows[0][3] == "Meidling"
    assert rows[1][3] == "Floridsdorf"


def test_append_stammstrecke_row_rolls_over_at_year_boundary(tmp_path: Path) -> None:
    """A timestamp in 2027 must land in ``stammstrecke_2027.csv``."""
    a = datetime(2026, 12, 31, 23, 59, tzinfo=VIENNA_TZ)
    b = datetime(2027, 1, 1, 0, 5, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=a, direction="Meidling", delay_minutes=1.0, stats_dir=tmp_path
    )
    stats_utils.append_stammstrecke_row(
        timestamp=b, direction="Meidling", delay_minutes=2.0, stats_dir=tmp_path
    )
    assert (tmp_path / "stammstrecke_2026.csv").exists()
    assert (tmp_path / "stammstrecke_2027.csv").exists()


def test_append_stammstrecke_row_returns_false_on_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulated OSError must be swallowed (returns False), not raised."""

    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(Path, "mkdir", _raise)
    ok = stats_utils.append_stammstrecke_row(
        timestamp=datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ),
        direction="Meidling",
        delay_minutes=5.0,
        stats_dir=tmp_path / "nope",
    )
    assert ok is False


# ---- Disruption writer ----------------------------------------------------


def test_append_disruption_row_persists_provider_and_location(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    ok = stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    assert ok is True
    header, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert tuple(header) == stats_utils.STOERUNGEN_HEADER
    assert rows == [
        ["2026-05-04T07:30:00+02:00", "Mo", "07", "ÖBB", "Wien Floridsdorf"]
    ]


def test_append_disruption_row_normalises_blank_fields(tmp_path: Path) -> None:
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="   ",
        location_name="",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows[0][3] == "unbekannt"
    assert rows[0][4] == "unbekannt"


# ---- Location heuristic ---------------------------------------------------


def test_extract_location_name_uses_zwischen_pattern() -> None:
    item = {
        "title": "S 7: Verspätung",
        "description": "Verspätungen zwischen Floridsdorf und Praterstern wegen Bauarbeiten.",
    }
    assert stats_utils.extract_location_name(item) == "Floridsdorf"


def test_extract_location_name_falls_back_to_wien_prefix() -> None:
    item = {
        "title": "ÖBB: Information",
        "description": "Streckensperre rund um Wien Meidling bis Vormittag.",
    }
    assert stats_utils.extract_location_name(item) == "Wien Meidling"


def test_extract_location_name_returns_unbekannt_when_nothing_matches() -> None:
    item = {"title": "", "description": ""}
    assert stats_utils.extract_location_name(item) == "unbekannt"


def test_extract_location_name_skips_stopwords_like_bauarbeiten() -> None:
    item = {
        "title": "Bauarbeiten",
        "description": "Bauarbeiten heute, Karlsplatz betroffen.",
    }
    extracted = stats_utils.extract_location_name(item)
    assert extracted != "Bauarbeiten"
    assert "Karlsplatz" in extracted


def test_extract_location_name_caps_overly_long_strings() -> None:
    item = {
        "title": "X" * 5000,
        "description": "Wien " + ("a" * 200),
    }
    out = stats_utils.extract_location_name(item)
    assert len(out) <= 90  # 80-char location cap + small "Wien " prefix slack


def test_extract_location_name_handles_non_string_inputs_safely() -> None:
    item: dict[str, object] = {"title": None, "description": 12345}
    assert stats_utils.extract_location_name(item) == "unbekannt"


# ---- Path helper ----------------------------------------------------------


def test_stats_path_uses_default_dir_when_unspecified() -> None:
    path = stats_utils.stats_path("stammstrecke", 2026)
    assert path.name == "stammstrecke_2026.csv"
    assert path.parent == stats_utils.DEFAULT_STATS_DIR


def test_stats_path_honours_base_dir_override(tmp_path: Path) -> None:
    path = stats_utils.stats_path("stoerungen", 2027, base_dir=tmp_path)
    assert path == tmp_path / "stoerungen_2027.csv"
