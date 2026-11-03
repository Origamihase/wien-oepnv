"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/scripts/test_update_stammstrecke_hbf.py.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts import update_stammstrecke_status as _legacy_script  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.providers import vor_quota  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ``VIENNA_TZ`` is sourced from the legacy ``/trip`` script (which the
# Hbf monitor re-uses for its shared ledger infrastructure). Tests
# import it from its actual home rather than via a re-export from the
# Hbf script — avoiding the ``from src.utils import logging as
# utils_logging`` pattern that CodeQL flagged on PR #1496.
VIENNA_TZ = _legacy_script.VIENNA_TZ


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_quota_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Redirect the VOR daily-quota counter to ``tmp_path``."""

    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor_quota, "REQUEST_COUNT_FILE", count_file)
    vor_provider._flush_quota_cache()
    yield count_file
    vor_provider._flush_quota_cache()


# ---- _collect_hbf_observations --------------------------------------------


def _dep(
    *,
    name: str = "S 1",
    direction: str = "Wien Meidling",
    sched_date: str = "2026-05-15",
    sched_time: str = "08:00:00",
    rt_time: str | None = "08:00:00",
    cancelled: bool = False,
    track: str | None = "1",
    rt_track: str | None = None,
) -> dict[str, Any]:
    """Build a synthetic ``/departureBoard`` Departure entry.

    Defaults to ``track="1"`` so existing tests (which pre-date the
    platform-level Stammstrecke gate) still produce observations under
    the new filter. Pass ``track=None`` for the no-platform-info case
    or ``track="5"`` to exercise the non-Stammstrecke-track drop path.
    The ``rt_track`` argument overrides ``track`` when populated,
    mirroring the VAO realtime-platform-change semantics.
    """

    entry: dict[str, Any] = {
        "name": name,
        "direction": direction,
        "date": sched_date,
        "time": sched_time,
    }
    if rt_time is not None:
        entry["rtTime"] = rt_time
    if cancelled:
        entry["cancelled"] = True
    if track is not None:
        entry["track"] = track
    if rt_track is not None:
        entry["rtTrack"] = rt_track
    return entry


__all__ = [
    "REPO_ROOT",
    "VIENNA_TZ",
    "_dep",
    "_isolated_quota_counter",
]
