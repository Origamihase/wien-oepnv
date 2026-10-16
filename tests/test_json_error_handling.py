
from unittest.mock import MagicMock, patch
from src.providers import wl_fetch

def test_wl_fetch_json_error_handling() -> None:
    # Mock requests session
    mock_session = MagicMock()

    # We need to mock fetch_content_safe to return invalid json bytes
    with patch("src.providers.wl_fetch.fetch_content_safe", return_value=b"Invalid JSON") as mock_fetch:
        # And mock log warning to verify it's called
        with patch("src.providers.wl_fetch.log.warning") as mock_log:
            result = wl_fetch._get_json("path", session=mock_session)
            assert result == {}
            assert mock_log.called
            args, _ = mock_log.call_args
            assert "ungültig oder kein JSON" in args[0]

