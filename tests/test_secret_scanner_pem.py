
import unittest
from src.utils.secret_scanner import _scan_content, Finding

class TestSecretScannerPEM(unittest.TestCase):
    def test_pem_private_key_detection(self):
        pem_content = """-----BEGIN RSA PRIVATE KEY-----
MIIEpQIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9Oi..
... (lots of base64) ...
-----END RSA PRIVATE KEY-----"""

        findings = _scan_content(pem_content)
        self.assertTrue(findings, "Should detect PEM content")

        reasons = [f[2] for f in findings]
        self.assertIn("Private Key (PEM) gefunden", reasons)
        # Verify deduplication: should not also detect as High Entropy
        self.assertEqual(len(findings), 1, "Should detect PEM as a single finding")

    def test_pem_with_newlines(self):
        # A real-looking PEM with 64-char lines
        pem_content = """-----BEGIN RSA PRIVATE KEY-----
MIIEpQIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9OiMIIEpQIBAAKCAQEA3Tz2mr7SZ
iAMfQyuvBjM9OiMIIEpQIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9OiMIIEpQIBAAK
-----END RSA PRIVATE KEY-----"""

        findings = _scan_content(pem_content)
        self.assertTrue(findings, "Should detect PEM content")

        reasons = [f[2] for f in findings]
        self.assertIn("Private Key (PEM) gefunden", reasons)
        self.assertEqual(len(findings), 1, "Should be deduplicated")

if __name__ == "__main__":
    unittest.main()
