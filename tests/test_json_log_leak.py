
import unittest
from src.utils.logging import sanitize_log_message

class TestJsonLogLeak(unittest.TestCase):
    def test_json_newline_leak(self) -> None:
        # This case fails if newlines are escaped before redaction
        # because \s* in regex doesn't match literal \n
        msg = '{"password":\n"secret"}'
        sanitized = sanitize_log_message(msg)
        print(f"Original: {repr(msg)}")
        print(f"Sanitized: {repr(sanitized)}")

        # We expect the secret to be redacted
        self.assertNotIn("secret", sanitized)
        self.assertIn("***", sanitized)

