from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import update_station_directory


def test_refresh_provider_caches_timeout(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Verify that a subprocess timeout is caught and logged gracefully."""

    # Mock sys.executable and Path.exists to satisfy the script discovery logic
    monkeypatch.setattr(sys, "executable", "python3")

    # We need to mock Path.exists to return True for at least one candidate
    original_exists = Path.exists
    def mock_exists(self: Path) -> bool:
        if self.name == "update_oebb_cache.py":
            return True
        return original_exists(self)
    monkeypatch.setattr(Path, "exists", mock_exists)

    # Mock subprocess.run to raise TimeoutExpired
    def mock_run(*args, **kwargs):
        assert kwargs.get("timeout") == 300
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=300)

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Run the function
    # We pass a fake script_dir to ensure we look for scripts in a known location
    # but the mock_exists handles the finding part.
    # The function uses Path(__file__).parent by default, so we can just call it.

    # However, we need to ensure at least one target is "available"
    # The default targets include ÖBB which has no extra availability check.

    with caplog.at_level("WARNING"):
        update_station_directory._refresh_provider_caches()

    # Verify the log message
    assert "ÖBB cache refresh timed out after 300s; continuing" in caplog.text
