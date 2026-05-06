"""Verify OUT_PATH_STATIONS is contained inside the project's allowed roots."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    """Import scripts/fetch_google_places_stations.py with project root on sys.path."""
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if "scripts.fetch_google_places_stations" in sys.modules:
        return importlib.reload(
            sys.modules["scripts.fetch_google_places_stations"]
        )
    return importlib.import_module("scripts.fetch_google_places_stations")


def test_out_path_default_when_unset() -> None:
    module = _load_module()
    resolved = module._resolve_stations_out_path(None)
    # Default lives under data/, the canonical allowed root for stations.json.
    assert resolved.name == "stations.json"
    assert "data" in resolved.parts


def test_out_path_default_when_blank() -> None:
    module = _load_module()
    resolved = module._resolve_stations_out_path("   ")
    assert resolved.name == "stations.json"


def test_out_path_accepts_repo_relative_data_path(tmp_path: Path) -> None:
    """Test fixtures legitimately point OUT_PATH_STATIONS at data/<tmp> — keep it working."""
    module = _load_module()
    project_root = Path(__file__).resolve().parents[2]
    base_dir = project_root / "data" / tmp_path.name
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        target = base_dir / "stations.json"
        resolved = module._resolve_stations_out_path(str(target))
        assert resolved == target.resolve()
    finally:
        # Cleanup — the integration test fixture creates similar dirs.
        for child in base_dir.iterdir():
            child.unlink()
        base_dir.rmdir()


@pytest.mark.parametrize(
    "candidate",
    [
        "/etc/passwd",
        "/tmp/stations.json",
        # Path traversal escaping the repo via ``..``.
        "../../etc/stations.json",
        # Inside the repo but in a NON-allowed subdir (e.g. src/, scripts/).
        "src/stations.json",
        "scripts/stations.json",
    ],
)
def test_out_path_rejects_paths_outside_allowed_roots(
    caplog: pytest.LogCaptureFixture, candidate: str
) -> None:
    """An env override that resolves outside ``data/``, ``docs/``, ``log/`` must fall back to the default."""
    import logging

    module = _load_module()
    caplog.set_level(logging.WARNING, logger="places.cli")
    resolved = module._resolve_stations_out_path(candidate)

    # Falls back to the default — the dangerous path is NEVER returned.
    assert "data" in resolved.parts
    assert resolved.name == "stations.json"
    assert any(
        "outside the allowed roots" in record.getMessage()
        for record in caplog.records
    )
