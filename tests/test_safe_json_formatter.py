import logging
import json
import unittest
from io import StringIO
import sys
from src.feed.logging_safe import SafeJSONFormatter, SafeFormatter

class TestSafeJSONFormatter(unittest.TestCase):
    def test_format_exception_does_not_clear_frames(self):
        try:
            1 / 0
        except ZeroDivisionError:
            ei = sys.exc_info()

        tb = ei[2]
        self.assertIsNotNone(tb.tb_frame.f_locals)

        formatter = SafeJSONFormatter()
        formatter.formatException(ei)

        # Frame should still exist
        self.assertIsNotNone(tb.tb_frame.f_locals)
        self.assertTrue(len(tb.tb_frame.f_locals) > 0)

    def test_safe_formatter_format_does_not_mutate_record(self):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )

        formatter = SafeFormatter("%(message)s")
        formatter.format(record)

        self.assertEqual(record.msg, "Hello %s")
        self.assertEqual(record.args, ("world",))

    def test_extra_dict_leak(self):
        """Test that secrets in nested dictionaries in 'extra' are redacted."""
        logger = logging.getLogger("test_json_leak")
        logger.setLevel(logging.INFO)

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = SafeJSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        value = "non_sensitive_value"
        # Nested dictionary with sensitive key
        extra_data = {"context": {"api_key": value}}

        logger.info("Test message", extra={"data": extra_data})

        output = stream.getvalue()
        try:
            log_record = json.loads(output)
        except json.JSONDecodeError:
            self.fail("Log output is not valid JSON")

        # The original value should not be present in the output
        self.assertNotIn("non_sensitive_value", output)
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
        secret_value = "password=placeholder"

        logger.info("Test message", extra={"query": secret_value})

        output = stream.getvalue()

        self.assertNotIn("placeholder", output)
        self.assertIn("password=***", output)

if __name__ == "__main__":
    unittest.main()
