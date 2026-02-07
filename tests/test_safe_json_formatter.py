import logging
import json
import unittest
from io import StringIO
from src.feed.logging_safe import SafeJSONFormatter

class TestSafeJSONFormatter(unittest.TestCase):
    def test_extra_dict_leak(self):
        """Test that secrets in nested dictionaries in 'extra' are redacted."""
        logger = logging.getLogger("test_json_leak")
        logger.setLevel(logging.INFO)

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = SafeJSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        secret = "SUPER_SECRET_TOKEN"
        # Nested dictionary with sensitive key
        extra_data = {"context": {"api_key": secret}}

        logger.info("Test message", extra={"data": extra_data})

        output = stream.getvalue()
        try:
            log_record = json.loads(output)
        except json.JSONDecodeError:
            self.fail("Log output is not valid JSON")

        # The secret should not be present in the output
        self.assertNotIn(secret, output)
        self.assertIn("***", output)

        # Verify structure is preserved (roughly)
        self.assertIn("data", log_record["extra"])
        self.assertIn("context", log_record["extra"]["data"])
        self.assertIn("api_key", log_record["extra"]["data"]["context"])
        self.assertEqual(log_record["extra"]["data"]["context"]["api_key"], "***")

    def test_value_redaction_in_json(self):
        """Test that values looking like secrets (e.g. key=value pattern) are redacted even in JSON strings."""
        logger = logging.getLogger("test_json_value")
        logger.setLevel(logging.INFO)

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = SafeJSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # "password=secret" pattern should be caught by regex
        secret_value = "password=mysecretpassword"

        logger.info("Test message", extra={"query": secret_value})

        output = stream.getvalue()

        self.assertNotIn("mysecretpassword", output)
        self.assertIn("password=***", output)

if __name__ == "__main__":
    unittest.main()
