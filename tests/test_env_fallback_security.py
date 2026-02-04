import sys
import logging
import pytest
from unittest.mock import patch

@pytest.fixture
def fallback_env():
    """
    Fixture that forces src.utils.env to be imported in fallback mode
    (simulating missing src.utils.logging).
    """
    # Preserve original modules
    original_env = sys.modules.get("src.utils.env")
    original_logging = sys.modules.get("src.utils.logging")

    # Clean up existing modules to ensure fresh import
    if "src.utils.env" in sys.modules:
        del sys.modules["src.utils.env"]
    if "src.utils.logging" in sys.modules:
        del sys.modules["src.utils.logging"]

    # Simulate utils.logging being missing
    with patch.dict(sys.modules, {"src.utils.logging": None, "utils.logging": None}):
        import src.utils.env as env
        yield env

    # Restore original modules or clean up
    if original_env:
        sys.modules["src.utils.env"] = original_env
    elif "src.utils.env" in sys.modules:
        del sys.modules["src.utils.env"]

    if original_logging:
        sys.modules["src.utils.logging"] = original_logging
    elif "src.utils.logging" in sys.modules:
        del sys.modules["src.utils.logging"]

def test_secure_fallback(caplog, fallback_env):
    """Verify that env.py fallback logging masks basic secrets."""
    caplog.set_level(logging.WARNING, logger="build_feed")

    # Simulate a value that looks like a query param or assignment
    # e.g. a connection string or URL param
    sensitive_value = "accessId=SuperSecretKey123"

    # Trigger a warning which calls sanitize_log_message on the value
    with patch("os.getenv", return_value=sensitive_value):
        fallback_env.get_int_env("DUMMY_VAR", 42)

    # Verify redaction of standard key
    assert "accessId=***" in caplog.text
    assert "accessId=SuperSecretKey123" not in caplog.text

def test_fallback_extended_keys(caplog, fallback_env):
    """
    Verify that env.py fallback logging masks vendor-specific and extended secrets.
    This protects against cloud provider keys (AWS, Azure, etc).
    """
    caplog.set_level(logging.WARNING, logger="build_feed")

    # List of sensitive patterns to test
    test_cases = [
        ("client_id=my-client-id", "client_id=***"),
        ("client_secret=my-secret", "client_secret=***"),
        ("x-api-key=abcdef123456", "x-api-key=***"),
        ("Ocp-Apim-Subscription-Key=azurekey", "Ocp-Apim-Subscription-Key=***"),
        ("Tenant-ID=tenant-uuid", "Tenant-ID=***"),
        ("refresh_token=refreshtoken", "refresh_token=***"),
    ]

    for raw, expected in test_cases:
        caplog.clear()
        with patch("os.getenv", return_value=raw):
            fallback_env.get_int_env("TEST_VAR", 0)

        if expected not in caplog.text:
            pytest.fail(f"Failed to redact '{raw}'. Log: {caplog.text}")

        # Ensure the secret value is not leaked
        secret_part = raw.split("=", 1)[1]
        assert secret_part not in caplog.text, f"Secret leaked for {raw}"
