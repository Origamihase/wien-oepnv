import os
import stat
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import src.build_feed

@pytest.fixture
def mock_feed_config(monkeypatch):
    """Mock the feed config to avoid environment validation issues."""
    with patch("src.feed.config.validate_path", side_effect=lambda p, n: Path(p).resolve()):
        yield

def test_save_state_secure_permissions(tmp_path, mock_feed_config):
    """Verify that _save_state creates files with secure permissions (0600)."""
    # Import inside the test to ensure patches are active
    from src.build_feed import _save_state

    # Define a state file path
    state_file = tmp_path / "secure_state.json"

    # Patch src.build_feed.feed_config.STATE_FILE.
    with patch.object(src.build_feed.feed_config, "STATE_FILE", state_file), \
         patch("src.build_feed.validate_path", side_effect=lambda p, n: Path(p).resolve()):

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
