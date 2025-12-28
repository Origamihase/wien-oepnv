
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
    # These should be REJECTED after the fix
    assert validate_http_url("http://localhost") is None
    assert validate_http_url("https://localhost:8080") is None
    assert validate_http_url("http://LOCALHOST") is None

def test_validate_http_url_rejects_userinfo() -> None:
    assert validate_http_url("https://user:pass@example.com") is None
    assert validate_http_url("http://user@example.com") is None

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
