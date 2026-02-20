
import json
import pytest
from unittest.mock import MagicMock, patch
from src.providers import vor, wl_fetch
from src.places import client

def test_wl_fetch_json_error_handling():
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

def test_vor_json_error_handling():
    # Mock requests session
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.content = b"Invalid JSON"
    # mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "Invalid JSON", 0)
    # ^ fetch_content_safe returns bytes, and json.loads is called on it.

    # We need to mock fetch_content_safe to return invalid json bytes
    with patch("src.providers.vor.fetch_content_safe", return_value=b"Invalid JSON") as mock_fetch:
        # Also need to mock load_request_count to allow request
        with patch("src.providers.vor.load_request_count", return_value=(None, 0)):
            with patch("src.providers.vor.save_request_count"):
                # And mock log warning to verify it's called
                with patch("src.providers.vor._log_warning") as mock_log:
                    result = vor._fetch_departure_board_for_station("123", None, session=mock_session)
                    assert result is None
                    assert mock_log.called
                    args, _ = mock_log.call_args
                    assert "ungültig/zu groß" in args[0]

def test_places_client_json_error_handling():
    config = client.GooglePlacesConfig(
        api_key="key", included_types=[], language="en", region="US",
        radius_m=1000, timeout_s=5, max_retries=0, max_result_count=1
    )

    mock_session = MagicMock()
    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "Invalid JSON", 0)
    mock_response.iter_content.return_value = [b"Invalid JSON"]

    mock_post.return_value.__enter__.return_value = mock_response
    mock_session.post = mock_post

    # We need to bypass verify_response_ip logic which requires socket
    with patch("src.places.client.verify_response_ip"):
        c = client.GooglePlacesClient(config, session=mock_session)
        with pytest.raises(client.GooglePlacesError) as exc:
            c._post("endpoint", {})
        assert "Invalid JSON payload" in str(exc.value)
