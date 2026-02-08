from __future__ import annotations

from pathlib import Path
from src.utils.secret_scanner import scan_repository

def test_secret_scanner_detects_prefixed_variables(tmp_path: Path) -> None:
    file_path = tmp_path / "config.py"
    # Secrets with enough length/entropy to pass _looks_like_secret
    # Focusing on cases where the sensitive keyword is NOT at the end
    secrets = {
        "db_password_prod": "DbPasswordProd1234567890",
        "api_key_v1": "ApiKeyV11234567890",
        "my_secret_value": "SecretValue1234567890",
        "auth_token_temp": "AuthTokenTemp1234567890",
        "stripe_webhook_secret": "StripeWebhookSecret1234567890", # Matches 'secret' but followed by nothing? No, matches 'secret' at end.
        "github_token_read": "GithubTokenRead1234567890"
    }

    content = ""
    for key, val in secrets.items():
        content += f'{key} = "{val}"\n'

    file_path.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    # We expect one finding per secret
    # With the old regex, many of these should be missed because they don't end with the keyword
    # e.g. db_password_prod -> 'password' followed by '_prod' (fails)

    findings_map = {}
    for finding in findings:
        for key, val in secrets.items():
            if val[:2] in finding.match and val[-2:] in finding.match:
                findings_map[key] = finding
                break

    missing = set(secrets.keys()) - set(findings_map.keys())
    assert not missing, f"Scanner missed keys: {missing}"
