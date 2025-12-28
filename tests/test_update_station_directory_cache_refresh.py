from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import update_station_directory as usd


def _make_result(code: int = 0) -> object:
    class _Result:
        returncode = code

    return _Result()


def test_refresh_provider_caches_runs_available_scripts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script_path = tmp_path / "update_dummy_cache.py"
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool, **kwargs: object) -> object:
        calls.append(command)
        return _make_result()

    monkeypatch.setattr(usd.subprocess, "run", fake_run)
    monkeypatch.setattr(
        usd,
        "_CACHE_REFRESH_TARGETS",
        (
            usd.CacheRefreshTarget(
                "Dummy",
                ("update_dummy_cache.py",),
                extra_args_factory=lambda: ("--flag",),
            ),
        ),
    )

    usd._refresh_provider_caches(script_dir=tmp_path)

    assert calls == [[sys.executable, str(script_path), "--flag"]]


def test_refresh_provider_caches_skips_missing_optional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool, **kwargs: object) -> object:
        calls.append(command)
        return _make_result()

    monkeypatch.setattr(usd.subprocess, "run", fake_run)
    monkeypatch.setattr(
        usd,
        "_CACHE_REFRESH_TARGETS",
        (
            usd.CacheRefreshTarget("Optional", ("missing.py",), optional=True),
        ),
    )

    usd._refresh_provider_caches(script_dir=tmp_path)

    assert calls == []


def test_refresh_provider_caches_respects_availability_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script_path = tmp_path / "update_dummy_cache.py"
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool, **kwargs: object) -> object:
        calls.append(command)
        return _make_result()

    monkeypatch.setattr(usd.subprocess, "run", fake_run)
    monkeypatch.setattr(
        usd,
        "_CACHE_REFRESH_TARGETS",
        (
            usd.CacheRefreshTarget(
                "Unavailable",
                ("update_dummy_cache.py",),
                availability_check=lambda: False,
            ),
        ),
    )

    usd._refresh_provider_caches(script_dir=tmp_path)

    assert calls == []
