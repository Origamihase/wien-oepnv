
import pytest
import ipaddress
from src.utils.http import validate_http_url, is_ip_safe

def test_validate_http_url_blocks_localhost_variants():
    # Standard localhost
    assert validate_http_url("http://localhost/foo", check_dns=False) is None
    assert validate_http_url("http://localhost", check_dns=False) is None

    # Case insensitive
    assert validate_http_url("http://LoCaLhOsT/foo", check_dns=False) is None

    # Trailing dot (FQDN style) - previously vulnerable
    assert validate_http_url("http://localhost./foo", check_dns=False) is None
    assert validate_http_url("http://localhost.", check_dns=False) is None
    assert validate_http_url("http://LoCaLhOsT./foo", check_dns=False) is None

def test_is_ip_safe_blocks_site_local():
    # Site local (deprecated but often routed internally)
    # Python ipaddress.is_global returns True for these, so we must explicitly block them
    site_local = ipaddress.ip_address("fec0::1")
    assert site_local.is_site_local
    assert is_ip_safe(site_local) is False

    # Check string variant
    assert is_ip_safe("fec0::1") is False

def test_is_ip_safe_allows_public_ips():
    assert is_ip_safe("8.8.8.8") is True
    assert is_ip_safe("2001:4860:4860::8888") is True

def test_validate_http_url_blocks_site_local_literals():
    # Even with check_dns=False, literals are checked against is_ip_safe
    assert validate_http_url("http://[fec0::1]/foo", check_dns=False) is None
