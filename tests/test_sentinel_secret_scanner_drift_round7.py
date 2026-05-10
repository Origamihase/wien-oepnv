"""Sentinel PoC: secret-scanner drift Round 7 — three additional high-impact
issuer prefixes whose canonical format silently bypasses specific attribution
in the post-Round-6 ``_KNOWN_TOKENS`` table.

The 2026-05-10 Round 6 (see ``.jules/sentinel.md``) closed Brevo / Postman /
HCP Vault Secrets and re-stated the prevention rule:

> "Every audit round that adds a new issuer MUST also enumerate THREE
> adjacent sub-landscapes the round did NOT cover."

Round 6 enumerated transactional-email / API-testing / secrets-management as
sub-landscapes, and named **secrets management continued (Doppler /
Infisical)** plus **CI/CD platforms (Buildkite / Render / Netlify / Vercel)**
as the next-round candidates. Re-running that audit walker against three of
those still-missing issuer classes — **Doppler**, **Buildkite**, and
**Netlify** — surfaces three issuer prefixes whose canonical formats are
matched by the generic high-entropy fallback (``[A-Za-z0-9+/=_-]{24,}``) *as
a generic span* — no specific issuer attribution — so the scanner output
reads ``Hochentropischer Token-String`` instead of e.g.
``Doppler Token gefunden``. Incident-response triage keys off the specific
issuer name (rotation playbook, revocation URL, blast-radius estimate) and
a generic-only finding slows that workflow:

  1. **Doppler tokens**
     (``dp.<role>.<43 alphanumeric body>`` where ``<role>`` is one of
     ``pt`` / ``st`` / ``sa`` / ``ct`` / ``scim`` / ``audit``) — issued via
     dashboard.doppler.com for Doppler's secrets-management API. Total
     length 50 chars (``dp.`` + 2-5 char role + ``.`` + 43-char body) for
     the canonical personal-token / service-token / service-account-token
     / CLI-token / SCIM-token / audit-log-token shapes. The literal ``.``
     separators are OUTSIDE the entropy fallback's alphabet
     ``[A-Za-z0-9+/=_-]``, so the entropy regex matches only the
     43-char body span as one finding — losing both the ``dp.<role>.``
     prefix AND the Doppler issuer attribution. A leak grants the
     attacker the issuing principal's full Doppler scope: read every
     project's secrets (database creds, third-party API keys, OAuth
     client secrets, signing keys are all routinely stored in Doppler
     environments), modify config branches, and exfiltrate the audit log.
     Doppler is the next-largest secrets-management player after HCP
     Vault (Round 6) and is widely used by Python web projects via
     the official ``dopplersdk`` package and Doppler CLI.

  2. **Buildkite Agent Token** (``bkat_<40+ alphanumeric body>``) — issued
     via buildkite.com/organizations/<org>/agents for Buildkite agent
     registration. The ``bkat_`` prefix is unambiguous (no other major
     issuer uses it), and the strict alphanumeric body lies entirely
     inside the entropy fallback's alphabet — so the entropy regex
     matches the full ``bkat_<body>`` span as one generic finding,
     losing the Buildkite-specific attribution. A leak lets a network
     adversary register a rogue agent that drains the Buildkite job
     queue: every CI job (with whatever build-secret env vars the
     pipeline exposes) is delivered to attacker-controlled hardware.
     The blast radius is the entire CI estate's job-execution
     surface — the highest leak surface in the modern CI stack.
     Buildkite is the canonical CI platform for the previously-named
     CI/CD sub-landscape Round 6 left for Round 7.

  3. **Netlify Personal Access Token** (``nfp_<40+ alphanumeric body>``) —
     issued via app.netlify.com/user/applications for full Netlify REST-
     API access (the modern post-2023 ``nfp_``-prefixed format; the
     legacy 40-char-hex pre-prefix tokens fell into the bucket-(b)
     no-prefix landscape). Total length 44+ chars (4-char prefix + 40+
     char body). The ``nfp_`` prefix is unambiguous, and the body lies
     entirely inside the entropy alphabet — same generic-only
     attribution gap as Buildkite. A leak grants the issuing user's
     full Netlify API scope: read/write every site's deploys, redirect
     rules, environment variables, build-hook URLs, edge-function
     code, and DNS records. The site-deploy primitive in particular
     means an attacker can replace the live site with arbitrary HTML
     / JS, bypassing every downstream content gate. Netlify rounds
     out the CI/CD sub-landscape's hosting-platform tier.

Each test below pre-fix would have flagged only the generic high-entropy
fallback (or, for Doppler, only the body span after the second ``.``);
post-fix every token gets the issuer-specific reason that incident-
response playbooks key off.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# Doppler tokens (multi-role: pt / st / sa / ct / scim / audit)
# ---------------------------------------------------------------------------
#
# Format: ``dp.<role>.<43-char alphanumeric body>``. Issued via
# dashboard.doppler.com for Doppler's secrets-management API. A leak
# grants the issuing principal's full Doppler scope across every project
# they can see — read every secret (database creds, third-party API
# keys, OAuth client secrets, signing keys are all routinely stored in
# Doppler environments), modify config branches, and exfiltrate the
# audit log. The revocation flow lives at dashboard.doppler.com.


def test_secret_scanner_detects_doppler_personal_token(tmp_path: Path) -> None:
    """Doppler Personal Token: ``dp.pt.<43 alphanumeric>``.

    Pre-fix: the ``.`` separators are OUTSIDE the entropy fallback's
    alphabet ``[A-Za-z0-9+/=_-]``, so the entropy regex matches only
    the 43-char body span as one finding — losing both the ``dp.pt.``
    prefix AND the Doppler issuer attribution.
    """
    file_path = tmp_path / "doppler_client.py"
    # Realistic synthetic Doppler personal token: 6-char prefix
    # (``dp.pt.``) + 43-char alphanumeric body. Total 49 chars matching
    # the documented canonical format.
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfG"  # 43 alphanumeric
    assert len(body) == 43
    secret = f"dp.pt.{body}"
    file_path.write_text(
        f'DOPPLER_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Doppler Personal Token"
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" in reasons, (
        f"Expected Doppler-specific attribution, got reasons: {reasons}. "
        "Doppler tokens grant the issuing principal's full secrets-"
        "management scope; precise attribution accelerates revocation "
        "at dashboard.doppler.com and confines blast-radius estimates "
        "to Doppler's read-every-project-secret shape."
    )
    # Ensure raw secret never appears in findings (redaction).
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_doppler_service_token(tmp_path: Path) -> None:
    """Doppler Service Token: ``dp.st.<43 alphanumeric>``.

    Service tokens are scoped to a single config and are commonly
    embedded in production deployments; they have the same blast
    radius for the scoped config as personal tokens have at the
    workspace level (every secret in the config is exposed).
    """
    file_path = tmp_path / "deploy.env"
    body = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"  # 43 alphanumeric
    secret = f"dp.st.{body}"
    file_path.write_text(f"DOPPLER_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" in reasons, (
        "Doppler service-token detector must flag tokens in unquoted "
        "KEY=VALUE shapes commonly seen in ``.env`` deploy artefacts."
    )


def test_secret_scanner_detects_doppler_service_account_token(tmp_path: Path) -> None:
    """Doppler Service Account Token: ``dp.sa.<43 alphanumeric>``.

    Service-account tokens are the modern long-lived form for CI
    pipelines and grant scoped read access across the configured
    projects.
    """
    file_path = tmp_path / "ci_config.py"
    body = "ZyXwVuTsRqPoNmLkJiHgFeDcBaZyXwVuTsRqPoNmLkJ"  # 43 alphanumeric
    secret = f"dp.sa.{body}"
    file_path.write_text(f'CI_DOPPLER = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" in reasons


def test_secret_scanner_detects_doppler_cli_token(tmp_path: Path) -> None:
    """Doppler CLI Token: ``dp.ct.<43 alphanumeric>``.

    CLI tokens are issued for ``doppler login`` device flows and
    cached in the operator's keychain; a leak from a compromised
    workstation has the same blast radius as a personal token.
    """
    file_path = tmp_path / "ci_token.py"
    body = "MnOpQrStUvWxYzAbCdEfGhIjKl0123456789MnOpQrS"  # 43 alphanumeric
    secret = f"dp.ct.{body}"
    file_path.write_text(f'TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" in reasons


def test_secret_scanner_does_not_flag_short_doppler_prefix(tmp_path: Path) -> None:
    """Negative case: short ``dp.pt.`` strings (e.g. accidental fragments
    or operator-named placeholders) MUST NOT match the Doppler pattern.
    The strict 43-alphanumeric body length guard prevents this collision.
    """
    file_path = tmp_path / "config.py"
    # 12-char body — far too short to be a real Doppler token.
    not_doppler = "dp.pt.abc123def456"
    file_path.write_text(f'placeholder = "{not_doppler}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Buildkite Agent Token
# ---------------------------------------------------------------------------
#
# Format: ``bkat_<40+ alphanumeric body>``. Issued via
# buildkite.com/organizations/<org>/agents for Buildkite agent
# registration. A leak lets a network adversary register a rogue agent
# that drains the Buildkite job queue: every CI job (with whatever
# build-secret env vars the pipeline exposes) is delivered to
# attacker-controlled hardware. Blast radius = the entire CI estate.


def test_secret_scanner_detects_buildkite_agent_token(tmp_path: Path) -> None:
    """Buildkite Agent Token: ``bkat_<40+ alphanumeric body>``.

    Pre-fix: the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``bkat_<body>`` span (the underscore is in the alphabet)
    and reports ``Hochentropischer Token-String`` — losing the
    Buildkite-specific attribution that incident-response keys off.
    """
    file_path = tmp_path / "buildkite_agent.py"
    # Realistic synthetic Buildkite agent token: 5-char prefix +
    # 40-char alphanumeric body. Real tokens range 40-50 chars.
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"  # 40 alphanumeric
    assert len(body) == 40
    secret = f"bkat_{body}"
    file_path.write_text(
        f'BUILDKITE_AGENT_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Buildkite Agent Token"
    reasons = [f.reason for f in findings]
    assert "Buildkite Agent Token gefunden" in reasons, (
        f"Expected Buildkite-specific attribution, got reasons: "
        f"{reasons}. Buildkite agent tokens grant attackers the "
        "ability to register rogue agents and drain the CI job "
        "queue, delivering every pipeline's secrets to attacker-"
        "controlled hardware. Precise attribution accelerates "
        "revocation at buildkite.com/organizations/<org>/agents."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_buildkite_token_in_env_config(tmp_path: Path) -> None:
    """Buildkite tokens commonly appear in ``.env`` / shell-rc files;
    the detector must work regardless of surrounding context."""
    file_path = tmp_path / "production.env"
    body = "ZyXwVuTsRqPoNmLkJiHgFeDcBaZyXwVuTsRqPoNm"  # 40 alphanumeric
    secret = f"bkat_{body}"
    file_path.write_text(f"BUILDKITE_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Buildkite Agent Token gefunden" in reasons


def test_secret_scanner_does_not_flag_short_bkat_prefix(tmp_path: Path) -> None:
    """Negative case: short ``bkat_`` strings MUST NOT match the
    Buildkite pattern. The strict 40+ char body length guard prevents
    collision with operator-named identifiers."""
    file_path = tmp_path / "config.py"
    # 16-char body — too short to be a real Buildkite agent token.
    not_buildkite = "bkat_abcdef0123456789"
    file_path.write_text(f'placeholder = "{not_buildkite}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Buildkite Agent Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Netlify Personal Access Token
# ---------------------------------------------------------------------------
#
# Format: ``nfp_<40+ alphanumeric body>``. Issued via
# app.netlify.com/user/applications for full Netlify REST-API access.
# A leak grants the issuing user's full Netlify API scope: read/write
# every site's deploys, redirect rules, environment variables, build-
# hook URLs, edge-function code, and DNS records. The site-deploy
# primitive means an attacker can replace the live site with arbitrary
# HTML / JS, bypassing every downstream content gate.


def test_secret_scanner_detects_netlify_pat(tmp_path: Path) -> None:
    """Netlify PAT: ``nfp_<40+ alphanumeric body>``.

    Pre-fix: the entropy fallback matches the full ``nfp_<body>`` span
    (the underscore is in the alphabet) and reports
    ``Hochentropischer Token-String`` — losing the Netlify-specific
    attribution that incident-response keys off.
    """
    file_path = tmp_path / "netlify_deploy.py"
    # Realistic synthetic Netlify PAT: 4-char prefix + 40-char
    # alphanumeric body matching the documented modern format.
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"  # 40 alphanumeric
    assert len(body) == 40
    secret = f"nfp_{body}"
    file_path.write_text(
        f'NETLIFY_AUTH_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Netlify Personal Access Token"
    reasons = [f.reason for f in findings]
    assert "Netlify Personal Access Token gefunden" in reasons, (
        f"Expected Netlify-specific attribution, got reasons: "
        f"{reasons}. Netlify PATs grant full site-deploy access, "
        "letting attackers replace the live site with arbitrary "
        "HTML / JS. Precise attribution accelerates revocation at "
        "app.netlify.com/user/applications."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_nfp_prefix(tmp_path: Path) -> None:
    """Negative case: short ``nfp_`` strings MUST NOT match the Netlify
    pattern. The strict 40+ char body length guard prevents collision
    with operator-named identifiers."""
    file_path = tmp_path / "config.py"
    # 16-char body — too short to be a real Netlify PAT.
    not_netlify = "nfp_abcdef0123456789"
    file_path.write_text(f'placeholder = "{not_netlify}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Netlify Personal Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Mutual-exclusion regression: Doppler vs SendGrid (both dot-prefixed)
# ---------------------------------------------------------------------------


def test_doppler_does_not_misattribute_as_sendgrid(tmp_path: Path) -> None:
    """Mutual-exclusion regression: Doppler ``dp.<role>.`` shape uses two
    dot separators like SendGrid's ``SG.<22>.<43>`` 3-segment shape —
    but the SendGrid prefix is uppercase ``SG`` followed by a 22-char
    second segment, while Doppler uses lowercase ``dp`` followed by a
    short role identifier (2-5 chars). The patterns are mutually
    exclusive at the prefix level, so a real Doppler token MUST NOT
    be flagged as SendGrid."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfG"  # 43 alphanumeric
    secret = f"dp.pt.{body}"
    file_path.write_text(f'DOPPLER = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Doppler Token gefunden" in reasons
    assert "SendGrid API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_round7_taxonomy() -> None:
    """Audit invariant: each Round-7 token class must remain in
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
        # 2026-05-10 / Round 7 additions (this PR):
        "Doppler Token gefunden",
        "Buildkite Agent Token gefunden",
        "Netlify Personal Access Token gefunden",
    ]
    for reason in expected_reasons:
        assert reason in source, (
            f"src/utils/secret_scanner.py must register the {reason!r} "
            f"detector in _KNOWN_TOKENS. See "
            f"tests/test_sentinel_secret_scanner_drift_round7.py for the "
            f"per-issuer PoC and the rationale for each pattern."
        )
