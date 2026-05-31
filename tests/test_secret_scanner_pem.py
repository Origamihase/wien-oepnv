
import unittest
from src.utils.secret_scanner import _scan_content

class TestSecretScannerPEM(unittest.TestCase):
    def test_pem_private_key_detection(self) -> None:
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

    def test_pem_with_newlines(self) -> None:
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

    def test_pem_unterminated_markers_do_not_blow_up(self) -> None:
        # Regression guard for the quadratic ReDoS in _PEM_RE: many BEGIN
        # markers with no matching END must not drive finditer to scan to EOF
        # at every start position. The bounded inter-anchor quantifier keeps
        # this near-linear, so it completes well within the suite timeout and
        # (correctly) reports no PEM finding.
        content = ("-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 40 + "\n") * 3000
        findings = _scan_content(content)
        reasons = [f[2] for f in findings]
        self.assertNotIn("Private Key (PEM) gefunden", reasons)

    def test_pem_large_body_within_bound_detected(self) -> None:
        # A realistic large key body (well under the 8192-char bound, an
        # RSA-4096 PEM is ~3.2 KiB) is still detected after bounding _PEM_RE.
        body = "A" * 4000
        pem_content = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            f"{body}\n"
            "-----END RSA PRIVATE KEY-----"
        )
        findings = _scan_content(pem_content)
        reasons = [f[2] for f in findings]
        self.assertIn("Private Key (PEM) gefunden", reasons)

if __name__ == "__main__":
    unittest.main()
