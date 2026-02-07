
import sys
from unittest.mock import patch
import pytest

from src.utils.http import validate_http_url, _UNSAFE_TLDS

# Mock DNS resolution to return a safe IP
# This isolates the TLD check from DNS check.
# If DNS returns a safe IP, only the TLD check stops unsafe domains.
SAFE_IP_INFO = [(2, 1, 6, '', ('8.8.8.8', 80))]

@pytest.fixture
def mock_dns_safe():
    with patch("src.utils.http._resolve_hostname_safe", return_value=SAFE_IP_INFO):
        yield

def test_tld_local_blocked_standard(mock_dns_safe):
    """Verify that standard .local domain is blocked."""
    url = "http://foo.local"
    assert validate_http_url(url) is None

def test_tld_local_blocked_with_trailing_dot(mock_dns_safe):
    """Verify that .local. domain is blocked (prevent bypass)."""
    url = "http://foo.local."
    assert validate_http_url(url) is None

def test_tld_internal_blocked(mock_dns_safe):
    """Verify that .internal. domain is blocked."""
    url = "http://foo.internal."
    assert validate_http_url(url) is None

def test_tld_localhost_blocked(mock_dns_safe):
    """Verify that localhost. is blocked."""
    url = "http://localhost."
    assert validate_http_url(url) is None

def test_tld_valid_allowed(mock_dns_safe):
    """Verify that valid domain with trailing dot is allowed."""
    url = "http://google.com."
    # Should return the stripped URL or the original?
    # validate_http_url returns candidate.strip().
    # It does NOT strip trailing dot from hostname in the return value,
    # only uses stripped hostname for validation.
    result = validate_http_url(url)
    assert result == "http://google.com."

def test_tld_valid_standard(mock_dns_safe):
    """Verify that valid domain is allowed."""
    url = "http://google.com"
    assert validate_http_url(url) == "http://google.com"

def test_tld_empty_check(mock_dns_safe):
    """Verify behavior with root domain (dot)."""
    # http://. is invalid hostname usually
    assert validate_http_url("http://.") is None
