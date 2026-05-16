"""Sentinel PoC: secret-scanner drift — closes the **long-base64 entropy
fallback gap** that silently undetects multi-hundred-character credentials
(AWS STS Session Tokens, GCP service-account body, long opaque OAuth /
service tokens) whose unique-character count is capped by the base64
alphabet ceiling (~64 chars) while ``_looks_like_secret`` demanded
``len(set(candidate)) >= max(6, len(candidate) // 4)``.

Threat model
------------

A leaked **AWS STS Session Token** is the canonical example. Production
session tokens are 200-700+ chars of base64url-with-padding output from
``sts:AssumeRole`` / ``sts:GetSessionToken``. The body uses the
``[A-Za-z0-9+/=]`` alphabet — at most 65 unique characters total. The
pre-fix heuristic in ``_looks_like_secret`` demanded
``len(set(candidate)) >= len(candidate) // 4``, which for a 287-char
token requires **71 unique characters** — impossible under the base64
ceiling. The token is therefore silently undetected by both the entropy
fallback (``_HIGH_ENTROPY_RE``) AND the assignment heuristic
(``_SENSITIVE_ASSIGN_RE``), so a CI alert never fires.

Pre-fix detection branch failure modes for a leaked AWS session token:

  1. **``_PEM_RE``** — no PEM markers; no match.
  2. **``_KNOWN_TOKENS``** — AWS session tokens have NO canonical
     short prefix (unlike ``AKIA``/``ASIA``/``ACCA``/``ABIA`` access-
     key IDs, which the Round-14 enumeration covers); no match.
  3. **``_AWS_ID_RE``** — matches only the 20-char ``A[KCBS]IA``
     access-key-ID format, not the variable-length session-token body.
  4. **``_BEARER_RE``** — requires the literal ``Bearer`` prefix;
     bare AWS session tokens in env / log / JSON shapes don't carry it.
  5. **``_SENSITIVE_ASSIGN_RE``** — fires when the variable name has
     a sensitive keyword (``token``/``secret``/etc.). For typical
     leaks (``AWS_SESSION_TOKEN=...`` in ``.env``) the regex matches,
     but the captured candidate still flows through
     ``_looks_like_secret(is_assignment=True)`` which hits the SAME
     uniqueness gate and rejects the long-base64 body.
  6. **``_HIGH_ENTROPY_RE``** — body matches the alphabet, the
     candidate is forwarded to ``_looks_like_secret(is_assignment=
     False)`` which rejects on uniqueness for the same reason.

Per-prefix blast radius:

  * **AWS STS Session Token**: A leaked session token plus the
    Access-Key-ID / Secret-Access-Key triple grants temporary AWS
    API access at the role's permission level for the token's
    lifetime (15 min - 12 h depending on the originating session).
    Even alone (without the matching key+secret), the session token
    can be used for direct API calls because AWS treats it as a
    self-contained bearer credential during its validity window.
    Blast radius = whatever IAM scope the role had — typically
    read/write S3, RDS, EC2 management, Lambda invocation, KMS
    decrypt. The revocation flow is **token revocation via
    AWS STS** plus rotating the underlying access keys — distinct
    from the Round-14 ``ABIA`` / ``ASIA`` static-credential
    revocation flow.

  * **GCP service-account JSON ``private_key`` body**: Already
    covered structurally by ``_PEM_RE`` (the embedded
    ``-----BEGIN PRIVATE KEY-----`` block). Not regressed by this
    fix — the PEM detector fires before the entropy fallback.

  * **Long opaque OAuth tokens** (e.g. multi-segment Auth0, custom
    IdP tokens, long Microsoft Graph access tokens, JWTs without
    the canonical ``eyJ`` header): also silently undetected when
    the body exceeds ~250 chars. Same fix applies.

Severity
--------

**HIGH** for the AWS session-token case — silent-undetection in CI
means a leak slips into ``main`` without alerting, sits in the public
repo's history forever, and stays exploitable until the natural session
expiry (12 h max). For active red-team / hostile-PR scenarios, the
window is enough to pivot to higher-privilege credentials via
``IAM:CreateAccessKey`` or other privilege-escalation API calls during
the validity window.

Fix
---

Cap the uniqueness requirement at 32 characters:

    if len(set(candidate)) < max(6, min(len(candidate) // 4, 32)):
        return False

This preserves the pre-fix behaviour for **short** candidates (24-127
chars, the typical range for canonical secret formats like JWT segments,
GitHub PATs, Stripe keys) and **only** loosens the requirement for
**long** candidates (128+ chars) where the base64 alphabet ceiling
would otherwise make the original ratio mathematically impossible to
satisfy. False-positive risk is bounded by the fact that
``_HIGH_ENTROPY_RE`` only matches contiguous spans of
``[A-Za-z0-9+/=_-]`` — natural-language text breaks at punctuation /
whitespace, so long English passages cannot match. The remaining
false-positive class (long base64-encoded image data, ZIP archives
inlined as base64) is already filtered by ``_is_binary`` for binary
files; the small remaining surface (data URIs in HTML/Markdown) is
easily added to ``.secret-scan-ignore`` if it matters.

The 32-char floor is intentionally generous: every realistic secret
with at least 128 chars of length AND 32+ distinct characters is a
high-entropy credential by any reasonable measure. The fix lets the
detector catch the silently-undetected long-base64 class while
preserving every existing test's behaviour.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from src.utils.secret_scanner import _looks_like_secret, _scan_content, scan_repository


# ---------------------------------------------------------------------------
# 1. Direct unit test on ``_looks_like_secret``: long base64 body must pass
# ---------------------------------------------------------------------------


def test_looks_like_secret_accepts_long_base64_body() -> None:
    """Long base64 strings must pass the entropy heuristic.

    Pre-fix the uniqueness check ``len(set(candidate)) >= len(candidate)
    // 4`` was unsatisfiable for ANY string > 256 chars whose alphabet
    is capped at the base64 ceiling (64-65 unique chars). Post-fix
    the requirement is capped at 32 so realistic high-entropy long
    secrets pass.
    """
    # Real AWS session token shape: 287 chars, 64 unique chars.
    long_token = (
        "FwoGZXIvYXdzEAYaDM/QmwAJzxxxxxxxxKpYHCJ1IiTUDQNS9rxA1RkkLLA"
        "taylJTNFKLfa3PuD3OL2Dxsq+CDgL5C7ZAYNNX5sH3FKpJrYf4WMnCN42L"
        "cj+VtdY3VyDHN3ARZQrKf3Lz6Hr8DC3w4VYZjs/+rB+9b3iE6XBT4mhSlz"
        "+yK0xqK0Cd5/Vqfu1Tqq2HM5UWFEHC6yKHqQWBHQTSEqkkdoF8x6gNoH/"
        "+nXG5cF8H3OQ+8s5d8a4ZHHzv7tk6qDtVR2J7E7LxKaxjJxK+zPYTk="
    )
    assert len(long_token) > 256, (
        "Token must be long enough to trigger the gap (>256 chars)"
    )
    assert len(set(long_token)) <= 65, (
        "Token uses the base64 alphabet ceiling"
    )
    assert _looks_like_secret(long_token, is_assignment=False), (
        f"Long base64 token ({len(long_token)} chars, {len(set(long_token))} "
        f"unique) must pass the entropy heuristic. Pre-fix required "
        f"{max(6, len(long_token) // 4)} unique chars — impossible "
        f"under the base64 alphabet ceiling. Post-fix the cap at 32 "
        f"makes the requirement satisfiable."
    )
    assert _looks_like_secret(long_token, is_assignment=True), (
        "Same token must also pass in assignment context (the cap "
        "applies to both branches)."
    )


def test_looks_like_secret_rejects_long_repetitive_string() -> None:
    """Long but low-diversity strings (boilerplate, repetition) must
    still be rejected.

    The fix raises the floor for short candidates and caps the
    requirement at 32 — but does NOT lower it below 6. A 500-char
    string of only 10 unique chars (e.g. ``abababab...`` style
    boilerplate) must still fail the uniqueness check.
    """
    repetitive = "abcdefghij" * 50  # 500 chars, 10 unique
    assert len(repetitive) == 500
    assert len(set(repetitive)) == 10
    assert not _looks_like_secret(repetitive, is_assignment=False), (
        "Low-diversity repetitive strings must still be rejected — "
        "the cap at 32 does NOT regress to allowing all long strings."
    )


def test_looks_like_secret_preserves_short_secret_behaviour() -> None:
    """Strings <= 127 chars must keep their pre-fix behaviour exactly.

    The fix only changes behaviour for length >= 128 (the breakpoint
    where ``len // 4`` exceeds 32). Short canonical secret formats
    (JWT segments, GitHub PATs, Stripe keys, etc.) must keep their
    pre-fix accept/reject decisions byte-for-byte.
    """
    # 24-char ``ghp_`` body shape: 24 unique mixed-case alphanumeric.
    short_realistic = "AbCdEfGhIjKlMnOpQrStUvWx"
    assert len(short_realistic) == 24
    assert _looks_like_secret(short_realistic, is_assignment=False), (
        "Realistic short secret must pass (unchanged behaviour)."
    )

    # 24-char with only 6 unique (borderline case at the floor).
    short_borderline = "AbCdEfAbCdEfAbCdEfAbCdEf"
    assert len(short_borderline) == 24
    assert len(set(short_borderline)) == 6
    assert _looks_like_secret(short_borderline, is_assignment=False), (
        "Borderline 6-unique-char short string passes (matches the 6-char floor)."
    )

    # 24-char with only 5 unique (below the floor).
    short_too_uniform = "AbCdEAbCdEAbCdEAbCdEAbCd"
    assert len(short_too_uniform) == 24
    assert len(set(short_too_uniform)) == 5
    assert not _looks_like_secret(short_too_uniform, is_assignment=False), (
        "Below-floor uniform short string must still be rejected."
    )


# ---------------------------------------------------------------------------
# 2. End-to-end scan: a real-shape AWS session token must be detected
# ---------------------------------------------------------------------------


def _make_realistic_aws_session_token(seed: int = 42) -> str:
    """Generate a deterministic 287-char base64url-with-padding string.

    Mirrors the on-the-wire shape of an AWS STS session token: 287
    chars (within the typical 200-700 range), base64 alphabet, ~64
    distinct characters. The seed parameter keeps the test
    deterministic across CI runs.
    """
    rng = base64.b64encode(
        bytes((seed * i + 7) % 256 for i in range(220))
    ).decode("ascii")
    # Trim/pad to exactly 287 chars.
    return rng[:287] if len(rng) >= 287 else rng + ("A" * (287 - len(rng)))


def test_scan_detects_aws_session_token_in_assignment(tmp_path: Path) -> None:
    """Real-shape AWS session token in an env assignment must be flagged.

    Pre-fix the 287-char body's uniqueness count (~64) fell below
    ``len // 4 = 71``, so ``_looks_like_secret(is_assignment=True)``
    rejected it. Both the assignment regex AND the entropy fallback
    therefore produced ZERO findings for the leaked token.
    """
    token = _make_realistic_aws_session_token()
    assert len(token) >= 200
    file_path = tmp_path / "aws_credentials.env"
    file_path.write_text(f"AWS_SESSION_TOKEN={token}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    assert findings, (
        f"Long AWS session token (len={len(token)}, unique="
        f"{len(set(token))}) must be detected by the secret scanner. "
        f"Pre-fix _looks_like_secret rejected it because the uniqueness "
        f"requirement (len//4={len(token) // 4}) exceeded the base64 "
        f"alphabet ceiling (~64). The credential silently slipped past "
        f"every detection branch."
    )
    # The raw token must be masked in the finding (security hygiene).
    assert all(
        token not in f.match for f in findings
    ), "Raw secret value must be masked in findings"


def test_scan_detects_aws_session_token_in_plain_log(tmp_path: Path) -> None:
    """Bare AWS session token in plain context (log line, JSON without
    sensitive variable name) must still be detected via the entropy
    fallback.

    This is the canonical leak shape: debug logging accidentally dumps
    the token without a sensitive-keyword variable name. Pre-fix the
    entropy fallback fired ``_HIGH_ENTROPY_RE`` BUT
    ``_looks_like_secret(is_assignment=False)`` rejected the body for
    the uniqueness reason. Post-fix the entropy fallback catches it.
    """
    token = _make_realistic_aws_session_token(seed=11)
    file_path = tmp_path / "debug.log"
    # No sensitive keyword — the assignment regex does NOT fire here.
    file_path.write_text(
        f"2025-12-01 12:00:00 INFO request completed payload {token}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    assert findings, (
        "Bare AWS session token in a log line (no sensitive variable "
        "name) must be caught by the entropy fallback. Pre-fix the "
        "long-base64 body was silently undetected entirely."
    )


# ---------------------------------------------------------------------------
# 3. Negative cases: no regression on long false-positive shapes
# ---------------------------------------------------------------------------


def test_scan_does_not_flag_long_natural_language_paragraph(
    tmp_path: Path,
) -> None:
    """Natural-language text must NOT be flagged regardless of length.

    The entropy regex ``_HIGH_ENTROPY_RE`` only matches contiguous
    ``[A-Za-z0-9+/=_-]`` spans — natural-language passages break at
    spaces and punctuation, so no long contiguous span matches. The
    cap-at-32 fix does NOT change this — it only affects spans that
    DO match the alphabet regex.
    """
    file_path = tmp_path / "comment.txt"
    text = (
        "The quick brown fox jumps over the lazy dog. " * 20
        + "Pack my box with five dozen liquor jugs. " * 20
    )
    file_path.write_text(text, encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    # Filter out any unrelated findings (in practice should be zero).
    high_entropy_findings = [
        f for f in findings if "Hochentropisch" in f.reason
    ]
    assert not high_entropy_findings, (
        f"Natural-language text must not be flagged: {high_entropy_findings}"
    )


def test_scan_does_not_flag_long_repetitive_alphanumeric_blob(
    tmp_path: Path,
) -> None:
    """A long repetitive alphanumeric blob (e.g. accidentally-committed
    boilerplate) must NOT be flagged because the 32-char uniqueness
    floor still applies."""
    file_path = tmp_path / "boilerplate.txt"
    # 500 chars, only 10 unique
    blob = "abcdefghij" * 50
    file_path.write_text(f"data = {blob}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    high_entropy_findings = [
        f for f in findings if "Hochentropisch" in f.reason
    ]
    assert not high_entropy_findings, (
        f"Low-diversity long blob must not be flagged. The 32-char "
        f"uniqueness floor in the fix preserves rejection of "
        f"repetitive content: {high_entropy_findings}"
    )


# ---------------------------------------------------------------------------
# 4. Inventory invariant: long base64 with 32+ unique chars passes
# ---------------------------------------------------------------------------


def test_scan_detects_arbitrary_long_base64_with_high_diversity(
    tmp_path: Path,
) -> None:
    """Any 200+ char base64 string with at least 32 distinct characters
    must be flagged by the entropy fallback.

    This generalises the AWS-session-token case to the broader class
    of long opaque tokens (Auth0 access tokens, custom IdP tokens,
    long Microsoft Graph tokens, etc.) that share the same
    ``len // 4 > 64`` failure mode.
    """
    file_path = tmp_path / "tokens.txt"
    # 400 chars, ~62 unique
    token = base64.b64encode(os.urandom(300)).decode("ascii")[:400]
    assert len(token) == 400
    assert len(set(token)) >= 32
    file_path.write_text(f"opaque_token = {token}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    assert findings, (
        f"Long base64 token (len={len(token)}, unique="
        f"{len(set(token))}) must be detected. The cap-at-32 fix "
        f"closes the uniqueness gap for the entire long-opaque-token "
        f"class, not just AWS session tokens."
    )


def test_scan_content_direct_invocation_returns_finding() -> None:
    """Direct call to ``_scan_content`` confirms the entropy fallback fires.

    Companion to the ``scan_repository`` end-to-end tests above:
    invokes the internal helper directly to pin the behaviour without
    going through the file-walking layer. Useful for downstream callers
    that compose the scanner programmatically.
    """
    token = _make_realistic_aws_session_token(seed=99)
    content = f"export AWS_SESSION_TOKEN={token}\n"
    findings = _scan_content(content)
    assert findings, (
        "_scan_content must produce at least one finding for a long "
        "AWS-session-token-shaped string."
    )
