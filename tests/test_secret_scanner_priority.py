from pathlib import Path
from src.utils.secret_scanner import scan_repository, Finding

def test_secret_scanner_priority(tmp_path):
    # Create a file with a known token assigned to a variable
    # expected: should detect "GitHub Personal Access Token gefunden"
    # actual (before fix): "Verdächtige Zuweisung eines potentiellen Secrets"

    secret_file = tmp_path / "secrets.py"
    # Create a valid-looking GitHub token with high entropy to pass _looks_like_secret
    # ghp_ + 36 chars
    token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    # 4 + 36 = 40 chars.

    content = f'my_github_token = "{token}"\n'
    secret_file.write_text(content, encoding="utf-8")

    findings = scan_repository(tmp_path)

    assert len(findings) == 1
    finding = findings[0]

    # We want the more specific reason
    assert "GitHub Personal Access Token gefunden" in finding.reason
    assert "Verdächtige Zuweisung" not in finding.reason
