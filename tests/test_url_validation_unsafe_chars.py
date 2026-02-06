
from src.utils.http import validate_http_url

def test_validate_http_url_blocks_unsafe_chars() -> None:
    """Test that validate_http_url rejects URLs containing unsafe characters."""

    # List of unsafe characters from RFC 3986/2396 "unwise" or dangerous for injection
    # We explicitly block: < > " \ ^ ` { | }
    unsafe_chars = ['<', '>', '"', '\\', '^', '`', '{', '|', '}']

    base_url = "http://example.com/foo"

    for char in unsafe_chars:
        # Test char in path
        url = f"{base_url}{char}bar"
        assert validate_http_url(url, check_dns=False) is None, f"Should block '{char}' in path"

        # Test char in query
        url = f"{base_url}?q={char}"
        assert validate_http_url(url, check_dns=False) is None, f"Should block '{char}' in query"

def test_validate_http_url_allows_safe_urls() -> None:
    """Ensure safe URLs are still accepted."""
    assert validate_http_url("http://example.com/safe", check_dns=False) == "http://example.com/safe"
    assert validate_http_url("http://example.com/path-with-dashes", check_dns=False) == "http://example.com/path-with-dashes"
    assert validate_http_url("http://example.com/path_with_underscores", check_dns=False) == "http://example.com/path_with_underscores"
    assert validate_http_url("http://example.com/path.with.dots", check_dns=False) == "http://example.com/path.with.dots"
    assert validate_http_url("http://example.com/path~tilde", check_dns=False) == "http://example.com/path~tilde"

def test_validate_http_url_preserves_ipv6_brackets() -> None:
    """Ensure that [ and ] are allowed (required for IPv6 literals)."""
    # Note: validate_http_url calls is_ip_safe which checks global reachability.
    # [::1] is loopback, so it's rejected by is_ip_safe if check_dns=True (default logic for IPs).
    # But validate_http_url calls is_ip_safe EVEN IF check_dns=False for IP literals.
    # So we need a GLOBAL IPv6 address for it to pass.
    # 2001:db8::1 is documentation (reserved), might be blocked?
    # Let's use a random global unicast address. 2001:4860:4860::8888 (Google DNS)
    ipv6_url = "http://[2001:4860:4860::8888]"

    # We disable check_dns (for hostname resolution), but IP check logic runs anyway for literals.
    # Assuming the environment has network or is_ip_safe allows it.
    # src/utils/http.py is_ip_safe checks is_global.

    result = validate_http_url(ipv6_url, check_dns=False)
    assert result == ipv6_url

def test_validate_http_url_whitespace_control() -> None:
    """Re-verify whitespace and control chars are blocked (regression check)."""
    assert validate_http_url("http://example.com/ foo", check_dns=False) is None
    assert validate_http_url("http://example.com/\tfoo", check_dns=False) is None
    assert validate_http_url("http://example.com/\nfoo", check_dns=False) is None
    assert validate_http_url("http://example.com/\x00foo", check_dns=False) is None
