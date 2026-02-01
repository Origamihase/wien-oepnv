
import pytest
import requests
from src.utils.http import fetch_content_safe, session_with_retries

def test_fetch_content_safe_returns_sanitized_error_url():
    """Verify that fetch_content_safe includes the sanitized URL in the error message."""
    session = session_with_retries("test-agent")

    # A URL that fails validation (localhost) and contains a secret
    unsafe_url = "http://localhost/api/resource?token=SUPER_SECRET_VALUE&tenant_id=12345"

    with pytest.raises(ValueError) as excinfo:
        fetch_content_safe(session, unsafe_url, check_dns=False)

    error_msg = str(excinfo.value)

    # Assertions
    assert "Unsafe or invalid URL" in error_msg
    assert "http://localhost/api/resource" in error_msg
    assert "SUPER_SECRET_VALUE" not in error_msg
    assert "12345" not in error_msg

    # Check for redaction (urlencode uses %2A for *)
    assert "token=%2A%2A%2A" in error_msg or "token=***" in error_msg
    assert "tenant_id=%2A%2A%2A" in error_msg or "tenant_id=***" in error_msg

def test_fetch_content_safe_malformed_url():
    """Verify handling of malformed URLs."""
    session = session_with_retries("test-agent")
    malformed_url = "http://["

    with pytest.raises(ValueError) as excinfo:
        fetch_content_safe(session, malformed_url, check_dns=False)

    error_msg = str(excinfo.value)
    # The _sanitize_url_for_error returns "invalid_url" for malformed inputs
    assert "Unsafe or invalid URL: invalid_url" in error_msg
