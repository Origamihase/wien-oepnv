
from unittest.mock import patch
from src.utils.stations import _vienna_stations_regex

def test_vienna_stations_regex_excludes_digits():
    # Mock data with a numeric alias
    mock_data = (
        {
            "name": "Wien Test",
            "in_vienna": True,
            "aliases": ["51", "123", "Test Alias"]
        },
    )

    # Clear cache to ensure we rebuild regex with mock data
    _vienna_stations_regex.cache_clear()

    with patch("src.utils.stations._station_entries", return_value=mock_data):
        regex = _vienna_stations_regex()

        # "Test Alias" should match
        assert regex.search("Test Alias")

        # "Wien Test" should match
        assert regex.search("Wien Test")

        # "51" should NOT match (after fix)
        # Currently (before fix) it DOES match because "51".isdigit() is True.
        # If this fails, it confirms the bug exists (or rather, the behavior we want to change).
        assert not regex.search("51")

        # "123" should NOT match (after fix)
        assert not regex.search("123")
