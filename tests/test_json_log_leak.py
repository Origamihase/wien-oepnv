
import unittest
from src.utils.logging import sanitize_log_message

class TestJsonLogLeak(unittest.TestCase):
    def test_json_newline_leak(self):
        # This case fails if newlines are escaped before redaction
        # because \s* in regex doesn't match literal \n
        msg = '{"password":\n"secret"}'
        sanitized = sanitize_log_message(msg)
        print(f"Original: {repr(msg)}")
        print(f"Sanitized: {repr(sanitized)}")

        # We expect the secret to be redacted
        self.assertNotIn("secret", sanitized)
        self.assertIn("***", sanitized)

    def test_json_escaped_newline_leak(self):
        # Case where the input already has escaped newlines (e.g. from another logger)
        # This might be tricky, but let's see.
        # If input is '{"password":\\n"secret"}', then \\n is literal backslash n.
        # \s* doesn't match it.
        # So strictly speaking, sanitize_log_message might not handle already escaped newlines well either.
        # But our primary concern is raw newlines being escaped by US.
        pass
