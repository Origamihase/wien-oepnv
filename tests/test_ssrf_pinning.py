
import pytest
from unittest.mock import patch, MagicMock
from src.utils.http import fetch_content_safe
import socket
import requests

def test_fetch_content_safe_pins_dns():
    # Setup
    url = "http://example.com/foo"
    safe_ip = "93.184.216.34"

    # Mock _resolve_hostname_safe to return safe IP
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 80))]

        session = requests.Session()

        # Mock session.get to inspect arguments
        with patch.object(session, "get") as mock_get:
            mock_response = MagicMock(spec=requests.Response)
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "text/plain"}
            mock_response.iter_content.return_value = [b"ok"]
            mock_response.raw = MagicMock()
            mock_response.raw.connection.sock.getpeername.return_value = (safe_ip, 80)
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None
            mock_get.return_value = mock_response

            fetch_content_safe(session, url)

            # Verification
            mock_get.assert_called_once()
            args, kwargs = mock_get.call_args
            called_url = args[0]

            # 1. URL should contain IP, not hostname
            assert f"http://{safe_ip}" in called_url
            assert "example.com" not in called_url.split("/")[2] # Netloc should be IP

            # 2. Host header should be set to hostname
            assert "headers" in kwargs
            assert kwargs["headers"].get("Host") == "example.com"

def test_fetch_content_safe_https_skipped():
    # HTTPS should NOT be pinned (SNI issue)
    url = "https://example.com/foo"
    safe_ip = "93.184.216.34"

    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (safe_ip, 443))]

        session = requests.Session()

        with patch.object(session, "get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "text/plain"}
            mock_response.iter_content.return_value = [b"ok"]
            mock_response.raw.connection.sock.getpeername.return_value = (safe_ip, 443)
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None
            mock_get.return_value = mock_response

            fetch_content_safe(session, url)

            mock_get.assert_called_once()
            args, kwargs = mock_get.call_args
            called_url = args[0]

            # URL should still contain hostname
            assert "https://example.com" in called_url
