"""Test sanitization of error messages in GooglePlacesClient."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from src.places.client import GooglePlacesClient, GooglePlacesConfig

class TestClientErrorSanitization(unittest.TestCase):
    def test_format_error_message_sanitization(self):
        config = GooglePlacesConfig(
            api_key="TEST_KEY",
            included_types=[],
            language="de",
            region="AT",
            radius_m=1000,
            timeout_s=10,
            max_retries=0
        )
        client = GooglePlacesClient(config)

        # Simulate a malicious response with control characters, newlines, and ANSI
        response = MagicMock()
        response.status_code = 400
        # The JSON payload simulates an error response from Google Places API
        response.text = '{"error": ...}' # Dummy text for the initial sanitize call
        response.json.return_value = {
            "error": {
                "message": "Malicious\nPayload\r\nWith\tControl\bChars\x1b[31mANSI\x1b[0m",
                "status": "INVALID_ARGUMENT\n\t",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.BadRequest",
                        "fieldViolations": [
                            {
                                "field": "bad_field\n",
                                "description": "bad_value\r"
                            }
                        ]
                    }
                ]
            }
        }

        # We access the private method to verify logic
        formatted = client._format_error_message(response)

        # Verify that control characters are escaped or removed
        # \n should become \n (escaped) or stripped depending on sanitization
        # sanitize_log_message replaces \n with \\n by default

        self.assertNotIn("\n", formatted)
        self.assertNotIn("\r", formatted)
        self.assertNotIn("\t", formatted)
        self.assertNotIn("\b", formatted)
        self.assertNotIn("\x1b", formatted) # ANSI

        # Check for escaped versions if that's what sanitize_log_message does
        self.assertIn("\\n", formatted)

        # Check that content is still present (sanitized)
        self.assertIn("Malicious", formatted)
        self.assertIn("Payload", formatted)
        self.assertIn("INVALID_ARGUMENT", formatted)
        self.assertIn("bad_field", formatted)
        self.assertIn("bad_value", formatted)
