from unittest.mock import MagicMock
import socket

def get_mock_socket_structure():
    """
    Returns a mock connection object structure (r.raw.connection.sock)
    that passes fetch_content_safe security checks.
    """
    mock_sock = MagicMock()
    # Mock getpeername to return a safe IP (8.8.8.8)
    mock_sock.getpeername.return_value = ("8.8.8.8", 80)

    mock_connection = MagicMock()
    mock_connection.sock = mock_sock

    return mock_connection
