from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache


SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "samples" / "baustellen_sample.geojson"


@pytest.mark.parametrize(
    "duration, expected_start, expected_end",
    [
        ("2025-11-05/2025-11-20", date(2025, 11, 5), date(2025, 11, 20)),
        ("2025-01-01T00:00:00+01:00/2025-01-02T12:00:00+01:00", date(2025, 1, 1), date(2025, 1, 2)),
    ],
)
def test_parse_range_handles_duration(duration: str, expected_start: date, expected_end: date) -> None:
    properties = {"DAUER": duration}
    start, end = update_baustellen_cache._parse_range(properties)
    assert start is not None and start.date() == expected_start
    assert end is not None and end.date() == expected_end


def test_collect_events_from_sample_payload() -> None:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    events = update_baustellen_cache._collect_events(payload)
    assert len(events) == 2
    first = events[0]
    assert first["category"] == "Baustelle"
    assert first["context"]["district"] == "06"
    assert first["starts_at"].startswith("2025-10-01")
    assert "location" in first


def test_main_uses_fallback_when_remote_fails(monkeypatch) -> None:
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def fake_fetch_remote(url: str, timeout: int) -> None:
        return None

    def capture_cache(provider: str, items: list[dict[str, str]]) -> None:
        calls.append((provider, items))

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(update_baustellen_cache, "write_cache", capture_cache)
    monkeypatch.setenv("BAUSTELLEN_FALLBACK_PATH", str(SAMPLE_PATH))

    exit_code = update_baustellen_cache.main()

    assert exit_code == 0
    assert calls and calls[0][0] == "baustellen"
    assert len(calls[0][1]) == 2
