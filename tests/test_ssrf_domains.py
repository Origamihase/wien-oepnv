
import pytest
from src.utils.http import validate_http_url

def test_validate_http_url_rebinding_domains():
    """Test that known DNS rebinding domains are blocked even without DNS resolution."""
    unsafe_domains = [
        "http://127.0.0.1.nip.io",
        "https://10.0.0.1.nip.io/foo",
        "http://customer-db.internal.nip.io",
        "http://nip.io",
        "http://foo.sslip.io",
        "http://192.168.0.1.sslip.io",
        "http://localtest.me",
        "http://sub.localtest.me",
        "http://lvh.me",
        "http://vcap.me",
        "http://xip.io",
        "http://xip.name",
        "http://127.0.0.1.127.0.0.1.nip.io", # Nested
    ]

    for url in unsafe_domains:
        # Should be blocked with check_dns=False
        assert validate_http_url(url, check_dns=False) is None, f"Should block {url} (no DNS)"

        # Should also be blocked with check_dns=True (either by domain block or IP block if DNS works)
        # Note: If DNS fails, it returns None anyway.
        assert validate_http_url(url, check_dns=True) is None, f"Should block {url} (with DNS)"

def test_validate_http_url_valid_domains():
    """Test that valid public domains are still allowed."""
    safe_domains = [
        "https://google.com",
        "http://example.com", # 'example' TLD is blocked in _UNSAFE_TLDS, wait.
        # "http://example.org",
        "https://github.com",
        "https://wien.gv.at",
        "http://oebb.at",
    ]

    # 'example' is in _UNSAFE_TLDS, so example.com is NOT blocked (TLD is com).
    # example.com is valid.

    for url in safe_domains:
        assert validate_http_url(url, check_dns=False) == url, f"Should allow {url} (no DNS)"

def test_validate_http_url_mixed_case():
    """Test that domain blocking is case-insensitive."""
    assert validate_http_url("http://127.0.0.1.NIP.IO", check_dns=False) is None
    assert validate_http_url("http://LoCaLtEsT.Me", check_dns=False) is None

def test_validate_http_url_suffix_match():
    """Test that we match suffixes correctly (dot-prefixed)."""
    # nip.io is blocked
    assert validate_http_url("http://nip.io", check_dns=False) is None
    # .nip.io is blocked
    assert validate_http_url("http://foo.nip.io", check_dns=False) is None

    # anip.io should NOT be blocked (unless anip.io is also unsafe, but it's not in our list)
    # nip.io is in list. anip.io ends with nip.io?
    # Our logic: lower_host == unsafe_domain or lower_host.endswith("." + unsafe_domain)
    # anip.io != nip.io
    # anip.io does NOT end with .nip.io

    # We need to verify if anip.io is real or not. For unit test, we assume it's safe if not in list.
    assert validate_http_url("http://anip.io", check_dns=False) == "http://anip.io"

    # manipulating.io
    assert validate_http_url("http://manipulating.io", check_dns=False) == "http://manipulating.io"
