import base64
import unittest
from src.providers.vor import _normalise_access_token

class TestVorTokenParsing(unittest.TestCase):
    """Test the parsing logic for VOR access credentials."""

    def test_bare_token(self):
        """Test that a bare token becomes a Bearer token."""
        token, header = _normalise_access_token("mytoken")
        self.assertEqual(token, "mytoken")
        self.assertEqual(header, "Bearer mytoken")

    def test_user_pass_encoding(self):
        """Test that 'user:pass' is auto-encoded to Basic Auth."""
        token, header = _normalise_access_token("user:pass")
        self.assertEqual(token, "user:pass")
        expected_b64 = base64.b64encode(b"user:pass").decode("ascii")
        self.assertEqual(header, f"Basic {expected_b64}")

    def test_prefixed_basic_header(self):
        """Test that an existing 'Basic ...' header is preserved."""
        raw = "Basic dXNlcjpwYXNz"
        token, header = _normalise_access_token(raw)
        # We expect the function to strip the prefix for the 'token' return value,
        # but keep the full header correct.
        self.assertEqual(token, "dXNlcjpwYXNz")
        self.assertEqual(header, raw)

    def test_prefixed_basic_header_unencoded(self):
        """Test that 'Basic user:pass' (unencoded) is detected and encoded."""
        # This supports legacy behavior/user error where they prefix but forget to encode
        raw = "Basic user:pass"
        token, header = _normalise_access_token(raw)
        self.assertEqual(token, "user:pass")
        expected_b64 = base64.b64encode(b"user:pass").decode("ascii")
        self.assertEqual(header, f"Basic {expected_b64}")

    def test_prefixed_bearer_header(self):
        """Test that an existing 'Bearer ...' header is preserved."""
        raw = "Bearer mytoken"
        token, header = _normalise_access_token(raw)
        self.assertEqual(token, "mytoken")
        self.assertEqual(header, raw)

    def test_empty(self):
        token, header = _normalise_access_token("")
        self.assertEqual(token, "")
        self.assertEqual(header, "")

    def test_whitespace(self):
        token, header = _normalise_access_token("  mytoken  ")
        self.assertEqual(token, "mytoken")
        self.assertEqual(header, "Bearer mytoken")
