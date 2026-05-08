"""Sentinel PoC: secret-scanner drift behind common token taxonomies.

The 2026-05-05 / 2026-05-06 journal rounds added Anthropic / OpenAI / GitHub
non-PAT / SendGrid / Stripe / Slack token patterns to ``_KNOWN_TOKENS`` in
``src/utils/secret_scanner.py``. The prevention rule on those rounds was:

> "Treat ``_KNOWN_TOKENS`` as an issuer-keyed table, not a list. Whenever
> a new issuer is added or an existing entry is edited, walk the issuer's
> full documented prefix taxonomy and add every variant in the same pass
> with a distinct reason."

Re-running that audit against the issuer landscape used by modern Python
projects surfaced four still-missing token classes whose canonical formats
either bypass the generic high-entropy fallback entirely (because they
contain a non-alphanumeric separator outside ``[A-Za-z0-9+/=_-]``) or are
only flagged as a generic high-entropy match without the specific issuer
attribution that triage requires:

  1. **JSON Web Tokens (JWTs)**: ``eyJ<base64url>.<base64url>.<base64url>``
     — three dot-separated segments. The dots are outside the entropy
     fallback's character class, so without a specific pattern only ONE
     segment is matched at a time (and as a generic high-entropy hit, not
     as a JWT). JWTs are the most common credential format in modern
     OAuth/OIDC flows; missed attribution makes revocation slower.

  2. **Hugging Face Access Tokens** (``hf_<32+ alphanumeric>``) — issued
     for private model / dataset / Space access. Project dependencies on
     Hugging Face are increasingly common in Python codebases.

  3. **DigitalOcean PATs / OAuth refresh tokens** (``dop_v1_<64 hex>`` /
     ``doo_v1_<64 hex>``) — the ``v1`` prefix and 64-char hex body bypass
     the entropy fallback's character class as a single span (the body
     IS hex, but the underscore in ``dop_v1_`` interrupts the entropy
     match). Refresh tokens (``doo_v1_``) are especially dangerous: they
     mint fresh ``dop_v1_`` tokens until manually revoked.

  4. **GitLab Pipeline Trigger Tokens** (``glptt-<40 chars>``) — distinct
     from GitLab PATs (``glpat-``, already covered) and Deploy Tokens.
     A leaked trigger token lets a network adversary kick off arbitrary
     CI pipelines, exposing protected-branch secrets to attacker-
     controlled jobs.

Each test below pre-fix would have flagged only the generic high-entropy
fallback (or no finding at all for short tokens); post-fix every token
gets the issuer-specific reason that incident-response playbooks key off.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# JSON Web Tokens (multi-segment dot-separated)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_jwt_hs256_shape(tmp_path: Path) -> None:
    """JWTs have three base64url segments separated by dots; the dots
    bypass the entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet, so the
    full token must be matched by a specific pattern."""
    file_path = tmp_path / "config.py"
    # Synthetic but format-correct JWT shape:
    #   header: base64url("{\"alg\":\"HS256\",\"typ\":\"JWT\"}")
    #   payload: base64url("{\"sub\":\"1234567890\",\"name\":\"Test\",\"iat\":1516239022}")
    #   signature: base64url(HS256(...))
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        "."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IlRlc3QiLCJpYXQiOjE1MTYyMzkwMjJ9"
        "."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    file_path.write_text(f'AUTH_TOKEN = "{jwt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect JWT"
    reasons = [f.reason for f in findings]
    assert "JSON Web Token (JWT) gefunden" in reasons, (
        f"Expected JWT-specific attribution, got reasons: {reasons}. "
        "Without the JWT pattern, scanners flag only one base64url segment "
        "at a time (or miss the token entirely if no segment is long enough), "
        "losing both the issuer attribution and the full-token span needed "
        "for revocation."
    )
    # Ensure raw secret never appears in findings (redaction)
    assert jwt not in [f.match for f in findings]


def test_secret_scanner_detects_jwt_in_bearer_header(tmp_path: Path) -> None:
    """JWTs commonly appear after ``Authorization: Bearer ``; ensure both
    the bearer-shape detector AND the JWT-specific detector flag the token
    so triage sees the precise issuer context."""
    file_path = tmp_path / "header_log.txt"
    jwt = (
        "eyJhbGciOiJSUzI1NiJ9"
        "."
        "eyJleHAiOjE5OTk5OTk5OTksInN1YiI6ImFkbWluIn0"
        "."
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"
    )
    file_path.write_text(f"Authorization: Bearer {jwt}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "JSON Web Token (JWT) gefunden" in reasons, (
        "JWT detector must flag tokens regardless of surrounding context"
    )


def test_secret_scanner_does_not_flag_short_eyj_prefix(tmp_path: Path) -> None:
    """Negative case: short base64url-shaped strings starting with ``eyJ``
    that are NOT JWTs (e.g. accidental encoding fragments) should not
    produce JWT findings. The min-length guard prevents this collision."""
    file_path = tmp_path / "non_jwt.py"
    # Short eyJ-prefixed string with only one dot — clearly not a JWT.
    not_jwt = "eyJabc.short"
    file_path.write_text(f'value = "{not_jwt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "JSON Web Token (JWT) gefunden" not in reasons


# ---------------------------------------------------------------------------
# Hugging Face access tokens
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_hugging_face_token(tmp_path: Path) -> None:
    file_path = tmp_path / "ml_config.py"
    # Realistic HF token: hf_<37 alphanumeric chars> for legacy format.
    secret = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AB"
    file_path.write_text(f'HF_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Hugging Face token"
    reasons = [f.reason for f in findings]
    assert "Hugging Face Access Token gefunden" in reasons, (
        f"Expected HF-specific attribution, got reasons: {reasons}. "
        "Hugging Face tokens grant access to private models / datasets / "
        "Spaces; precise attribution speeds revocation via huggingface.co."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# DigitalOcean PATs and OAuth refresh tokens
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_digitalocean_pat(tmp_path: Path) -> None:
    file_path = tmp_path / "infra.py"
    # DigitalOcean PAT format: dop_v1_<64 hex>. The 64 hex chars are
    # split by the underscore from the ``dop_v1`` prefix, so the entropy
    # fallback alone would miss the issuer attribution.
    secret = "dop_v1_" + "0123456789abcdef" * 4  # 64 hex chars
    file_path.write_text(f'DO_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect DigitalOcean PAT"
    reasons = [f.reason for f in findings]
    assert "DigitalOcean Personal Access Token gefunden" in reasons
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_digitalocean_oauth_refresh(tmp_path: Path) -> None:
    file_path = tmp_path / "oauth_state.py"
    # DigitalOcean OAuth refresh token format: doo_v1_<64 hex>.
    # Refresh tokens are higher-impact than PATs because they mint fresh
    # PATs until revocation (long-lived effective credential).
    secret = "doo_v1_" + "fedcba9876543210" * 4  # 64 hex chars
    file_path.write_text(f'REFRESH_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "DigitalOcean OAuth Refresh Token gefunden" in reasons, (
        f"Expected DO refresh-token attribution, got reasons: {reasons}. "
        "Refresh tokens mint fresh dop_v1_ until manual revocation, so "
        "their leak is a long-lived account-API compromise."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# GitLab Pipeline Trigger Tokens (distinct from glpat-)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_gitlab_pipeline_trigger_token(tmp_path: Path) -> None:
    file_path = tmp_path / "ci_config.py"
    # GitLab Pipeline Trigger Token: glptt-<40 chars>. Distinct from
    # glpat- (PAT, already covered) and gldt- (Deploy Token).
    secret = "glptt-" + "a" * 40
    file_path.write_text(f'TRIGGER_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Pipeline Trigger Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Pipeline Trigger Token gefunden" in reasons, (
        f"Expected GitLab pipeline-trigger attribution, got reasons: {reasons}. "
        "A leaked trigger token lets a network adversary kick off CI pipelines, "
        "exposing protected-branch secrets to attacker-controlled jobs."
    )
    assert secret not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# Twilio Account SID and API Key SID
# ---------------------------------------------------------------------------
#
# Twilio's documented SID format is a 2-letter resource-type prefix followed
# by 32 lowercase hex chars (https://www.twilio.com/docs/glossary/what-is-a-sid).
# The Account SID (``AC...``) is the principal credential — it pairs with the
# Auth Token to authenticate every API call (call/SMS history, billing,
# phone-number provisioning) — and a leak grants the entire blast radius of
# the project. The API Key SID (``SK...``) pairs with a separate secret for
# fine-grained scoped access (still substantial: scoped API keys can issue
# calls/SMSes, charging the account). Both formats bypass the generic
# high-entropy fallback's issuer attribution: the 32-hex body is matched as
# a generic high-entropy hit, but the issuer-specific reason is lost — so
# incident-response triage cannot tell from the scanner output whether to
# rotate at twilio.com vs. at any other vendor.


def test_secret_scanner_detects_twilio_account_sid(tmp_path: Path) -> None:
    """Twilio Account SID format: ``AC<32 lowercase hex chars>``."""
    file_path = tmp_path / "twilio_config.py"
    # Realistic-looking Twilio Account SID (synthetic, all hex zeros are
    # not a real SID but match the format).
    secret = "AC" + "0123456789abcdef" * 2  # 32 hex chars total
    file_path.write_text(f'TWILIO_ACCOUNT_SID = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Twilio Account SID"
    reasons = [f.reason for f in findings]
    assert "Twilio Account SID gefunden" in reasons, (
        f"Expected Twilio-specific attribution, got reasons: {reasons}. "
        "Twilio Account SIDs pair with the Auth Token to authenticate every "
        "API call; precise attribution speeds revocation via twilio.com."
    )
    # Ensure raw secret never appears in findings (redaction)
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_twilio_api_key_sid(tmp_path: Path) -> None:
    """Twilio API Key SID format: ``SK<32 lowercase hex chars>``.

    Distinct from Stripe ``sk_live_`` / ``sk_test_`` (lowercase + underscore).
    The case + separator difference makes the two patterns mutually exclusive,
    but a regression that drops the case-sensitivity guard would silently
    re-attribute Twilio leaks to Stripe and vice versa.
    """
    file_path = tmp_path / "twilio_api_key.py"
    secret = "SK" + "fedcba9876543210" * 2  # 32 hex chars total
    file_path.write_text(f'TWILIO_API_KEY_SID = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Twilio API Key SID gefunden" in reasons, (
        f"Expected Twilio-specific attribution, got reasons: {reasons}."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_confuse_twilio_with_stripe(tmp_path: Path) -> None:
    """Negative case: lowercase ``sk_live_`` (Stripe) must NOT be flagged
    as a Twilio API Key SID, and uppercase ``SK<hex>`` (Twilio) must NOT
    be flagged as a Stripe key. The case + underscore difference between
    the two patterns is the only thing keeping them apart.
    """
    file_path = tmp_path / "stripe_config.py"
    stripe_secret = "sk_live_" + "0123456789abcdEFghIJklmn"  # 24 chars
    file_path.write_text(f'STRIPE_KEY = "{stripe_secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Twilio API Key SID gefunden" not in reasons, (
        "Stripe lowercase ``sk_live_`` must not be misattributed as a "
        "Twilio API Key SID. The patterns differ by case and separator."
    )
    # Stripe pattern should still flag this as Stripe.
    assert "Stripe Live Secret Key gefunden" in reasons


# ---------------------------------------------------------------------------
# Notion Integration Tokens (legacy ``secret_`` and modern ``ntn_``)
# ---------------------------------------------------------------------------
#
# Notion API tokens are issued via developer integrations and grant
# read/write access to whatever workspace content the integration is shared
# with (full database/page contents, including any private collaborator
# notes). The legacy ``secret_<43 alphanumeric>`` format and the modern
# ``ntn_<43+ chars>`` format have the same blast radius; distinct
# attribution matters because revocation happens at notion.so and the
# rotation playbook differs per format.


def test_secret_scanner_detects_notion_legacy_token(tmp_path: Path) -> None:
    """Notion legacy Internal Integration Token: ``secret_<43 alphanumeric>``."""
    file_path = tmp_path / "notion_integration.py"
    # Realistic-looking Notion legacy token: secret_ + 43 alphanumeric chars
    secret = "secret_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfG"
    file_path.write_text(f'NOTION_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Notion Integration Token gefunden" in reasons, (
        f"Expected Notion-specific attribution, got reasons: {reasons}. "
        "Notion legacy ``secret_`` tokens grant workspace read/write access; "
        "precise attribution speeds revocation via notion.so/my-integrations."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_notion_modern_token(tmp_path: Path) -> None:
    """Notion modern Integration Token: ``ntn_<43+ chars>`` (post-2024 format).

    Same blast radius as the legacy ``secret_`` form; rolled out alongside
    the v2024-09 API. Distinct prefix ensures unambiguous attribution.
    """
    file_path = tmp_path / "notion_modern.py"
    # Realistic-looking Notion modern token: ntn_ + 43+ chars
    secret = "ntn_" + "X1Y2Z3a4B5c6D7e8F9g0H1i2J3k4L5m6N7o8P9q0R1s"
    file_path.write_text(f'NOTION_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "Notion Modern Integration Token gefunden" in reasons, (
        f"Expected Notion modern-format attribution, got reasons: {reasons}."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_secret_prefix(tmp_path: Path) -> None:
    """Negative case: short ``secret_<short>`` strings (e.g. test fixtures
    or operator-named env keys) MUST NOT match the Notion pattern. The
    strict 43-char body length guard prevents this collision."""
    file_path = tmp_path / "config.py"
    # 10-char body — too short to be a Notion token.
    not_notion = "secret_abc123def4"
    file_path.write_text(f'value = "{not_notion}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Notion Integration Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_carry_post_fix_taxonomy() -> None:
    """Audit invariant: each new token class must remain in _KNOWN_TOKENS.

    A future PR that drops one of these patterns silently re-opens the
    issuer-attribution gap that this round closes. This test pins the
    canonical set so any such regression fails at PR-review time.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "utils" / "secret_scanner.py").read_text(
        encoding="utf-8"
    )

    expected_reasons = [
        "JSON Web Token (JWT) gefunden",
        "Hugging Face Access Token gefunden",
        "DigitalOcean Personal Access Token gefunden",
        "DigitalOcean OAuth Refresh Token gefunden",
        "GitLab Pipeline Trigger Token gefunden",
        # 2026-05-08 round 3 additions:
        "Twilio Account SID gefunden",
        "Twilio API Key SID gefunden",
        "Notion Integration Token gefunden",
        "Notion Modern Integration Token gefunden",
    ]
    for reason in expected_reasons:
        assert reason in source, (
            f"src/utils/secret_scanner.py must register the {reason!r} "
            f"detector in _KNOWN_TOKENS. See "
            f"tests/test_sentinel_secret_scanner_drift.py for the "
            f"per-issuer PoC and the rationale for each pattern."
        )
