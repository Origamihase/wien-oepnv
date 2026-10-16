
from pathlib import Path
from unittest.mock import patch

from src.utils.cache import write_cache

def test_cache_file_secure_permissions(tmp_path: Path) -> None:
    """Verify that write_cache creates files with secure permissions (0600)."""
    # Setup
    cache_dir = tmp_path / "cache"
    provider = "test_provider"

    # Patch _cache_file to return a path in our temp dir
    with patch("src.utils.cache._cache_file") as mock_cache_file:
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
