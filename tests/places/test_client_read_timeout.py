from unittest.mock import MagicMock, patch
import pytest
import requests
from src.places.client import GooglePlacesClient, GooglePlacesConfig, GooglePlacesError

@pytest.fixture
def client_config():
    return GooglePlacesConfig(
        api_key="test-key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=500,
        timeout_s=5.0,
        max_retries=0,
        max_result_count=20
    )

@patch("src.places.client.verify_response_ip")
@patch("src.places.client.read_response_safe")
def test_post_passes_read_timeout(mock_read_response_safe, mock_verify_ip, client_config):
    """Verify that _post calculates and passes a read timeout to read_response_safe."""
    # Setup mock session and response
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 200
    mock_response.headers = {"Content-Type": "application/json"}

    # Context manager mock for session.post
    mock_post_cm = MagicMock()
    mock_post_cm.__enter__.return_value = mock_response
    mock_session.post.return_value = mock_post_cm

    # Mock read_response_safe to return valid JSON bytes
    mock_read_response_safe.return_value = b'{"places": []}'

    client = GooglePlacesClient(client_config, session=mock_session)

    # Act
    client._post("places:searchNearby", {"foo": "bar"})

    # Assert
    # Verify read_response_safe was called
    assert mock_read_response_safe.called

    # Verify timeout argument was passed and is reasonably close to config.timeout_s
    # (It will be slightly less due to elapsed time, but close enough for a mock test)
    call_kwargs = mock_read_response_safe.call_args.kwargs
    assert "timeout" in call_kwargs
    passed_timeout = call_kwargs["timeout"]
    assert passed_timeout is not None
    # Allow some buffer for execution time, but it should be close to 5.0
    assert 4.0 < passed_timeout <= 5.0
