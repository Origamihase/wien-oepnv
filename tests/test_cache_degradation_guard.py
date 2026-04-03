import json
import pytest
from pathlib import Path
from unittest.mock import patch
from src.utils.cache import write_cache, DataDegradationError

def test_cache_degradation_guard_bypass_on_new_cache(tmp_path):
    """Verify that writing to a non-existent cache works normally."""
    provider = "test_new"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        mock_cache_file.return_value = target_file

        items = [{"id": 1}]
        write_cache(provider, items)

        assert target_file.exists()
        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 1

def test_cache_degradation_guard_bypass_on_empty_existing(tmp_path):
    """Verify that writing empty items to an empty existing cache works."""
    provider = "test_empty_existing"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([], f)

        mock_cache_file.return_value = target_file

        items = []
        write_cache(provider, items)

        assert target_file.exists()
        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 0

def test_cache_degradation_guard_raises_on_empty_payload(tmp_path):
    """Verify that writing empty items to a populated cache raises DataDegradationError."""
    provider = "test_empty_payload"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(10)], f)

        mock_cache_file.return_value = target_file

        items = []
        with pytest.raises(DataDegradationError, match="Empty payload rejected"):
            write_cache(provider, items)

def test_cache_degradation_guard_raises_on_drastic_drop(tmp_path):
    """Verify that writing drastically fewer items raises DataDegradationError."""
    provider = "test_drastic_drop"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(100)], f)

        mock_cache_file.return_value = target_file

        # Drop is > 80%, so anything < 20 items should trigger the error
        items = [{"id": i} for i in range(19)]
        with pytest.raises(DataDegradationError, match="Degraded payload rejected"):
            write_cache(provider, items)

def test_cache_degradation_guard_bypass_on_slight_drop(tmp_path):
    """Verify that a drop < 80% bypasses the degradation guard."""
    provider = "test_slight_drop"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(100)], f)

        mock_cache_file.return_value = target_file

        # 50 items is a 50% drop, which is acceptable
        items = [{"id": i} for i in range(50)]
        write_cache(provider, items)

        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 50

def test_cache_degradation_guard_bypass_on_corrupt_cache(tmp_path):
    """Verify that if the existing cache is corrupt, it's bypassed and overwritten."""
    provider = "test_corrupt"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            f.write("invalid json")

        mock_cache_file.return_value = target_file

        items = [{"id": 1}]
        write_cache(provider, items)

        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 1
