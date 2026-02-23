
from unittest.mock import patch
import src.utils.stations as stations_module

def test_vienna_stations_regex_excludes_digits():
    # Mock data with a numeric alias
    mock_data = (
        {
            "name": "Wien Test",
            "in_vienna": True,
            "aliases": ["51", "123", "Test Alias"]
        },
    )

    # Access the private function via the module object to avoid import errors
    # if it's not in __all__ or if strict import checking is in place.
    # We also need to clear the cache on the function object itself.
    regex_func = stations_module._vienna_stations_regex
    regex_func.cache_clear()

    with patch("src.utils.stations._station_entries", return_value=mock_data):
        regex = regex_func()

        # "Test Alias" should match
        assert regex.search("Test Alias")

        # "Wien Test" should match
        assert regex.search("Wien Test")

        # "51" should NOT match (after fix)
        assert not regex.search("51")

        # "123" should NOT match (after fix)
        assert not regex.search("123")
