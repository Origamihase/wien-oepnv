from src.utils.http import _sanitize_url_for_error

def test_sanitize_malformed_url_with_at_in_password():
    # Vulnerability: If regex stops at first @, 'ss' leaks.
    # Original: https:user:p@ss@host.com/path
    url = "https:user:p@ss@host.com/path"
    sanitized = _sanitize_url_for_error(url)

    # Assert that no part of the secret remains
    assert "p@ss" not in sanitized
    assert "ss@" not in sanitized
    assert "ss" not in sanitized.split("@")[-1] # Ensure 'ss' is not confused as host or auth

    # Check expected sanitized output
    # The exact output depends on whether it's treated as malformed by urlparse or not.
    # But for this specific malformed URL (no slashes), the regex handles it.
    assert "https:***@host.com/path" == sanitized or "https://***@host.com/path" == sanitized

def test_sanitize_url_with_multiple_ats_complex():
    # Case with more complex password
    url = "https://user:secret@part@host.com"
    sanitized = _sanitize_url_for_error(url)
    assert "secret" not in sanitized
    assert "part" not in sanitized
    assert "host.com" in sanitized

def test_sanitize_url_with_at_in_password_standard_scheme():
    # Even if scheme is standard, if urlparse fails or regex matches first, it should be safe.
    # Note: Standard `https://user:p@ss@host.com` might be parsed by urlparse differently depending on implementation.
    # But our regex should handle it safely regardless.
    url = "https://user:p@ss@host.com/foo"
    sanitized = _sanitize_url_for_error(url)
    assert "p@ss" not in sanitized
    assert "ss" not in sanitized.replace("host.com", "").replace("foo", "")
    assert "host.com" in sanitized
