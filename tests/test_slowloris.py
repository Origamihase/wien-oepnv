import pytest
import requests
import time
from unittest.mock import MagicMock, patch
from src.utils.http import fetch_content_safe, read_response_safe

def test_read_response_safe_timeout():
    """Test that read_response_safe raises Timeout if reading takes too long."""

    # Create a mock response with a slow iterator
    response = MagicMock(spec=requests.Response)
    response.headers = {}

    def slow_iterator(chunk_size=1):
        yield b"chunk1"
        time.sleep(0.2)
        yield b"chunk2"
        time.sleep(0.2)
        yield b"chunk3"

    response.iter_content.side_effect = slow_iterator

    # Set a short timeout (0.1s) which is less than the sleep (0.2s)
    with pytest.raises(requests.Timeout) as excinfo:
        read_response_safe(response, timeout=0.1)

    assert "Read timed out" in str(excinfo.value)

@patch("src.utils.http.validate_http_url")
@patch("src.utils.http.verify_response_ip")
def test_fetch_content_safe_slowloris(mock_verify_ip, mock_validate_url):
    """Test that fetch_content_safe handles total timeout correctly."""

    # Setup mocks
    mock_validate_url.return_value = "http://example.com"
    mock_verify_ip.return_value = None

    # Create a session mock
    session = MagicMock(spec=requests.Session)
    response = MagicMock(spec=requests.Response)
    response.headers = {}

    # Setup slow iterator for response content
    def slow_iterator(chunk_size=8192):
        # Initial chunk
        yield b"data"
        # Sleep to simulate slow transfer
        time.sleep(0.3)
        yield b"more data"

    response.iter_content.side_effect = slow_iterator
    response.raise_for_status.return_value = None

    # Configure session.get context manager to return our response
    session.get.return_value.__enter__.return_value = response
    session.get.return_value.__exit__.return_value = None

    # Call with timeout=0.1. session.get takes negligible time in mock.
    # So read_response_safe will get ~0.1s timeout.
    # The iterator sleeps 0.3s, so it should fail.

    with pytest.raises(requests.Timeout) as excinfo:
        fetch_content_safe(session, "http://example.com", timeout=0.1)

    assert "Read timed out" in str(excinfo.value)

def test_read_response_safe_no_timeout():
    """Test that read_response_safe works fine without timeout."""
    response = MagicMock(spec=requests.Response)
    response.headers = {}
    response.iter_content.return_value = [b"chunk1", b"chunk2"]

    content = read_response_safe(response, timeout=None)
    assert content == b"chunk1chunk2"

def test_content_length_malformed():
    """Test that malformed Content-Length is ignored."""
    response = MagicMock(spec=requests.Response)
    response.headers = {"Content-Length": "invalid"}
    response.iter_content.return_value = [b"data"]

    # Should not raise ValueError
    content = read_response_safe(response, max_bytes=100)
    assert content == b"data"
