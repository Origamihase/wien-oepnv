
import sys
import pytest
from unittest.mock import patch
import importlib

def test_fallback_sanitization_missing_keys():
    # Save original modules to restore later
    original_logging = sys.modules.get('src.utils.logging')
    original_env = sys.modules.get('src.utils.env')

    try:
        # Remove src.utils.logging and src.utils.env from sys.modules to force reload
        if 'src.utils.logging' in sys.modules:
            del sys.modules['src.utils.logging']
        if 'src.utils.env' in sys.modules:
            del sys.modules['src.utils.env']

        # Mock ImportError for src.utils.logging
        with patch.dict(sys.modules, {'src.utils.logging': None}):
            # Now import src.utils.env
            try:
                # We use import_module to ensure we get the fresh module
                env_module = importlib.import_module("src.utils.env")
            except ImportError:
                pass

            # Check sanitize_log_message from the fresh module
            sanitize_log_message = env_module.sanitize_log_message

            # Test cases that are currently missing in env.py but present in logging.py
            secrets = {
                "nonce": "secret_nonce_123",
                "state": "secret_state_456",
                "client_assertion": "eyJhbGciOi...",
                "SAMLRequest": "PHNhbWxwOl...",
            }

            for key, value in secrets.items():
                log_msg = f"{key}={value}"
                sanitized = sanitize_log_message(log_msg)
                assert value not in sanitized, f"Failed to redact {key} in fallback: {sanitized}"
                assert "***" in sanitized, f"Failed to redact {key} in fallback: {sanitized}"

            # Header style
            header_msg = f"Client-Assertion: {secrets['client_assertion']}"
            sanitized_header = sanitize_log_message(header_msg)
            assert secrets['client_assertion'] not in sanitized_header
            assert "***" in sanitized_header
    finally:
        # Restore original modules
        if 'src.utils.logging' in sys.modules:
            del sys.modules['src.utils.logging']
        if 'src.utils.env' in sys.modules:
            del sys.modules['src.utils.env']

        if original_logging:
            sys.modules['src.utils.logging'] = original_logging
        if original_env:
            sys.modules['src.utils.env'] = original_env
