
import sys
from unittest.mock import patch

def test_env_fallback_pass_sanitization():
    # Save original modules to restore later
    original_env = sys.modules.get('src.utils.env')
    original_logging = sys.modules.get('src.utils.logging')

    # Remove src.utils.logging and src.utils.env from sys.modules to force reload
    if 'src.utils.logging' in sys.modules:
        del sys.modules['src.utils.logging']
    if 'src.utils.env' in sys.modules:
        del sys.modules['src.utils.env']

    try:
        # Mock ImportError for src.utils.logging
        with patch.dict(sys.modules, {'src.utils.logging': None}):
            # Import src.utils.env which should now use fallback
            import src.utils.env

            # Verify fallback implementation handles pass/pwd
            sanitize = src.utils.env.sanitize_log_message

            assert sanitize("pass='secret'") == "pass=***"
            assert sanitize("pwd='secret'") == "pwd=***"
            assert sanitize("user_pass='secret'") == "user_pass=***"

            # Verify safety
            assert sanitize("passenger='10'") == "passenger='10'"
    finally:
        # Restore original modules to prevent side effects on other tests
        if original_env:
            sys.modules['src.utils.env'] = original_env
        elif 'src.utils.env' in sys.modules:
            del sys.modules['src.utils.env']

        if original_logging:
            sys.modules['src.utils.logging'] = original_logging
        elif 'src.utils.logging' in sys.modules:
            del sys.modules['src.utils.logging']
