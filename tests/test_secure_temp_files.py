
import os
import stat
from pathlib import Path
from unittest.mock import patch
import json
from utils.cache import write_cache
from places.quota import MonthlyQuota, QuotaConfig

def test_cache_file_secure_permissions(tmp_path):
    """Verify that write_cache creates files with secure permissions (0600)."""
    # Setup
    cache_dir = tmp_path / "cache"
    provider = "test_provider"

    # Patch _cache_file to return a path in our temp dir
    with patch("utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        mock_cache_file.return_value = target_file

        items = [{"id": 1, "data": "test"}]

        write_cache(provider, items)

        assert target_file.exists()

        # Check permissions
        st = target_file.stat()
        mode = st.st_mode

        # Check that group and others have NO permissions
        perms = mode & 0o077
        assert perms == 0, f"Cache file permissions {oct(mode)} are too loose (expected 0o600)"

def test_quota_file_secure_permissions(tmp_path):
    """Verify that MonthlyQuota.save_atomic creates files with secure permissions (0600)."""
    quota_file = tmp_path / "quota.json"

    quota = MonthlyQuota(month_key="2023-01", counts={"nearby": 10}, total=10)

    quota.save_atomic(quota_file)

    assert quota_file.exists()

    # Check permissions
    st = quota_file.stat()
    mode = st.st_mode

    # Check that group and others have NO permissions
    perms = mode & 0o077
    assert perms == 0, f"Quota file permissions {oct(mode)} are too loose (expected 0o600)"
