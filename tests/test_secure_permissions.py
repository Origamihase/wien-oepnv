import os
import stat
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

@pytest.fixture
def mock_feed_config(monkeypatch):
    """Mock the feed config to avoid environment validation issues."""
    with patch("feed.config.validate_path", side_effect=lambda p, n: Path(p).resolve()):
        yield

def test_save_state_secure_permissions(tmp_path, mock_feed_config):
    """Verify that _save_state creates files with secure permissions (0600)."""
    # Import inside the test to ensure patches are active
    from build_feed import _save_state

    # Define a state file path
    state_file = tmp_path / "secure_state.json"

    # Patch build_feed.STATE_FILE.
    # Since build_feed uses global STATE_FILE, we patch it there.
    # Note: _save_state calls _validate_path(STATE_FILE, ...)
    # _validate_path is imported from feed.config
    # We mocked feed.config.validate_path above.
    # But build_feed.py imports it at top level.
    # So we also need to make sure build_feed._validate_path calls our mock or the mock is applied before import.
    # Since build_feed is likely already imported by other tests, we rely on patch here.

    # We also need to patch build_feed._validate_path because it captured the original function
    # OR we patch where it is used.
    # The fixture mock_feed_config patches 'feed.config.validate_path'.
    # If build_feed does `from feed.config import validate_path`, then `build_feed.validate_path` is the old one.

    with patch("build_feed.STATE_FILE", state_file), \
         patch("build_feed._validate_path", side_effect=lambda p, n: Path(p).resolve()):

        test_state = {"test_item": {"first_seen": "2023-01-01T00:00:00+00:00"}}

        _save_state(test_state)

        assert state_file.exists()

        # Check permissions
        st = state_file.stat()
        mode = st.st_mode

        # Check that group and others have NO permissions
        # Mask 0o077 covers ---rwxrwx
        perms = mode & 0o077

        assert perms == 0, f"File permissions {oct(mode)} are too loose (expected 0o600)"

        # Also verify content to ensure it was written correctly
        with open(state_file, "r") as f:
            saved_data = json.load(f)
        assert saved_data == test_state
