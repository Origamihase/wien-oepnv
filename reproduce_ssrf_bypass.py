import sys
import os
from unittest.mock import MagicMock
import requests

# Add src to path
sys.path.insert(0, os.getcwd())

from src.utils.http import fetch_content_safe, validate_http_url

def reproduce():
    session = requests.Session()

    # Create a mock response
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 404
    mock_response.headers = {"Content-Length": "0"}
    mock_response.iter_content.return_value = []

    # Mock the socket info to return a private IP
    mock_connection = MagicMock()
    mock_sock = MagicMock()
    # 127.0.0.1 is unsafe
    mock_sock.getpeername.return_value = ('127.0.0.1', 80)
    mock_connection.sock = mock_sock
    # We need to set it on the mock_response.raw object which is accessed via getattr(response.raw, "connection", None)
    mock_raw = MagicMock()
    mock_raw.connection = mock_connection
    mock_response.raw = mock_raw
    mock_response.url = "http://example.com"

    # Mock raise_for_status to raise HTTPError
    def raise_404():
        raise requests.exceptions.HTTPError("404 Client Error", response=mock_response)
    mock_response.raise_for_status.side_effect = raise_404

    # Context manager for session.get
    mock_get = MagicMock()
    mock_get.__enter__.return_value = mock_response
    mock_get.__exit__.return_value = None
    session.get = MagicMock(return_value=mock_get)

    print("Attempting to fetch content from http://example.com (mocked to resolve to 127.0.0.1 and return 404)...")
    try:
        # We use a valid URL that passes the pre-check.
        # The mock session.get simulates the DNS rebinding or internal routing by returning a response from 127.0.0.1
        fetch_content_safe(session, "http://example.com")
        print("Success? (Should not happen)")
    except ValueError as e:
        if "Security: Connected to unsafe IP" in str(e):
            print("SAFE: Security check caught the IP!")
        else:
            print(f"ValueError: {e}")
    except requests.exceptions.HTTPError as e:
        print("VULNERABLE: HTTPError raised instead of Security Error!")
        print(f"Error: {e}")

if __name__ == "__main__":
    reproduce()
