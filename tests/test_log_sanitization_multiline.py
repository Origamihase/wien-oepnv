
import sys
import pytest
from unittest.mock import patch
from src.utils.logging import sanitize_log_message

def test_multiline_leak():
    msg = "Token: \n harmless \n SUPER_SECRET_VALUE"
    sanitized = sanitize_log_message(msg)
    print(f"Original: {repr(msg)}")
    print(f"Sanitized: {repr(sanitized)}")
    assert "SUPER_SECRET_VALUE" not in sanitized
    assert "Token: ***" in sanitized or "Token: \\n ***" in sanitized or "Token: \\n harmless \\n ***" in sanitized

def test_multiline_header_leak():
    msg = "Authorization: Bearer\ntoken"
    sanitized = sanitize_log_message(msg)
    print(f"Sanitized: {repr(sanitized)}")
    assert "token" not in sanitized
    assert "Authorization: ***" in sanitized

def test_env_fallback_multiline():
    # Save original modules
    original_env = sys.modules.get('src.utils.env')
    original_logging = sys.modules.get('src.utils.logging')

    # Remove from sys.modules to force reload
    if 'src.utils.env' in sys.modules:
        del sys.modules['src.utils.env']
    if 'src.utils.logging' in sys.modules:
        del sys.modules['src.utils.logging']

    try:
        # Mock src.utils.logging to appear missing
        with patch.dict(sys.modules):
            sys.modules['src.utils.logging'] = None
            sys.modules['utils.logging'] = None # Also block the fallback import attempt

            # Import src.utils.env
            # It handles ImportError by defining sanitize_log_message locally
            import src.utils.env

            # Check that we are indeed using the fallback
            # The fallback function is defined inside the try/except block, so checking its module might be tricky?
            # Actually, if we successfully imported src.utils.env without src.utils.logging,
            # then src.utils.env.sanitize_log_message MUST be the fallback one.

            sanitize = src.utils.env.sanitize_log_message

            # Verify fix
            msg = "Token: \n harmless \n SUPER_SECRET_VALUE"
            sanitized = sanitize(msg)
            print(f"Fallback Sanitized: {repr(sanitized)}")

            assert "SUPER_SECRET_VALUE" not in sanitized
            assert "Token: ***" in sanitized or "Token: \\n ***" in sanitized or "Token: \\n harmless \\n ***" in sanitized

            # Verify multiline header
            msg_header = "Authorization: Bearer\ntoken"
            sanitized_header = sanitize(msg_header)
            print(f"Fallback Sanitized Header: {repr(sanitized_header)}")
            assert "token" not in sanitized_header
            assert "Authorization: ***" in sanitized_header

    finally:
        # Restore original modules
        if original_env:
            sys.modules['src.utils.env'] = original_env
        elif 'src.utils.env' in sys.modules:
            del sys.modules['src.utils.env']

        if original_logging:
            sys.modules['src.utils.logging'] = original_logging
        elif 'src.utils.logging' in sys.modules:
            del sys.modules['src.utils.logging']
