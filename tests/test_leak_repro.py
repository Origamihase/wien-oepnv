import pytest
from src.utils.logging import sanitize_log_message

def test_aws_credential_leak():
    # This currently fails (leaks)
    secret = "x-amz-credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request"
    sanitized = sanitize_log_message(secret)
    # Ideally it should be redacted
    assert "***" in sanitized, f"Credential leaked: {sanitized}"
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
