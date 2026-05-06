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


def test_main_uses_fallback_when_remote_fails(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_resolve_fallback_path_default_when_unset() -> None:
    assert update_baustellen_cache._resolve_fallback_path(None) == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    assert update_baustellen_cache._resolve_fallback_path("") == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    assert update_baustellen_cache._resolve_fallback_path("   ") == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_accepts_repo_relative() -> None:
    """Paths inside the repo (the legitimate use case) must be honoured."""
    relative = SAMPLE_PATH.relative_to(update_baustellen_cache.REPO_ROOT).as_posix()
    resolved = update_baustellen_cache._resolve_fallback_path(relative)
    assert resolved == SAMPLE_PATH


def test_resolve_fallback_path_blocks_outside_repo(tmp_path: Path) -> None:
    """An env-controlled absolute path outside the repo must be rejected."""
    outside = tmp_path / "evil.json"
    outside.write_text('{"features": []}', encoding="utf-8")
    resolved = update_baustellen_cache._resolve_fallback_path(str(outside))
    assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_blocks_traversal_via_dotdot() -> None:
    """A relative path that escapes the repo via ../ must be rejected."""
    resolved = update_baustellen_cache._resolve_fallback_path("../../etc/passwd")
    assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH


def test_resolve_fallback_path_blocks_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the repo pointing outside must be rejected by resolve()."""
    target = tmp_path / "outside.json"
    target.write_text('{"features": []}', encoding="utf-8")
    link = update_baustellen_cache.REPO_ROOT / "data" / "samples" / "_pytest_symlink_test.json"
    link.symlink_to(target)
    try:
        resolved = update_baustellen_cache._resolve_fallback_path(
            str(link.relative_to(update_baustellen_cache.REPO_ROOT))
        )
        assert resolved == update_baustellen_cache.DEFAULT_FALLBACK_PATH
    finally:
        link.unlink(missing_ok=True)
