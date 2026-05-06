"""Tests for token-taxonomy completeness in the secret scanner.

The journal in ``.jules/sentinel.md`` (entries from 2026-05-05) recorded that
``_KNOWN_TOKENS`` repeatedly drifted behind the issuer's full prefix list,
making the generic high-entropy fallback the only line of defence for whole
families of credentials. This module checks the variants that were missing
prior to the current security pass:

* Stripe ``sk_test_`` (test-mode secret key) — the live counterpart was
  detected, but test keys leaked the same way and silently fell back to
  the generic detector.
* Slack ``xoxa-`` (OAuth access token, configuration tokens) and
  ``xoxr-`` (refresh token used for rotating ``xoxb-``/``xoxp-`` issuance) —
  the bot/user variants were detected, but the OAuth/refresh variants were
  not, even though they grant equivalent or stronger workspace access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


def test_secret_scanner_detects_stripe_test_secret_key(tmp_path: Path) -> None:
    file_path = tmp_path / "billing.py"
    secret = "sk_test_" + "A1b2C3d4E5f6G7h8I9j0K1l2"  # 24-char body
    assert len(secret) == 32
    file_path.write_text(f'STRIPE_SECRET_KEY = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Stripe test secret key"
    # Raw secret must never appear unredacted in findings.
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert "Stripe Test Secret Key gefunden" in reasons, reasons


def test_secret_scanner_distinguishes_stripe_live_and_test(tmp_path: Path) -> None:
    """Live and test keys should be reported with separate reasons.

    The journal explicitly calls for distinct reasons per token variant so
    the report identifies *which* environment leaked.
    """
    file_path = tmp_path / "stripe_keys.py"
    live = "sk_live_" + "L" * 24
    test = "sk_test_" + "T" * 24
    file_path.write_text(
        f'STRIPE_LIVE = "{live}"\nSTRIPE_TEST = "{test}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = {f.reason for f in findings}

    assert "Stripe Live Secret Key gefunden" in reasons
    assert "Stripe Test Secret Key gefunden" in reasons


@pytest.mark.parametrize(
    ("prefix", "expected_reason"),
    [
        ("xoxa-", "Slack OAuth Access Token gefunden"),
        ("xoxr-", "Slack Refresh Token gefunden"),
    ],
)
def test_secret_scanner_detects_slack_oauth_token_variants(
    tmp_path: Path, prefix: str, expected_reason: str
) -> None:
    file_path = tmp_path / f"slack_{prefix.rstrip('-')}.py"
    # 24-char body covers both bot-style and OAuth-style structures.
    secret = prefix + "1234567890-abcdef-ABCDEFGH"
    file_path.write_text(f'SLACK_TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, f"Should detect {prefix} token"
    # Raw secret must never appear unredacted in findings.
    assert secret not in [f.match for f in findings]
    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, reasons


def test_secret_scanner_slack_xoxa_does_not_overlap_with_xoxb(tmp_path: Path) -> None:
    """The new xoxa-/xoxr- patterns must not absorb the existing bot/user matches."""
    file_path = tmp_path / "slack_mixed.py"
    bot = "xoxb-" + "1234567890-1234567890-" + "A" * 24
    oauth = "xoxr-" + "1234567890-abcdef-ABCDEFGH"
    file_path.write_text(
        f'BOT = "{bot}"\nREFRESH = "{oauth}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = {f.reason for f in findings}

    assert "Slack Bot Token gefunden" in reasons
    assert "Slack Refresh Token gefunden" in reasons
