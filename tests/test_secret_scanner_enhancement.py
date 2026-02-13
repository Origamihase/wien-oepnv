from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository, _scan_content


def test_secret_scanner_detects_webhook_url(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # Webhook URL with secret token (sufficient entropy/length for scanner)
    secret = "https://discord.com/api/webhooks/1234567890/ABCDEFG_HIJKLMNOPQRSTUVWXYZ_123456"
    file_path.write_text(f'webhook_url = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect webhook_url assignment"
    # Check that full secret is NOT exposed
    assert secret not in [f.match for f in findings]
    # Check matching logic
    assert "Verdächtige Zuweisung" in findings[0].reason


def test_secret_scanner_detects_dsn(tmp_path: Path) -> None:
    file_path = tmp_path / "sentry.py"
    secret = "https://abcdef1234567890@o0.ingest.sentry.io/0"
    file_path.write_text(f'sentry_dsn = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect sentry_dsn assignment"
    assert secret not in [f.match for f in findings]
    assert "Verdächtige Zuweisung" in findings[0].reason


def test_secret_scanner_detects_subscription_key(tmp_path: Path) -> None:
    file_path = tmp_path / "azure.py"
    secret = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    file_path.write_text(f'subscriptionkey = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect subscriptionkey assignment"
    assert secret not in [f.match for f in findings]
    assert "Verdächtige Zuweisung" in findings[0].reason


def test_secret_scanner_detects_short_webhook(tmp_path: Path) -> None:
    file_path = tmp_path / "short.py"
    # Even shorter webhooks should be caught if they meet min length/entropy
    # scan_repository uses _looks_like_secret which requires min length 8 for assignments
    secret = "https://hooks.slack.com/services/T000/B000/KEY123"
    file_path.write_text(f'slack_webhook = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect short webhook assignment"
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_webhook_without_underscore(tmp_path: Path) -> None:
    file_path = tmp_path / "plain.py"
    secret = "https://example.com/hooks/secret123456"
    file_path.write_text(f'webhook="{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect plain 'webhook' assignment"
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_multiline_assignment():
    """Test that a secret assigned across multiple lines is detected."""
    content = """
    my_secret =
    "super_secret_value_that_should_be_detected"
    """
    findings = _scan_content(content)
    assert len(findings) == 1
    assert findings[0][1] == "super_secret_value_that_should_be_detected"
    assert "Verdächtige Zuweisung eines potentiellen Secrets" in findings[0][2]

def test_secret_scanner_detects_triple_quoted_string():
    """Test that a secret in triple quotes is detected."""
    content = """
    my_secret = \"\"\"
    super_secret_value_in_triple_quotes
    \"\"\"
    """
    findings = _scan_content(content)
    assert len(findings) == 1
    # Note: _scan_content strips quotes, but preserves internal newlines
    assert "super_secret_value_in_triple_quotes" in findings[0][1]
    assert "Verdächtige Zuweisung eines potentiellen Secrets" in findings[0][2]

def test_secret_scanner_detects_triple_single_quoted_string():
    """Test that a secret in triple single quotes is detected."""
    content = """
    my_secret = '''
    another_secret_value_in_triple_quotes
    '''
    """
    findings = _scan_content(content)
    assert len(findings) == 1
    assert "another_secret_value_in_triple_quotes" in findings[0][1]

def test_secret_scanner_detects_mixed_newlines_and_spaces():
    """Test flexible whitespace around assignment."""
    content = """
    api_key
      =
    "my_api_key_value_12345"
    """
    findings = _scan_content(content)
    assert len(findings) == 1
    assert findings[0][1] == "my_api_key_value_12345"

def test_secret_scanner_ignores_short_values_in_triple_quotes():
    """Test that short/low entropy values in triple quotes are ignored."""
    content = """
    description = \"\"\"
    This is a long description but not a secret.
    It has low entropy.
    \"\"\"
    """
    findings = _scan_content(content)
    # Should be 0 because it doesn't look like a secret (low entropy/too short per line logic?)
    assert len(findings) == 0

def test_secret_scanner_detects_multiline_with_keyword():
    """Test that a keyword variable with multiline text is flagged if it looks like a secret."""
    content = """
    private_key = \"\"\"
    -----BEGIN PRIVATE KEY-----
    MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDZ...
    -----END PRIVATE KEY-----
    \"\"\"
    """
    findings = _scan_content(content)
    assert len(findings) >= 1
    found_assignment = False
    for f in findings:
        if "Verdächtige Zuweisung" in f[2]:
            found_assignment = True
            break
    assert found_assignment
