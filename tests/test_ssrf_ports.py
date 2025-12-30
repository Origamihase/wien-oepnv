
import pytest
from src.utils.http import validate_http_url

def test_validate_http_url_blocks_unsafe_ports():
    # Port 8080 is not in default allowed ports (80, 443)
    assert validate_http_url("http://example.com:8080", check_dns=False) is None
    assert validate_http_url("https://example.com:8443", check_dns=False) is None
    assert validate_http_url("http://example.com:22", check_dns=False) is None

def test_validate_http_url_allows_standard_ports():
    assert validate_http_url("http://example.com:80", check_dns=False) == "http://example.com:80"
    assert validate_http_url("https://example.com:443", check_dns=False) == "https://example.com:443"

def test_validate_http_url_allows_implicit_ports():
    assert validate_http_url("http://example.com", check_dns=False) == "http://example.com"
    assert validate_http_url("https://example.com", check_dns=False) == "https://example.com"

def test_validate_http_url_custom_ports():
    assert validate_http_url("http://example.com:8080", check_dns=False, allowed_ports=(8080,)) == "http://example.com:8080"
    assert validate_http_url("http://example.com", check_dns=False, allowed_ports=(8080,)) is None # Default port 80 not allowed

def test_validate_http_url_invalid_port_format():
    # Port out of range or invalid format shouldn't crash
    # urlparse might handle or raise, we ensure we catch it
    assert validate_http_url("http://example.com:999999", check_dns=False) is None
    # Note: 'http://example.com:foo' is parsed as netloc='example.com:foo', hostname='example.com', port=None by urllib in some versions?
    # Actually urllib.parse often raises ValueError for invalid ports on access to .port
    assert validate_http_url("http://example.com:foo", check_dns=False) is None
