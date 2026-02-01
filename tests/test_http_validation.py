from src.utils.http import validate_http_url


def test_validate_http_url_valid() -> None:
    assert validate_http_url("https://example.com") == "https://example.com"
    assert validate_http_url("http://google.com/foo") == "http://google.com/foo"
    # Port usage is fine if not local/private (will require complex logic or just be allowed if IP is public)
    # We will test public IPs if we can, but let's stick to domain names for now.


def test_validate_http_url_invalid_scheme() -> None:
    assert validate_http_url("ftp://example.com") is None
    assert validate_http_url("file:///etc/passwd") is None
    assert validate_http_url("javascript:alert(1)") is None


def test_validate_http_url_ssrf_domains() -> None:
    # These should be REJECTED (SSRF protection)
    assert validate_http_url("http://localhost") is None
    assert validate_http_url("https://localhost:8080") is None
    assert validate_http_url("http://LOCALHOST") is None


def test_validate_http_url_rejects_userinfo() -> None:
    assert validate_http_url("https://user:pass@example.com") is None
    assert validate_http_url("http://user@example.com") is None


def test_validate_http_url_rejects_excessive_length() -> None:
    long_url = "https://example.com/" + ("a" * 5000)
    assert validate_http_url(long_url) is None


def test_validate_http_url_ssrf_ips() -> None:
    # Private IPs should be REJECTED
    assert validate_http_url("http://127.0.0.1") is None
    assert validate_http_url("http://127.0.0.1:5000") is None
    assert validate_http_url("http://10.0.0.1") is None
    assert validate_http_url("http://192.168.1.1") is None
    assert validate_http_url("http://172.16.0.1") is None
    assert validate_http_url("http://0.0.0.0") is None

    # Link-local (cloud metadata)
    assert validate_http_url("http://169.254.169.254") is None

    # IPv6 loopback
    assert validate_http_url("http://[::1]") is None


def test_validate_http_url_obfuscated_ips_no_dns() -> None:
    # Integer IP (2130706433 -> 127.0.0.1)
    assert validate_http_url("http://2130706433", check_dns=False) is None

    # Hex IP (0x7f000001 -> 127.0.0.1)
    assert validate_http_url("http://0x7f000001", check_dns=False) is None

    # Dotted Hex IP (0x7f.0x0.0x0.0x1 -> 127.0.0.1) - TLD "0x1" starts with digit
    assert validate_http_url("http://0x7f.0x0.0x0.0x1", check_dns=False) is None

    # Short numeric (127.1 -> 127.0.0.1) - TLD "1" starts with digit
    assert validate_http_url("http://127.1", check_dns=False) is None

    # Octal/Mixed (0127.0.0.1) - TLD "1" starts with digit
    assert validate_http_url("http://0127.0.0.1", check_dns=False) is None

    # IP with TLD (rare but possible obfuscation?) - 127.0.0.1.
    # TLD is empty? "127.0.0.1." split -> "1" is last non-empty?
    # split(".") for "1." -> ["1", ""]
    # My code: labels = lower_host.split(".") -> ["127", "0", "0", "1", ""]
    # labels[-1] is empty. if not tld: return None.
    # So "http://127.0.0.1." should return None.
    assert validate_http_url("http://127.0.0.1.", check_dns=False) is None

    # Valid domains should pass
    assert validate_http_url("http://example.com", check_dns=False) == "http://example.com"
    assert validate_http_url("http://123.com", check_dns=False) == "http://123.com"
    assert validate_http_url("http://xn--Example.com", check_dns=False) == "http://xn--Example.com"
    # IDN Punycode TLD
    assert validate_http_url("http://example.xn--vermgensberatung-pwb", check_dns=False) == "http://example.xn--vermgensberatung-pwb"


def test_validate_http_url_reserved_tlds() -> None:
    # These should be blocked even if check_dns=False
    assert validate_http_url("http://myprinter.local", check_dns=False) is None
    assert validate_http_url("http://router.lan", check_dns=False) is None
    assert validate_http_url("http://server.internal", check_dns=False) is None
    assert validate_http_url("http://test.localhost", check_dns=False) is None
    assert validate_http_url("http://example.test", check_dns=False) is None
    assert validate_http_url("http://site.invalid", check_dns=False) is None

    # Tor and I2P should be blocked
    assert validate_http_url("http://example.onion", check_dns=False) is None
    assert validate_http_url("http://example.i2p", check_dns=False) is None

    # Case insensitivity check
    assert validate_http_url("http://ROUTER.LAN", check_dns=False) is None
    assert validate_http_url("http://MyPrinter.Local", check_dns=False) is None
    assert validate_http_url("http://Hidden.Onion", check_dns=False) is None


def test_validate_http_url_shared_address_space() -> None:
    # 100.64.0.0/10 (CGNAT) should be rejected
    assert validate_http_url("http://100.64.0.1", check_dns=False) is None
    assert validate_http_url("http://100.127.255.255", check_dns=False) is None
    # 0.0.0.0 should be rejected
    assert validate_http_url("http://0.0.0.0", check_dns=False) is None
    # :: should be rejected
    assert validate_http_url("http://[::]", check_dns=False) is None
