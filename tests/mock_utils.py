from unittest.mock import MagicMock

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
    # Ensure _connection is also set if the caller assigns it to raw.connection but not raw._connection
    # Wait, the caller assigns the return value to raw.connection.
    # The caller typically does: response.raw.connection = get_mock_socket_structure()
    # If we want response.raw._connection to also work, the caller must assign it.
    # We can't fix it here unless we return something that magically sets _connection on parent? No.

    return mock_connection
