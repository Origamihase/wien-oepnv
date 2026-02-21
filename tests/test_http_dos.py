import unittest
from src.utils.http import _sanitize_url_for_error, MAX_URL_LENGTH

class TestHttpDoS(unittest.TestCase):
    def test_sanitize_url_truncation(self):
        # Create a URL that is definitely longer than MAX_URL_LENGTH
        long_url = "https://example.com/" + "a" * (MAX_URL_LENGTH + 100)

        # This call should not hang or crash, and should return a truncated string
        sanitized = _sanitize_url_for_error(long_url)

        self.assertTrue(len(sanitized) <= MAX_URL_LENGTH + len("...[TRUNCATED]"),
                        f"Sanitized URL length {len(sanitized)} exceeds expected limit")
        self.assertTrue(sanitized.endswith("...[TRUNCATED]"),
                        "Sanitized URL should end with ...[TRUNCATED]")

if __name__ == "__main__":
    unittest.main()
