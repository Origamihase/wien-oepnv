"""Sentinel PoC: secret-scanner drift Round 5 — three high-impact issuer
prefixes whose canonical format silently bypasses specific attribution
in the current ``_KNOWN_TOKENS`` table.

The 2026-05-05 / 2026-05-06 / 2026-05-08 rounds (see
the audit) closed Anthropic / OpenAI / GitHub non-PAT /
SendGrid / Stripe / Slack / Hugging Face / DigitalOcean / GitLab
Pipeline Trigger / Twilio / Notion / JWT / Discord token patterns.
The prevention rule on those rounds was:

> "Treat ``_KNOWN_TOKENS`` as an issuer-keyed table, not a list. Whenever
> a new issuer is added or an existing entry is edited, walk the issuer's
> full documented prefix taxonomy and add every variant in the same pass
> with a distinct reason."

Re-running that audit against the modern Python-project issuer landscape
surfaced three still-missing issuer classes whose canonical formats are
matched by the generic high-entropy fallback (``[A-Za-z0-9+/=_-]{24,}``)
*as a generic span* — no specific issuer attribution — so the scanner
output reads ``Hochentropischer Token-String`` instead of e.g.
``Atlassian API Token gefunden``. Incident-response triage keys off the
specific issuer name (rotation playbook, revocation URL, blast-radius
estimate) and a generic-only finding slows that workflow:

  1. **Atlassian Cloud API Tokens** (``ATATT3xFfGF0...``) — issued via
     id.atlassian.com for Jira / Confluence / Trello Cloud REST APIs.
     Total length ~204 chars (12-char prefix + ~184-char base64-ish
     body + 8-char CRC32 hex suffix). The ``ATATT3xFfGF0`` prefix is
     unique to Atlassian and unambiguous, but the body is pure base64
     alphabet — the entropy fallback flags it generically without the
     issuer attribution.

  2. **Sentry Auth Tokens** (``sntrys_<base64-with-embedded-JSON>``) —
     Sentry's modern rotation-aware auth tokens (since 2023). Format:
     ``sntrys_<base64 body>_<checksum>`` where the body encodes the
     organization / scope JSON. Total length 60-100+ chars. The
     ``sntrys_`` prefix is unique. Used for the Sentry org-level API
     (``/api/0/organizations/<slug>/...``); a leak grants access to
     every project's issue/event data, releases, debug files, and
     source maps — full IR-relevant blast radius.

  3. **Linear API Keys** (``lin_api_<32+ alphanumeric>``) — Linear
     (issue tracker / project management) personal API keys, issued
     via linear.app/settings/api. Format: ``lin_api_`` prefix + 32+
     alphanumeric body. A leak grants the user's full API scope:
     read/write all visible issues, comments, attachments, projects,
     and team metadata. The strict alphanumeric body avoids overlap
     with the legacy ``ln-`` prefix some other vendors use.

Each test below pre-fix would have flagged only the generic
high-entropy fallback (or no finding at all if the issuer prefix
interrupts the entropy-alphabet match); post-fix every token gets the
issuer-specific reason that incident-response playbooks key off.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# Atlassian Cloud API Tokens
# ---------------------------------------------------------------------------
#
# Format: ``ATATT3xFfGF0`` (12-char unique prefix) + ~184-char base64-ish
# body + 8-char CRC32 hex suffix. Total ~204 chars. The body uses the
# base64url alphabet (``[A-Za-z0-9_=\-]``). A leak grants Cloud-API
# access for the issuing user across Jira / Confluence / Trello — read
# every accessible workspace, post comments, transition issues. The
# revocation flow lives at id.atlassian.com/manage-profile/security/api-tokens
# and is distinct from any other vendor's, so issuer-specific
# attribution accelerates IR triage.


def test_secret_scanner_detects_atlassian_cloud_api_token(tmp_path: Path) -> None:
    """Atlassian Cloud API Token: ``ATATT3xFfGF0<base64 body><CRC32>``."""
    file_path = tmp_path / "atlassian_config.py"
    # Realistic synthetic token: 12-char prefix + 184-char base64-ish
    # body + 8-char CRC32 hex. Total 204 chars matching observed format.
    body = (
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # 36 chars
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"  # 36 chars
        "KLMNOPQRSTUVWXYZ01234567890123456789"  # 36 chars
        "abcdef-_=AbCdEfGhIjKlMnOpQrStUvWxYz0"  # 36 chars (incl. base64-pad)
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 36 chars
        "abcd"                                   # 4 chars (total 184)
    )
    crc = "AB12CD34"  # 8-char CRC32 hex
    secret = f"ATATT3xFfGF0{body}{crc}"
    file_path.write_text(f'JIRA_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Atlassian Cloud API Token"
    reasons = [f.reason for f in findings]
    assert "Atlassian API Token gefunden" in reasons, (
        f"Expected Atlassian-specific attribution, got reasons: {reasons}. "
        "Atlassian Cloud API tokens grant Jira / Confluence / Trello "
        "REST-API access for the issuing user across all accessible "
        "workspaces; precise attribution accelerates revocation at "
        "id.atlassian.com/manage-profile/security/api-tokens."
    )
    # Ensure raw secret never appears in findings (redaction).
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_atlassian_token_in_yaml_config(tmp_path: Path) -> None:
    """Atlassian tokens commonly appear in YAML / .env config files; the
    detector must work regardless of surrounding context (quoted /
    unquoted, leading whitespace, KEY=VALUE shapes)."""
    file_path = tmp_path / "secrets.env"
    body = "X" * 100 + "Y" * 84  # 184-char body, low diversity but valid format
    secret = f"ATATT3xFfGF0{body}DEADBEEF"
    file_path.write_text(f"ATLASSIAN_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Atlassian API Token gefunden" in reasons, (
        "Atlassian detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_secret_scanner_does_not_flag_short_atatt3_prefix(tmp_path: Path) -> None:
    """Negative case: short ``ATATT3``-prefixed strings (e.g. accidental
    base64 fragments or operator-named identifiers) MUST NOT match the
    Atlassian pattern. The strict 100+ body length guard prevents this
    collision."""
    file_path = tmp_path / "config.py"
    # 8-char body — far too short to be a real Atlassian token (canonical
    # body is ~192 chars). The detector must require sufficient length.
    not_atlassian = "ATATT3xFfGF0AbCdEfGh"
    file_path.write_text(f'value = "{not_atlassian}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Atlassian API Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Sentry Auth Tokens
# ---------------------------------------------------------------------------
#
# Format: ``sntrys_<base64 body>_<checksum>``. Sentry's modern
# rotation-aware auth tokens (introduced 2023) replace the legacy 32/64-
# hex internal user tokens. The body is base64-ish encoding of an
# embedded JSON object describing the organisation / scope, and the
# trailing checksum guards against typo-induced cross-token confusion.
# Total length 60-100+ chars in practice.
#
# Used for the Sentry org-level API (``/api/0/organizations/<slug>/``)
# and project-level API; a leak grants access to every accessible
# project's issue/event data, releases, debug files, source maps, and
# member list — full IR-relevant blast radius. The revocation flow
# lives at sentry.io/settings/auth-tokens/ and is distinct from any
# other vendor's.


def test_secret_scanner_detects_sentry_auth_token(tmp_path: Path) -> None:
    """Sentry Auth Token: ``sntrys_<base64 body>_<checksum>``."""
    file_path = tmp_path / "sentry_config.py"
    # Realistic synthetic Sentry auth token. The body is a base64-ish
    # blob (typical real format encodes embedded JSON metadata).
    secret = (
        "sntrys_eyJpYXQiOjE2OTAwMDAwMDAuMCwidXJsIjoiaHR0cHM6Ly9zZW50"
        "cnkuaW8iLCJyZWdpb25fdXJsIjoiaHR0cHM6Ly91cy5zZW50cnkuaW8iLCJ"
        "vcmciOiJleGFtcGxlIn0_AbCdEfGh"
    )
    file_path.write_text(f'SENTRY_AUTH_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Sentry Auth Token"
    reasons = [f.reason for f in findings]
    assert "Sentry Auth Token gefunden" in reasons, (
        f"Expected Sentry-specific attribution, got reasons: {reasons}. "
        "Sentry auth tokens grant org-level API access (issues, events, "
        "releases, debug files, source maps); precise attribution "
        "accelerates revocation at sentry.io/settings/auth-tokens/."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_sntrys_prefix(tmp_path: Path) -> None:
    """Negative case: short ``sntrys_``-prefixed strings MUST NOT match
    the Sentry pattern. The 30+ body length guard prevents collision
    with operator-named identifiers or accidental fragments."""
    file_path = tmp_path / "config.py"
    not_sentry = "sntrys_short"  # 12-char body — too short.
    file_path.write_text(f'value = "{not_sentry}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Sentry Auth Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Linear API Keys
# ---------------------------------------------------------------------------
#
# Format: ``lin_api_<32+ alphanumeric chars>``. Issued via
# linear.app/settings/api for personal API access. A leak grants the
# user's full Linear scope: read/write all visible issues, comments,
# attachments, projects, team metadata, and webhook configuration. The
# revocation flow lives at linear.app/settings/api and is distinct from
# any other vendor's. The ``lin_api_`` prefix is unambiguous and the
# strict alphanumeric body (no underscores or hyphens after the prefix
# in canonical Linear format) avoids overlap with hyphenated tokens
# from other vendors.


def test_secret_scanner_detects_linear_api_key(tmp_path: Path) -> None:
    """Linear API Key: ``lin_api_<32+ alphanumeric chars>``."""
    file_path = tmp_path / "linear_client.py"
    # Realistic synthetic Linear API key: 8-char prefix + 40 alphanumeric.
    secret = "lin_api_" + "0123456789abcdefABCDEF" + "ghijklmnopqrstuvwxyz"
    assert len(secret) == 8 + 42
    file_path.write_text(f'LINEAR_API_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Linear API Key"
    reasons = [f.reason for f in findings]
    assert "Linear API Key gefunden" in reasons, (
        f"Expected Linear-specific attribution, got reasons: {reasons}. "
        "Linear API keys grant the issuing user's full project-management "
        "API scope; precise attribution accelerates revocation at "
        "linear.app/settings/api."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_lin_api_prefix(tmp_path: Path) -> None:
    """Negative case: short ``lin_api_`` strings MUST NOT match the Linear
    pattern. The 32-char minimum body prevents collision with operator-
    named identifiers (e.g. ``lin_api_url``)."""
    file_path = tmp_path / "config.py"
    # 8-char body — too short to be a real Linear API key.
    not_linear = "lin_api_xyzabc12"
    file_path.write_text(f'value = "{not_linear}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Linear API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_round5_taxonomy() -> None:
    """Audit invariant: each Round-5 token class must remain in
    ``_KNOWN_TOKENS``.

    A future PR that drops one of these patterns silently re-opens the
    issuer-attribution gap that this round closes. This test pins the
    canonical set so any such regression fails at PR-review time.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "utils" / "secret_scanner.py").read_text(
        encoding="utf-8"
    )

    expected_reasons = [
        # 2026-05-09 / Round 5 additions (this PR):
        "Atlassian API Token gefunden",
        "Sentry Auth Token gefunden",
        "Linear API Key gefunden",
    ]
    for reason in expected_reasons:
        assert reason in source, (
            f"src/utils/secret_scanner.py must register the {reason!r} "
            f"detector in _KNOWN_TOKENS. See "
            f"tests/test_sentinel_secret_scanner_drift_round5.py for the "
            f"per-issuer PoC and the rationale for each pattern."
        )
