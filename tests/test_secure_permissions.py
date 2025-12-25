import os
import stat
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# We need to import the function to test
# Since build_feed imports config which has side effects (environment loading),
# we need to be careful.

@pytest.fixture
def mock_feed_config(monkeypatch):
    """Mock the feed config to avoid environment validation issues."""
    # We can patch validate_path in feed.config
    with patch("feed.config.validate_path", side_effect=lambda p, n: Path(p).resolve()):
        yield

def test_save_state_secure_permissions(tmp_path, mock_feed_config):
    """Verify that _save_state creates files with secure permissions (0600)."""
    # Import inside the test to ensure patches are active
    from build_feed import _save_state

    # Define a state file path
    state_file = tmp_path / "secure_state.json"

    # We need to patch the global STATE_FILE in build_feed module
    # or ensure _validate_path returns our path.
    # _save_state uses: path = _validate_path(STATE_FILE, "STATE_PATH")
    # We patched _validate_path to just return the path.
    # But we also need to make sure STATE_FILE variable passed to it is something reasonable?
    # Actually, if we patched validate_path, we can just pass the path we want to check if we could control the argument.
    # But _save_state takes 'state' dict, and uses global STATE_FILE.

    with patch("build_feed.STATE_FILE", state_file):
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
