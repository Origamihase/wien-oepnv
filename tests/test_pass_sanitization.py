
from src.utils.http import _sanitize_url_for_error
from src.utils.logging import sanitize_log_message

def test_sanitize_url_missing_keys():
    """Test that pass and pwd are redacted in URLs."""
    # Note: urlencode encodes *** as %2A%2A%2A
    assert _sanitize_url_for_error("https://example.com?pass=secret") == "https://example.com?pass=%2A%2A%2A"
    assert _sanitize_url_for_error("https://example.com?pwd=secret") == "https://example.com?pwd=%2A%2A%2A"
    assert _sanitize_url_for_error("https://example.com?user_pass=secret") == "https://example.com?user_pass=%2A%2A%2A"

def test_sanitize_log_missing_keys():
    """Test that pass and pwd are redacted in logs."""
    # Note: assignment sanitization strips quotes
    assert sanitize_log_message("pass='secret'") == "pass=***"
    assert sanitize_log_message("pwd='secret'") == "pwd=***"
    assert sanitize_log_message("user_pass='secret'") == "user_pass=***"

    # Ensure no false positives
    assert sanitize_log_message("passenger='10'") == "passenger='10'"
    assert sanitize_log_message("compass='north'") == "compass='north'"
    assert sanitize_log_message("cwd='/home'") == "cwd='/home'"
    assert sanitize_log_message("api-pass='secret'") == "api-pass=***"
