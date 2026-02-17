
import unittest
from src.utils.secret_scanner import _SENSITIVE_ASSIGN_RE, _looks_like_secret

class TestSecretScannerNewKeys(unittest.TestCase):
    def test_new_keys_detection(self):
        # We want to detect these new patterns
        test_cases = [
            'webhook_url = "https://discord.com/api/webhooks/123/abc"',
            'sentry_dsn = "https://abc@sentry.io/123"',
            # otp removed due to false positives
            'slack_token = "xoxb-1234-5678"',
            'glpat = "glpat-1234567890abcdef"',
            'ghp = "ghp_1234567890abcdef"',
        ]

        for case in test_cases:
            match = _SENSITIVE_ASSIGN_RE.search(case)
            self.assertIsNotNone(match, f"Failed to match regex: {case}")
            candidate = match.group(2).strip().strip('"')

            # Check if it passes _looks_like_secret
            is_secret = _looks_like_secret(candidate, is_assignment=True)
            self.assertTrue(is_secret, f"Candidate {candidate} rejected by _looks_like_secret")

    def test_specific_keywords(self):
        # These should match the regex
        keywords = ["glpat", "ghp"]
        for kw in keywords:
            text = f'{kw} = "some_secret_value_123"'
            match = _SENSITIVE_ASSIGN_RE.search(text)
            self.assertIsNotNone(match, f"Keyword {kw} not detected")

if __name__ == "__main__":
    unittest.main()
