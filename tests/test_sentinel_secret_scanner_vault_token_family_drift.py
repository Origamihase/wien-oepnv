"""Sentinel PoC: secret-scanner drift — closes the **HashiCorp Vault batch /
recovery token family gap** that the 2026-05-10 Round 6 (``hvs.`` HCP Vault
Secrets — PR #1467) named but did NOT enumerate. Both ``hvb.`` and ``hvr.``
share the same Vault cluster's auth backend with ``hvs.`` (issued via the
SAME ``auth/token/*`` API surface), the same on-the-wire encoding (base64url
body after the dotted prefix), and the same incident-response recovery
surface (Vault audit log review + token revocation flow), but pre-fix were
SILENTLY UNDETECTED across both detection branches:

  1. **``_KNOWN_TOKENS``** — only the ``hvs.`` Service Token shape was
     enumerated. The ``hvb.`` (Batch Token) and ``hvr.`` (Recovery Token)
     prefixes had NO entry, so the issuer-specific German attribution
     (``HashiCorp Vault Batch Token gefunden`` / ``HashiCorp Vault
     Recovery Token gefunden``) that incident-response triage keys off
     was never emitted.

  2. **``_HIGH_ENTROPY_RE``** — the body alphabet ``[A-Za-z0-9+/=_-]``
     EXCLUDES the literal ``.`` separator, so the entropy regex matches
     only the body span AFTER ``hvb.``/``hvr.`` as a generic
     ``Hochentropischer Token-String`` finding. The ``hvb.``/``hvr.``
     prefix is NOT in the matched span, so the Vault-specific issuer
     attribution is lost even when the entropy fallback does fire. For
     uniform-character-class bodies (all-lowercase / all-uppercase /
     all-digit, common for hash-derived or poorly-seeded RNG tokens —
     see Round 13 WooCommerce closing checklist documenting this as a
     general entropy-bypass class), the entropy fallback's
     ``_looks_like_secret`` heuristic requires ``min_categories=2`` and
     returns ``False`` for the body span — a fully silent-undetection
     branch for uniform bodies.

Per-prefix blast radius (HashiCorp Vault token family):

  * **hvb. (Batch Token)**: Lightweight, ephemeral token issued via
    ``POST /v1/auth/token/create`` with ``type=batch``. NOT written to
    Vault's storage backend — Vault encrypts the token's auth data
    into the token itself, scaling to high-throughput workloads
    (CI/CD pipelines, ephemeral container workloads, serverless
    function invocations — exactly the contexts most likely to leak
    in committed source code). Grants the issuing policy's full
    Vault scope for the token's TTL: read every KV secret the policy
    permits, generate dynamic secrets (DB creds, AWS STS, GCP SA
    tokens, PKI certificates), and transit-mount encrypt/decrypt
    arbitrary application data. Revocation: ``vault token revoke
    <token>``.

  * **hvr. (Recovery Token)**: HIGHEST severity in the Vault token
    family. Issued via ``POST /v1/sys/generate-recovery-token`` ONLY
    in HSM-backed or auto-unseal Vault deployments. Grants root-
    equivalent operations on a sealed/recovering Vault cluster,
    including the ability to GENERATE A NEW ROOT TOKEN
    (``POST /v1/sys/generate-root``) once unsealed — which becomes a
    persistent backdoor with FULL Vault administrative capability
    (read all secrets, modify all policies, disable audit logging,
    add persistent auth methods, mint dynamic secrets outliving the
    Vault breach). Recovery flow: ``POST /v1/sys/generate-recovery-
    token/attempt`` (cancel + restart) PLUS Shamir threshold
    re-keying of the recovery-key shares.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# 1. hvb. HashiCorp Vault Batch Token (lightweight ephemeral token)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_hashicorp_vault_batch_token(
    tmp_path: Path,
) -> None:
    """HashiCorp Vault Batch Token: ``hvb.<base64url body>``.

    Pre-fix: the entropy fallback flagged only the body span (the ``.``
    is OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``), reporting
    ``Hochentropischer Token-String`` — losing the Vault-Batch-Token-
    specific attribution that IR triage keys off (vault audit log review,
    `vault token revoke` flow, blast-radius scoping by token policy +
    TTL).
    """
    file_path = tmp_path / "vault_batch_client.py"
    # Realistic synthetic Vault batch token: 4-char prefix + 90-char
    # base64url body. The body uses ``[A-Za-z0-9_-]`` characters per
    # the base64url alphabet; real Vault tokens encode embedded auth
    # metadata (policy hash, TTL, accessor).
    body = (
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # 36 chars
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"  # 36 chars
        "_-AbCdEfGhIjKlMnOp"                     # 18 chars (90 total)
    )
    secret = f"hvb.{body}"
    assert len(secret) == 4 + 90
    file_path.write_text(f'VAULT_BATCH_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect HashiCorp Vault Batch Token"
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" in reasons, (
        f"Expected Vault-Batch-Token-specific attribution; got reasons: "
        f"{reasons}. Vault batch tokens grant the issuing policy's full "
        "Vault scope for the token TTL; precise attribution accelerates "
        "revocation via `vault token revoke <token>`."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_hvb_in_plain_log_line(tmp_path: Path) -> None:
    """``hvb.`` token in a plain log line — no sensitive variable name
    to anchor the generic assignment heuristic. The new detector MUST
    fire on the bare token regardless of surrounding context (mirrors
    Round 15 ``xoxe-`` plain-log-line test)."""
    file_path = tmp_path / "vault_audit.log"
    body = "X" * 50 + "y" * 40 + "Z9"  # 92 chars mixed-case
    secret = f"hvb.{body}"
    file_path.write_text(
        f"2026-05-17T10:00:00Z DEBUG vault.batch.issued token={secret}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" in reasons, (
        f"Bare hvb. token in log line MUST be attributed to Vault Batch "
        f"Token detector. Got reasons: {reasons}"
    )


def test_secret_scanner_does_not_flag_short_hvb_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``hvb.`` strings MUST NOT match the Vault
    pattern. The 30+ body length guard prevents collision with attribute
    access chains (``obj.hvb.foo``), filesystem paths, or accidental
    fragments mid-identifier (``hvb.x``)."""
    file_path = tmp_path / "config.py"
    # 12-char body — well below the canonical Vault batch token shape
    # (real bodies are typically 90+ chars).
    not_vault = "hvb.abc123def456"
    file_path.write_text(f'placeholder = "{not_vault}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 2. hvr. HashiCorp Vault Recovery Token (HIGHEST severity)
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_hashicorp_vault_recovery_token(
    tmp_path: Path,
) -> None:
    """HashiCorp Vault Recovery Token: ``hvr.<base64url body>``.

    Pre-fix: the entropy fallback flagged only the body span generically,
    losing the Vault-Recovery-Token-specific attribution. The IR surface
    for recovery tokens is HIGHEST severity — the holder can generate a
    new root token via ``POST /v1/sys/generate-root`` once Vault is
    unsealed, creating a persistent admin backdoor. Generic attribution
    delays the operator-required Shamir re-keying flow.
    """
    file_path = tmp_path / "vault_recovery_runbook.py"
    body = (
        "PaSsW0rDpAsSw0rDpAsSw0rDpAsSw0rDpAsSw0rD"  # 40 chars mixed
        "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"  # 40 chars
        "efghijklmnopqr"                              # 14 chars (94 total)
    )
    secret = f"hvr.{body}"
    file_path.write_text(
        f'VAULT_RECOVERY_TOKEN = "{secret}"  # NEVER COMMIT — rotate on leak',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect HashiCorp Vault Recovery Token"
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Recovery Token gefunden" in reasons, (
        f"Expected Vault-Recovery-Token-specific attribution (HIGHEST "
        f"severity in Vault token family); got reasons: {reasons}. "
        "Recovery tokens grant root-equivalent operations on a sealed "
        "Vault cluster, including the ability to generate a new root "
        "token via POST /v1/sys/generate-root — a persistent admin "
        "backdoor. Precise attribution accelerates the operator-required "
        "Shamir re-keying flow at POST /v1/sys/generate-recovery-token/"
        "attempt."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_hvr_in_plain_log_line(tmp_path: Path) -> None:
    """``hvr.`` token in a plain log line — no sensitive variable name
    to anchor the generic assignment heuristic. Vault recovery tokens
    can appear in operator runbooks, postmortem write-ups, or
    accidentally-committed shell history files."""
    file_path = tmp_path / "incident_postmortem.md"
    body = "A" * 40 + "b" * 40 + "C9_-"  # 84 chars mixed-class
    secret = f"hvr.{body}"
    file_path.write_text(
        f"During the incident the operator generated `{secret}` to unseal "
        "the HSM-backed cluster after the auto-unseal flow failed.\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Recovery Token gefunden" in reasons, (
        f"Bare hvr. token in operator runbook MUST be attributed to "
        f"Vault Recovery Token detector. Got reasons: {reasons}"
    )


def test_secret_scanner_does_not_flag_short_hvr_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``hvr.`` strings MUST NOT match the Vault
    pattern. The 30+ body length guard prevents collision with
    attribute access chains (``obj.hvr.foo``) or accidental fragments."""
    file_path = tmp_path / "config.py"
    not_vault = "hvr.abc123"
    file_path.write_text(f'placeholder = "{not_vault}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Recovery Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 3. Mutual-exclusion regressions: ``hvb.``/``hvr.`` MUST NOT misattribute
#    to other dot-prefixed token detectors (``hvs.``, ``SG.``, ``dp.pt.``)
# ---------------------------------------------------------------------------


def test_hvb_does_not_misattribute_as_hvs_service_token(
    tmp_path: Path,
) -> None:
    """Sibling disambiguation: ``hvb.`` is structurally identical to
    ``hvs.`` (4-char dotted prefix + base64url body) but the third
    character distinguishes batch vs. service token. The Vault Batch
    Token detector MUST fire and the HCP Vault Secrets Service Token
    detector MUST NOT fire on a real ``hvb.`` token."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" * 2 + "abcdefghij_-"
    secret = f"hvb.{body}"
    file_path.write_text(f'VAULT = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" in reasons
    assert "HCP Vault Secrets Token gefunden" not in reasons


def test_hvr_does_not_misattribute_as_hvs_service_token(
    tmp_path: Path,
) -> None:
    """Sibling disambiguation: ``hvr.`` is structurally identical to
    ``hvs.``/``hvb.`` but the third character distinguishes recovery
    from service/batch. The Vault Recovery Token detector MUST fire
    and the HCP Vault Secrets Service Token detector MUST NOT fire."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" * 2 + "0123456789_-"
    secret = f"hvr.{body}"
    file_path.write_text(f'VAULT = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Recovery Token gefunden" in reasons
    assert "HCP Vault Secrets Token gefunden" not in reasons


def test_hvb_does_not_misattribute_as_sendgrid(tmp_path: Path) -> None:
    """Mutual-exclusion regression: ``hvb.`` is dot-prefixed like
    SendGrid's ``SG.`` but uses lowercase + a different prefix and a
    single-segment body. A real Vault batch token MUST NOT be flagged
    as SendGrid (which requires the ``SG.<22>.<43>`` 3-segment shape)."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" * 2 + "abcdefghij"
    secret = f"hvb.{body}"
    file_path.write_text(f'VAULT = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" in reasons
    assert "SendGrid API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# 4. Uniform-character-class bodies (entropy-fallback bypass class)
# ---------------------------------------------------------------------------
#
# Per Round 13 (WooCommerce) closing checklist: uniform character-class
# bodies (all-lowercase / all-uppercase / all-digit) bypass the entropy
# fallback's ``_looks_like_secret`` heuristic entirely because the
# ``min_categories=2`` gate fails. Vault tokens are issued by a CSPRNG
# but a poorly-seeded test fixture or hand-typed placeholder that
# accidentally lands in a uniform character class would be SILENTLY
# UNDETECTED entirely without our new detector (the detector regex
# anchors on the prefix + body length, NOT on character-class diversity).


def test_secret_scanner_detects_hvb_with_uniform_lowercase_body(
    tmp_path: Path,
) -> None:
    """Uniform all-lowercase ``hvb.`` body — pre-fix bypassed entropy
    fallback entirely (``min_categories=2`` rejected single-class
    bodies). Post-fix the new ``hvb.`` detector anchors on prefix +
    body length, so the token is detected regardless of character-
    class diversity."""
    file_path = tmp_path / "vault.py"
    body = "abcdefghijklmnopqrstuvwxyz" * 4  # 104 chars all-lowercase
    secret = f"hvb.{body}"
    file_path.write_text(f'VAULT_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Batch Token gefunden" in reasons, (
        f"Uniform-lowercase hvb. body MUST be detected by the new "
        f"detector regardless of entropy heuristic. Got reasons: "
        f"{reasons}"
    )


def test_secret_scanner_detects_hvr_with_uniform_digit_body(
    tmp_path: Path,
) -> None:
    """Uniform all-digit ``hvr.`` body — pre-fix bypassed both
    detection branches (entropy fallback's ``min_categories=2``
    rejected, no ``_KNOWN_TOKENS`` entry for ``hvr.``). Post-fix
    detected."""
    file_path = tmp_path / "vault.py"
    body = "1234567890" * 9 + "_-"  # 92 chars mostly-digits
    secret = f"hvr.{body}"
    file_path.write_text(f'VAULT_TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HashiCorp Vault Recovery Token gefunden" in reasons, (
        f"Uniform-digit hvr. body MUST be detected by the new "
        f"detector. Got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 5. Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_vault_family_taxonomy() -> None:
    """Audit invariant: each Vault token-family attribution must remain
    in ``_KNOWN_TOKENS``.

    A future PR that drops one of these patterns silently re-opens the
    issuer-attribution gap that this round closes. This test pins the
    canonical set so any such regression fails at PR-review time.
    Mirrors the round-N taxonomy invariant pattern (e.g.
    ``test_known_tokens_round6_taxonomy`` for HCP Vault Secrets).
    """
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "utils" / "secret_scanner.py").read_text(
        encoding="utf-8"
    )

    expected_reasons = [
        # The pre-existing ``hvs.`` Service Token detector (Round 6,
        # PR #1467) — re-pinned here so the audit invariant covers the
        # FULL Vault token family.
        "HCP Vault Secrets Token gefunden",
        # 2026-05-17 / Vault family drift additions (this PR):
        "HashiCorp Vault Batch Token gefunden",
        "HashiCorp Vault Recovery Token gefunden",
    ]

    for reason in expected_reasons:
        assert reason in source, (
            f"Expected reason '{reason}' not found in _KNOWN_TOKENS. "
            "The Vault token-family detector regression check failed — "
            "see test docstring for the drift-closure rationale."
        )
