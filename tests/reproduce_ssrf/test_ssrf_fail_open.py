
import pytest
import requests
import responses
from src.utils.http import fetch_content_safe, session_with_retries

def test_fetch_content_safe_fails_closed_without_socket_mock():
    """
    Verify that fetch_content_safe now FAILS (raises ValueError) when
    the socket/IP verification cannot be performed.
    """
    session = session_with_retries("TestAgent")
    url = "http://example.com/data"

    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, url, body=b"secret data", status=200)

        with pytest.raises(ValueError) as excinfo:
            fetch_content_safe(session, url)

        # Verify the error message indicates one of our failure paths
        err_msg = str(excinfo.value)
        assert "Security: Could not retrieve socket" in err_msg or "Security: Could not verify peer IP" in err_msg
