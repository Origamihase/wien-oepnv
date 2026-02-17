
import unittest
from src.utils.logging import sanitize_log_message

class TestLogSanitizationNewKeys(unittest.TestCase):
    def test_new_keys_redaction(self):
        # otp
        msg = "my_otp_code=123456"
        self.assertEqual(sanitize_log_message(msg), "my_otp_code=***")

        # otp seed (suffix match for otp, followed by _seed)
        msg = "otp_seed=123456"
        self.assertEqual(sanitize_log_message(msg), "otp_seed=***")

        # glpat
        msg = "gitlab_glpat_token=glpat-123"
        self.assertEqual(sanitize_log_message(msg), "gitlab_glpat_token=***")

        # ghp
        msg = "github_ghp_token=ghp_123"
        self.assertEqual(sanitize_log_message(msg), "github_ghp_token=***")

    def test_false_positives(self):
        # hotpot (contains otp but not at boundary)
        # Should NOT be redacted
        msg = "hotpot=delicious"
        sanitized = sanitize_log_message(msg)
        self.assertEqual(sanitized, "hotpot=delicious")

        # roughpath (contains ghp, still broad match)
        # Should be redacted because ghp uses wildcards
        msg = "roughpath=bumpy"
        sanitized = sanitize_log_message(msg)
        self.assertEqual(sanitized, "roughpath=***")

if __name__ == "__main__":
    unittest.main()
