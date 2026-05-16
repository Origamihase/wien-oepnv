"""Sentinel PoC: secret-scanner drift Round 14 — closes the **AWS
STS Service Bearer Token (``ABIA<16>``) prefix gap** in
``_AWS_ID_RE = re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])")``.

Round 13 (PR #1517) closed the WooCommerce ``ck_``/``cs_`` and
Mailchimp ``-us<N>`` adjacent-prefix candidates explicitly named-but-
deferred by Round 12. Round 13's closing checklist explicitly
documented ``_AWS_ID_RE`` as the canonical handler for the AWS
4-character credential-prefix family but enumerated only **three of
the four** AWS prefixes that grant API access:

  * ``AKIA`` — long-term IAM access key (covered)
  * ``ASIA`` — STS short-term access key (covered)
  * ``ACCA`` — context-specific credentials (covered)
  * ``ABIA`` — **AWS STS service bearer token (CREDENTIAL — NOT covered)**

Per the AWS IAM "Unique identifiers" documentation (and mirrored in
gitleaks / trufflehog / detect-secrets / aws-secret-detector default
rules), ``ABIA`` is the canonical 4-character prefix for **AWS STS
service bearer tokens** — bearer credentials issued by ``sts:GetServiceBearerToken``
that allow services to authenticate to other AWS APIs on behalf of a
user. They follow the identical 4+16=20 char format as ``AKIA``/
``ASIA``/``ACCA`` (4-char prefix + 16-char ``[A-Z0-9]`` body).

Threat model
------------

A leaked ``ABIA<16>`` bearer token in committed source / log artefacts /
CI debug snippets / hostile-PR fragments fails ALL of the secret
scanner's detection branches in ``_scan_content``:

  1. **``_PEM_RE``** — no PEM markers; no match.

  2. **``_KNOWN_TOKENS``** — pre-fix no entry covers ``ABIA``; no match.

  3. **``_AWS_ID_RE``** — pre-fix the regex enumerates only
     ``(AKIA|ASIA|ACCA)``; ``ABIA`` is silently excluded. NO match.

  4. **``_BEARER_RE``** — requires the literal ``Bearer `` keyword
     before the token body; a bare ABIA token without that prefix
     does NOT match.

  5. **``_SENSITIVE_ASSIGN_RE``** — the assignment heuristic only
     fires when the **variable name** carries a sensitive keyword
     (``key``/``secret``/``token``/etc.). For bare token leaks in
     log lines, JSON fixtures without sensitive keys, comments,
     documentation snippets, or arbitrary text — NO match.

  6. **``_HIGH_ENTROPY_RE``** — requires ``{24,}`` chars from
     ``[A-Za-z0-9+/=_-]``. The ABIA token format is exactly **20**
     chars (4-char prefix + 16-char body). The 20-char shape falls
     **below** the 24-char entropy threshold; NO match.

Net result pre-fix: a bare ``ABIAV2EXAMPLE12345AB`` leaked into a
log line, JSON fixture, error message, comment, or documentation
snippet is **silently undetected** by every detection branch — the
CI gate passes, the credential sits in the public repository
indefinitely, and the issuing user's full AWS scope (the bearer
token's authorized API access window) is exposed to every consumer.

Even when the token IS embedded in a sensitive-keyword assignment
(``AWS_BEARER_TOKEN = "ABIA…"``), the only finding emitted is the
generic ``Verdächtige Zuweisung eines potentiellen Secrets`` —
losing the AWS-specific issuer attribution that incident-response
playbooks key off (revocation flow at IAM > STS service bearer
token management — distinct from the IAM > Access keys flow used
to revoke ``AKIA`` tokens).

Real-world emission patterns:

  * **CloudTrail debug logs** capturing STS bearer token issuance
    via ``GetServiceBearerToken`` API responses.
  * **AWS SDK debug traces** with token strings embedded in HTTP
    request/response logging (`AWS_DEBUG=true` environment).
  * **Hand-typed examples in documentation / CI debug snippets /
    hostile-PR fragments** demonstrating service-to-service auth
    flows.
  * **Misconfigured boto3 credential files** that embed a bearer
    token in the ``aws_session_token`` field instead of the proper
    rotation flow.

Severity
--------

**HIGH** — detection-from-zero failure for an AWS credential prefix
that grants direct API access. The 20-char format places ABIA tokens
**below** the entropy fallback's 24-char minimum (every other AWS
prefix in the ``_AWS_ID_RE`` family is also 20 chars but they are
explicitly enumerated, so the enumeration is the only line of
defence). Every bare ABIA leak in a non-assignment context is silently
undetected; assignment-context leaks lose the AWS-specific issuer
attribution.

Fix
---

Add ABIA to ``_KNOWN_TOKENS`` with specific issuer attribution
``"AWS STS Service Bearer Token gefunden"`` (matching the existing
pattern for distinct-attribution tokens like ``Stripe Live Secret
Key`` vs ``Stripe Test Secret Key``, and ``GitHub Personal Access
Token`` vs ``GitHub App Server-to-Server Token``). The KNOWN_TOKENS
processing branch runs **before** ``_AWS_ID_RE`` in ``_scan_content``,
so ``is_covered`` correctly anchors the ABIA detection at the more-
specific issuer attribution.

Closing-checklist update
------------------------

The AWS 4-character credential-prefix family now covers all four
documented credential prefixes per the AWS IAM "Unique identifiers"
reference:

  * ``AKIA`` (long-term IAM access key) — _AWS_ID_RE
  * ``ASIA`` (STS short-term access key) — _AWS_ID_RE
  * ``ACCA`` (context-specific credentials) — _AWS_ID_RE
  * ``ABIA`` (STS service bearer token) — _KNOWN_TOKENS, THIS round closes.

Non-credential AWS prefixes (``AGPA`` group, ``AIDA`` IAM user,
``AIPA`` EC2 instance profile, ``ANPA`` SNS access point, ``ANVA``
account, ``APKA`` public key, ``AROA`` role, ``ARPA`` resource,
``ASCA`` certificate) are **identifiers, not credentials** — they
do not grant API access on their own and are deliberately deferred
indefinitely (would create false-positive noise in policy/IAM
documentation files without IR value).

Marker: SENTINEL_AWS_ABIA_PREFIX_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


SENTINEL_AWS_ABIA_PREFIX_DRIFT = "aws abia sts service bearer token prefix drift"


# ---------------------------------------------------------------------------
# 1. Bare ABIA token in plaintext context (silent-undetection branch)
# ---------------------------------------------------------------------------
#
# The most severe branch: a bare ``ABIA<16>`` token in arbitrary text
# (log line, JSON fixture, error message, documentation snippet,
# hostile-PR fragment) where no sensitive variable name anchors the
# generic assignment heuristic. Pre-fix every detection branch yields
# zero findings — the credential is silently undetected.


def test_secret_scanner_detects_aws_abia_token_in_plain_text(tmp_path: Path) -> None:
    """Bare ``ABIA<16>`` token in plaintext context: a log line / JSON
    fixture / documentation snippet with no sensitive variable name.

    Pre-fix this is **silently undetected** — the entropy fallback's
    24-char minimum rejects the 20-char ABIA shape, the assignment
    heuristic requires a sensitive variable name (none here), and
    ``_AWS_ID_RE`` enumerates only ``(AKIA|ASIA|ACCA)``. Post-fix
    every ABIA token receives the AWS-STS-Service-Bearer-Token
    attribution that incident-response keys off (revocation flow at
    IAM > STS service bearer token management).
    """
    file_path = tmp_path / "log_snippet.txt"
    abia_token = "ABIAV2EXAMPLE123ABCD"  # 4+16=20 chars, uppercase+digits
    assert len(abia_token) == 20
    # Plain log line: no assignment, no sensitive keyword, just a
    # bare token embedded in arbitrary text.
    file_path.write_text(
        f"2026-05-15T14:32:01Z DEBUG sts.client received bearer token "
        f"{abia_token} for service-to-service auth flow\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, (
        f"Bare ABIA token MUST be detected (currently silently undetected). "
        f"Got findings: {findings}"
    )
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" in reasons, (
        f"Expected AWS-STS-specific attribution, got reasons: {reasons}. "
        f"ABIA tokens are STS service bearer tokens (distinct revocation "
        f"flow from AKIA/ASIA/ACCA — IAM > STS service bearer token "
        f"management vs IAM > Access keys); precise attribution accelerates "
        f"incident response."
    )
    # Mask check: raw value must not leak.
    assert abia_token not in [f.match for f in findings]


def test_secret_scanner_detects_aws_abia_in_json_without_sensitive_key(
    tmp_path: Path,
) -> None:
    """ABIA token embedded in a JSON fixture without any sensitive
    variable name (the JSON key is generic — ``identifier``, ``ref``,
    or similar). Pre-fix the assignment heuristic doesn't fire; the
    entropy fallback rejects the 20-char shape; ``_AWS_ID_RE`` doesn't
    cover ABIA. The token slips through entirely.
    """
    file_path = tmp_path / "auth_fixture.json"
    abia_token = "ABIAV2QWERTY9876ZXCV"  # 4+16=20 chars
    assert len(abia_token) == 20
    file_path.write_text(
        f'{{"identifier": "{abia_token}", "issued_at": "2026-05-15T10:00:00Z"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" in reasons, (
        f"ABIA token in non-sensitive JSON key MUST be detected. "
        f"Got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 2. ABIA in assignment context (attribution-drift branch)
# ---------------------------------------------------------------------------
#
# Even when the variable name does carry a sensitive keyword and the
# generic assignment heuristic does fire, the AWS-specific attribution
# is lost — the finding reads ``Verdächtige Zuweisung eines
# potentiellen Secrets`` rather than the AWS-STS-specific reason that
# anchors the correct revocation flow.


def test_secret_scanner_assigns_aws_attribution_to_abia_token(
    tmp_path: Path,
) -> None:
    """ABIA token in an assignment with a sensitive variable name.
    Pre-fix the only finding was the generic
    ``Verdächtige Zuweisung eines potentiellen Secrets`` (correct that
    a secret was found, but no AWS-specific attribution to anchor
    revocation). Post-fix the ABIA-specific reason wins via the
    KNOWN_TOKENS-before-SENSITIVE_ASSIGN ordering and is_covered
    anchoring.
    """
    file_path = tmp_path / "aws_config.py"
    abia_token = "ABIAV2BEARERTOKENABCD"[:20]  # ensure exactly 20 chars
    assert len(abia_token) == 20
    file_path.write_text(
        f'AWS_BEARER_TOKEN = "{abia_token}"\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" in reasons, (
        f"Expected AWS-STS-specific attribution to override the generic "
        f"assignment heuristic, got reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# 3. Negative cases: no false positives on near-shape strings
# ---------------------------------------------------------------------------


def test_aws_abia_pattern_does_not_flag_short_abia_prefix(tmp_path: Path) -> None:
    """Negative case: a short ``ABIA<N>`` string with body shorter
    than 16 chars MUST NOT match. The strict 16-char body length guard
    prevents collision with operator-named placeholders like
    ``ABIAExample`` or ``ABIA_PREFIX``.
    """
    file_path = tmp_path / "config.py"
    not_abia = "ABIA12"  # 6 chars total
    file_path.write_text(f'placeholder = "{not_abia}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" not in reasons


def test_aws_abia_pattern_does_not_flag_lowercase_body(tmp_path: Path) -> None:
    """Negative case: a 20-char string with the ABIA prefix but a
    lowercase body MUST NOT match. AWS canonicalises all credential
    bodies to uppercase + digits per spec; a lowercase body is not
    a real AWS credential and would be a normal English word
    happening to start with ABIA (rare, but defensive).
    """
    file_path = tmp_path / "config.py"
    not_abia = "ABIAabcdefghijklmnop"  # 20 chars but lowercase body
    file_path.write_text(f'value = "{not_abia}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" not in reasons


def test_aws_abia_pattern_does_not_flag_mid_word_abia(tmp_path: Path) -> None:
    """Negative case: ``XABIAV2EXAMPLE123ABCD`` (the ABIA appears
    mid-word, not at a token boundary) MUST NOT match. The
    ``(?<![A-Za-z0-9])`` lookbehind anchor ensures the ABIA prefix
    is not preceded by an alphanumeric character.
    """
    file_path = tmp_path / "config.py"
    not_abia = "XABIAV2EXAMPLE123ABCD"  # leading X breaks the boundary
    file_path.write_text(f'value = "{not_abia}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS STS Service Bearer Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 4. Regression guards: existing AWS prefixes still detected with their
#    canonical attribution (no collision with the new ABIA pattern).
# ---------------------------------------------------------------------------


def test_aws_akia_still_detected_with_canonical_attribution(tmp_path: Path) -> None:
    """Regression guard: ``AKIA<16>`` continues to receive the
    canonical ``AWS Access Key ID gefunden`` attribution from
    ``_AWS_ID_RE`` (the new ABIA entry must not steal the AKIA
    attribution via overly-broad ``[A-Z0-9]`` matching)."""
    file_path = tmp_path / "aws.py"
    akia_token = "AKIAIOSFODNN7EXAMPLE"  # canonical AWS docs example
    assert len(akia_token) == 20
    file_path.write_text(f'AWS_ACCESS_KEY_ID = "{akia_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS Access Key ID gefunden" in reasons, (
        f"AKIA must keep its canonical attribution, got: {reasons}"
    )


def test_aws_asia_still_detected_with_canonical_attribution(tmp_path: Path) -> None:
    """Regression guard: ``ASIA<16>`` continues to receive the
    canonical ``AWS Access Key ID gefunden`` attribution."""
    file_path = tmp_path / "aws.py"
    asia_token = "ASIAQWERTYUIOPLKJHGF"
    assert len(asia_token) == 20
    file_path.write_text(f'AWS_SESSION_KEY = "{asia_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS Access Key ID gefunden" in reasons


def test_aws_acca_still_detected_with_canonical_attribution(tmp_path: Path) -> None:
    """Regression guard: ``ACCA<16>`` continues to receive the
    canonical ``AWS Access Key ID gefunden`` attribution."""
    file_path = tmp_path / "aws.py"
    acca_token = "ACCAZXCVBNMASDFGHJKL"
    assert len(acca_token) == 20
    file_path.write_text(f'AWS_CONTEXT_KEY = "{acca_token}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "AWS Access Key ID gefunden" in reasons


# ---------------------------------------------------------------------------
# 5. Inventory invariant: source-grep enforces ABIA presence in
#    _KNOWN_TOKENS so a future regression to the pre-fix shape (or a
#    refactor that drops the ABIA entry) fails this test until the
#    canonical detection is restored.
# ---------------------------------------------------------------------------


def test_secret_scanner_module_contains_abia_known_token_entry() -> None:
    """Inventory pin: the source of ``src/utils/secret_scanner.py``
    must contain a ``_KNOWN_TOKENS`` entry that anchors the ``ABIA``
    prefix. A future regression to the pre-fix shape (or a refactor
    that drops the ABIA detection without replacement) fails this
    test until the canonical detection is restored.
    """
    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "ABIA" in source, (
        "secret_scanner.py must contain an ABIA detection pattern "
        "(STS service bearer token credential prefix)"
    )
    assert "AWS STS Service Bearer Token" in source, (
        "secret_scanner.py must use the canonical 'AWS STS Service "
        "Bearer Token gefunden' attribution string"
    )
