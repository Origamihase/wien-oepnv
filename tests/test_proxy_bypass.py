import os
import pytest
from unittest.mock import Mock, patch
from src.utils.http import verify_response_ip

def test_verify_response_ip_normal_fail():
    """Verify that private IPs raise error normally."""
    response = Mock()
    # Mock the connection/socket structure
    response.raw._connection.sock.getpeername.return_value = ('192.168.1.1', 80)

    # Without proxy envs, this should fail
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="Connected to unsafe IP"):
            verify_response_ip(response)

def test_verify_response_ip_proxy_bypass():
    """Verify that private IPs are allowed when proxy envs are set."""
    response = Mock()
    response.raw._connection.sock.getpeername.return_value = ('192.168.1.1', 80)

    # With proxy env, this should succeed (return None)
    with patch.dict(os.environ, {'HTTP_PROXY': 'http://proxy.example.com'}, clear=True):
        verify_response_ip(response)

    with patch.dict(os.environ, {'https_proxy': 'http://proxy.example.com'}, clear=True):
        verify_response_ip(response)

    with patch.dict(os.environ, {'ALL_PROXY': 'socks5://proxy.example.com'}, clear=True):
        verify_response_ip(response)

if __name__ == "__main__":
    pytest.main([__file__])
