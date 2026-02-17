
import unittest
from urllib.parse import unquote
from src.utils.http import _sanitize_url_for_error

class TestHttpSanitizationNewKeys(unittest.TestCase):
    def test_new_keys_redaction(self):
        # Test that glpat and ghp are redacted in query params
        url = "https://example.com/api?glpat=glpat-123456&ghp=ghp_abcdef&otp=123456&token=secret"
        sanitized = _sanitize_url_for_error(url)

        # Check that values are redacted
        # urlencode might encode *** as %2A%2A%2A
        decoded = unquote(sanitized)
        self.assertIn("glpat=***", decoded)
        self.assertIn("ghp=***", decoded)
        self.assertIn("otp=***", decoded)

        # Ensure values are NOT present in original sanitized string
        self.assertNotIn("glpat-123456", sanitized)
        self.assertNotIn("ghp_abcdef", sanitized)
        self.assertNotIn("123456", sanitized)

if __name__ == "__main__":
    unittest.main()
