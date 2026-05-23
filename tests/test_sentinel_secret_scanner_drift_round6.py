"""Sentinel PoC: secret-scanner drift Round 6 — three additional high-impact
issuer prefixes whose canonical format silently bypasses specific attribution
in the post-Round-5 ``_KNOWN_TOKENS`` table.

The 2026-05-09 Round 5 closed Atlassian /
Sentry / Linear and re-stated the prevention rule:

> "Treat ``_KNOWN_TOKENS`` as an issuer-keyed table — walk the modern
> Python-project credential landscape (config files, infra-as-code,
> observability stacks, project-management integrations) and add every
> variant whose canonical prefix is unambiguous and whose body matches
> the entropy fallback's alphabet."

Re-running that audit against three sub-landscapes that Round 5 did not
explicitly enumerate — **transactional email**, **API testing**, and
**secrets management** — surfaced three still-missing issuer classes
whose canonical formats are matched by the generic high-entropy fallback
(``[A-Za-z0-9+/=_-]{24,}``) *as a generic span* — no specific issuer
attribution — so the scanner output reads ``Hochentropischer Token-String``
instead of e.g. ``Brevo (Sendinblue) API Key gefunden``. Incident-response
triage keys off the specific issuer name (rotation playbook, revocation
URL, blast-radius estimate) and a generic-only finding slows that workflow:

  1. **Brevo (formerly Sendinblue) v3 API Keys**
     (``xkeysib-<64 hex>-<16 alphanumeric>``) — issued via
     app.brevo.com/settings/keys/api for transactional-email,
     marketing-automation, contacts, SMS-API, and webhook configuration
     access. Total length 89 chars (8-char prefix + 64-char hex secret +
     1 dash + 16-char alphanumeric request-id-like suffix). The
     ``xkeysib-`` prefix is unique to Brevo and unambiguous, but the
     body (hex + dash + alphanumeric) lives entirely inside the entropy
     fallback's alphabet — the entropy fallback flags the full span
     generically without the issuer attribution. A leak grants the
     attacker the ability to send mail FROM the project's domain
     (phishing amplification leveraging existing SPF / DKIM
     authentication), exfiltrate the contact list, or modify campaign
     templates.

  2. **Postman API Keys** (``PMAK-<24 hex>-<34 hex>``) — issued via
     postman.com/settings/me/api-keys for full Postman REST-API access
     (read/write every accessible workspace's collections, environments,
     mocks, monitors, team membership). Total length 64 chars. The
     ``PMAK-`` prefix is unique to Postman, but the strict-hex body
     would still be flagged generically by the entropy fallback. A leak
     grants access to private API definitions and mock-server URLs that
     may carry embedded credentials.

  3. **HashiCorp Cloud Platform (HCP) Vault Secrets tokens**
     (``hvs.<base64 body>``) — issued via portal.cloud.hashicorp.com
     for HCP Vault Secrets API access (the managed-Vault offering;
     read every secret stored in the namespace's apps and integrations).
     Total length 95-110 chars. The ``hvs.`` prefix is unique to
     HashiCorp's modern HCP token format (introduced 2023). A leak
     grants whoever holds the token full read-access to every secret
     the issuing service principal / human user can see — the highest
     blast-radius credential class in the modern infra stack.

Each test below pre-fix would have flagged only the generic
high-entropy fallback (or no finding at all if the prefix interrupts
the entropy-alphabet match); post-fix every token gets the issuer-
specific reason that incident-response playbooks key off.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# Brevo (formerly Sendinblue) v3 API Keys
# ---------------------------------------------------------------------------
#
# Format: ``xkeysib-<64 lowercase hex>-<16 alphanumeric>``. Issued via
# app.brevo.com/settings/keys/api. A leak grants:
# - Send mail FROM the project's domain via the transactional-email API
#   (phishing amplification leveraging SPF/DKIM authentication).
# - Read/exfiltrate the full contact list and segment metadata.
# - Register webhooks redirecting delivery events to attacker endpoints.
# - Create / modify / delete campaign templates and automation flows.
# The revocation flow lives at app.brevo.com/settings/keys/api and is
# distinct from any other vendor's, so issuer-specific attribution
# accelerates IR triage.


def test_secret_scanner_detects_brevo_api_key(tmp_path: Path) -> None:
    """Brevo (Sendinblue) v3 API Key: ``xkeysib-<64 hex>-<16 alphanumeric>``.

    Pre-fix: the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches the
    full token span (the dash and alphanumeric body are inside the
    alphabet) and reports ``Hochentropischer Token-String`` — losing
    the Brevo-specific attribution that incident-response keys off.
    """
    file_path = tmp_path / "brevo_client.py"
    # Realistic synthetic Brevo v3 API Key: 8-char prefix + 64-char hex
    # + 1 dash + 16-char alphanumeric. Total 89 chars matching the
    # documented canonical format.
    body = "0123456789abcdef" * 4  # 64-char lowercase hex
    suffix = "AbCdEf0123456789"  # 16-char alphanumeric
    secret = f"xkeysib-{body}-{suffix}"
    assert len(secret) == 89
    file_path.write_text(
        f'BREVO_API_KEY = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Brevo (Sendinblue) API Key"
    reasons = [f.reason for f in findings]
    assert "Brevo (Sendinblue) API Key gefunden" in reasons, (
        f"Expected Brevo-specific attribution, got reasons: {reasons}. "
        "Brevo v3 API keys grant transactional-email + contacts API "
        "access; precise attribution accelerates revocation at "
        "app.brevo.com/settings/keys/api and confines blast-radius "
        "estimates to Brevo's authenticated-mail-from-our-domain shape."
    )
    # Ensure raw secret never appears in findings (redaction).
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_brevo_token_in_env_config(tmp_path: Path) -> None:
    """Brevo tokens commonly appear in ``.env`` / shell-rc files; the
    detector must work regardless of surrounding context (quoted /
    unquoted, leading whitespace, KEY=VALUE shapes)."""
    file_path = tmp_path / "production.env"
    body = "abcdef0123456789" * 4  # 64-char hex
    suffix = "ZyXwVuTsRqPoNmLk"  # 16-char alphanumeric
    secret = f"xkeysib-{body}-{suffix}"
    file_path.write_text(f"BREVO_KEY={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Brevo (Sendinblue) API Key gefunden" in reasons, (
        "Brevo detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_secret_scanner_does_not_flag_short_xkeysib_prefix(tmp_path: Path) -> None:
    """Negative case: short ``xkeysib-`` strings (e.g. accidental
    fragments or operator-named placeholders) MUST NOT match the Brevo
    pattern. The strict 64-hex + 16-alphanumeric body length guard
    prevents this collision."""
    file_path = tmp_path / "config.py"
    # 12-char hex body — far too short to be a real Brevo token (canonical
    # body is 64 hex chars). The detector must require sufficient length.
    not_brevo = "xkeysib-abc123-x"
    file_path.write_text(f'placeholder = "{not_brevo}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Brevo (Sendinblue) API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# Postman API Keys
# ---------------------------------------------------------------------------
#
# Format: ``PMAK-<24 hex>-<34 hex>``. Issued via
# postman.com/settings/me/api-keys for full Postman REST-API access.
# A leak grants the issuing user's full Postman API scope across every
# workspace they belong to: read/write collections, environments, mocks,
# monitors, and team membership. Private API definitions and mock-server
# URLs that may carry embedded credentials are also exposed.
# The revocation flow lives at postman.com/settings/me/api-keys.


def test_secret_scanner_detects_postman_api_key(tmp_path: Path) -> None:
    """Postman API Key: ``PMAK-<24 hex>-<34 hex>``.

    Pre-fix: the entropy fallback matches the body+suffix as a generic
    high-entropy span; the ``PMAK-`` prefix is in a non-entropy alphabet
    region, so attribution is lost.
    """
    file_path = tmp_path / "postman_client.py"
    # Realistic synthetic Postman API key: 5-char prefix + 24-char hex
    # + 1 dash + 34-char hex. Total 64 chars matching the documented
    # canonical format.
    body = "abcdef0123456789ABCDEF12"  # 24-char hex (mixed case allowed)
    suffix = "0123456789abcdef0123456789ABCDEF12"  # 34-char hex
    secret = f"PMAK-{body}-{suffix}"
    assert len(secret) == 64
    file_path.write_text(f'POSTMAN_API_KEY = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Postman API Key"
    reasons = [f.reason for f in findings]
    assert "Postman API Key gefunden" in reasons, (
        f"Expected Postman-specific attribution, got reasons: {reasons}. "
        "Postman API keys grant the issuing user's full workspace API "
        "scope; precise attribution accelerates revocation at "
        "postman.com/settings/me/api-keys."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_pmak_prefix(tmp_path: Path) -> None:
    """Negative case: short ``PMAK-`` strings MUST NOT match the Postman
    pattern. The strict 24-hex + 34-hex body length guard prevents
    collision with operator-named identifiers."""
    file_path = tmp_path / "config.py"
    # Body too short, suffix too short — far below canonical Postman shape.
    not_postman = "PMAK-abcdef-1234"
    file_path.write_text(f'placeholder = "{not_postman}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Postman API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# HCP Vault Secrets Tokens
# ---------------------------------------------------------------------------
#
# Format: ``hvs.<base64 body>``. Issued via portal.cloud.hashicorp.com
# for HCP Vault Secrets API access (managed Vault). A leak grants
# whoever holds the token full read-access to every secret the issuing
# service principal / human user can see — the highest blast-radius
# credential class in the modern infra stack. The revocation flow
# lives at portal.cloud.hashicorp.com.


def test_secret_scanner_detects_hcp_vault_secrets_token(tmp_path: Path) -> None:
    """HCP Vault Secrets Token: ``hvs.<base64 body>``.

    Pre-fix: the entropy fallback flags only the body span (the ``.`` is
    OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``), so the
    ``Hochentropischer Token-String`` finding loses both the ``hvs.``
    prefix and the HashiCorp-specific issuer attribution that IR keys
    off.
    """
    file_path = tmp_path / "hcp_client.py"
    # Realistic synthetic HCP Vault Secrets token: 4-char prefix + 90-char
    # base64url body. The body uses ``[A-Za-z0-9_-]`` characters per the
    # base64url alphabet; HCP tokens encode embedded JSON metadata.
    body = (
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # 36 chars
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"  # 36 chars
        "_-AbCdEfGhIjKlMnOp"                     # 18 chars (90 total)
    )
    secret = f"hvs.{body}"
    assert len(secret) == 4 + 90
    file_path.write_text(f'HCP_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect HCP Vault Secrets Token"
    reasons = [f.reason for f in findings]
    assert "HCP Vault Secrets Token gefunden" in reasons, (
        f"Expected HCP-specific attribution, got reasons: {reasons}. "
        "HCP Vault Secrets tokens grant full read-access to every secret "
        "stored in the issuing namespace; precise attribution accelerates "
        "revocation at portal.cloud.hashicorp.com."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_hvs_prefix(tmp_path: Path) -> None:
    """Negative case: short ``hvs.`` strings MUST NOT match the HCP pattern.
    The 30+ body length guard prevents collision with attribute access
    chains (``obj.hvs.foo``) or accidental fragments."""
    file_path = tmp_path / "config.py"
    # 12-char body — too short to be a real HCP Vault Secrets token
    # (canonical body is ~90 chars).
    not_hcp = "hvs.abc123def456"
    file_path.write_text(f'placeholder = "{not_hcp}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HCP Vault Secrets Token gefunden" not in reasons


def test_hvs_does_not_misattribute_as_sendgrid(tmp_path: Path) -> None:
    """Mutual-exclusion regression: ``hvs.`` is dot-prefixed like
    SendGrid's ``SG.`` but uses lowercase + a different prefix and a
    single-segment body (no second dot). A real HCP token MUST NOT be
    flagged as SendGrid (which requires the ``SG.<22>.<43>`` 3-segment
    shape)."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" * 2 + "abcdefghijklmnop"
    secret = f"hvs.{body}"
    file_path.write_text(f'HCP = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HCP Vault Secrets Token gefunden" in reasons
    assert "SendGrid API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_round6_taxonomy() -> None:
    """Audit invariant: each Round-6 token class must remain in
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
        # 2026-05-10 / Round 6 additions (this PR):
        "Brevo (Sendinblue) API Key gefunden",
        "Postman API Key gefunden",
        "HCP Vault Secrets Token gefunden",
    ]
    for reason in expected_reasons:
        assert reason in source, (
            f"src/utils/secret_scanner.py must register the {reason!r} "
            f"detector in _KNOWN_TOKENS. See "
            f"tests/test_sentinel_secret_scanner_drift_round6.py for the "
            f"per-issuer PoC and the rationale for each pattern."
        )
