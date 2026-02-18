
import pytest
from src.utils.secret_scanner import _scan_content

def test_deduplication_high_entropy_assignment():
    """
    Verify that a high-entropy secret in a sensitive assignment
    is reported only once (by the assignment scanner).
    """
    # 32 chars, high entropy
    secret = "abcdefghijklmnopqrstuvwxyzABCDEF"
    content = f'API_KEY = "{secret}"'

    findings = _scan_content(content)

    # Should find exactly one issue
    assert len(findings) == 1
    line, match, reason = findings[0]
    assert match == secret
    assert "Verdächtige Zuweisung" in reason

def test_deduplication_bearer_token():
    """
    Verify that a high-entropy Bearer token is reported only once
    (by the Bearer scanner).
    """
    # 32 chars, high entropy
    token = "abcdefghijklmnopqrstuvwxyzABCDEF"
    # Use text without assignment to avoid SENSITIVE_ASSIGN_RE
    content = f"Bearer {token}"

    findings = _scan_content(content)

    assert len(findings) == 1
    line, match, reason = findings[0]
    assert match == token
    assert "Bearer-Token" in reason

def test_distinct_secrets_same_line():
    """
    Verify that two distinct secrets on the same line are both reported.
    """
    # High entropy values that pass _looks_like_secret
    secret1 = "abcdefghijklmnopqrstuvwxyzABCDEF"
    secret2 = "ZYXWVUTSRQPONMLKJIHGFEDCBAzyxwv"
    # Use keys that match _SENSITIVE_ASSIGN_RE keywords (e.g. 'secret')
    content = f'SECRET_1="{secret1}" SECRET_2="{secret2}"'

    findings = _scan_content(content)

    assert len(findings) == 2
    matches = {f[1] for f in findings}
    assert secret1 in matches
    assert secret2 in matches

def test_aws_key_assignment_deduplication():
    """
    Verify that an AWS key in an assignment is reported once
    (likely by assignment scanner if variable name matches, or AWS scanner if not).
    """
    # Valid-looking AWS ID (20 chars)
    aws_id = "AKIAABCDEFGHIJKLMNOP"
    # _AWS_ID_RE matches (AKIA|ASIA|ACCA)[A-Z0-9]{16} -> 4+16=20 chars.

    # Case 1: Variable name matches sensitive assignment regex
    content1 = f'AWS_ACCESS_KEY_ID = "{aws_id}"'
    findings1 = _scan_content(content1)

    assert len(findings1) == 1
    # Match includes quotes or not?
    # SENSITIVE_ASSIGN_RE strips quotes.
    # So match is aws_id.
    assert findings1[0][1] == aws_id
    # We now prioritize the specific AWS scanner over the generic assignment scanner
    assert "AWS Access Key ID" in findings1[0][2]

    # Case 2: Variable name does NOT match sensitive assignment regex
    # But AWS ID scanner picks it up
    content2 = f'MY_COOL_ID = "{aws_id}"'
    findings2 = _scan_content(content2)

    assert len(findings2) == 1
    assert findings2[0][1] == aws_id
    assert "AWS Access Key ID" in findings2[0][2]

def test_overlapping_matches():
    """
    Test complex overlap scenarios.
    """
    # "Bearer" followed by a token that looks like a high entropy string
    token = "abcdefghijklmnopqrstuvwxyzABCDEF"
    content = f"Bearer {token}"

    findings = _scan_content(content)
    assert len(findings) == 1
    assert "Bearer-Token" in findings[0][2]
