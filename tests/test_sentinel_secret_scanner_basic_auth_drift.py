"""Sentinel PoC: missing ``_BASIC_AUTH_RE`` detector — RFC 7617 HTTP Basic
Authentication credential leaks (``Authorization: Basic <base64(user:password)>``)
slip past the secret scanner with attribution drift OR silent undetection,
depending on the body shape.

This round closes the **named-but-deferred** adjacent-detector candidate from
the 2026-05-15 Bearer case-insensitivity drift round (sibling PR
``security(secret-scanner): pin Bearer detector to case-insensitive per RFC
7235``). That round explicitly named the next-round target::

    **Next-round candidates (named-but-deferred):** **Basic Auth detector**
    (`(?i)Basic\\s+[A-Za-z0-9+/=]{16,}`) — RFC 7617 HTTP Basic Authentication
    credential leak (`Authorization: Basic <base64(user:password)>`). The
    base64-encoded body falls inside `_HIGH_ENTROPY_RE`'s alphabet so the
    body matches generically, but the Basic-Auth-specific attribution
    (decode the base64 to recover the user:password pair, distinct
    revocation flow — rotate the user's password and any service-account
    credential) is lost. Adjacent-detector candidate with the same `(?i)`
    case-insensitivity contract as this round.

RFC 7617 §2 references RFC 7235 §2.1 — every conforming HTTP receiver MUST
match the ``Basic`` auth-scheme literal case-insensitively, so
``BASIC <body>`` / ``basic <body>`` / ``BaSiC <body>`` are all legitimate
canonical HTTP Authorization-header shapes on the wire.

Threat model
------------

A leaked ``Authorization: Basic <body>`` (where the body is a base64-encoded
``username:password`` pair) in committed source / log artefacts / CI debug
snippets / hostile-PR fragments fails the existing detection branches in
two ways:

1. **Attribution drift (24+ char body — the common case)** — the body
   matches ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically
   and lands as a ``Hochentropischer Token-String`` finding. The
   Basic-Auth-specific attribution is lost — incident-response triage
   must guess whether the leaked entropy span is a Bearer token (revoke
   via the issuing IdP), a Basic Auth credential (rotate the user's
   password), an opaque API key (revoke at the vendor dashboard), or
   some other secret. Each has a different revocation flow.

2. **Silent undetection (16-23 char body OR all-letter body)** — short
   base64 bodies (``YWRtaW46cGFzc3dvcmQ=`` = ``admin:password`` is only
   20 chars) fall BELOW the entropy fallback's 24-char minimum. AND
   the entropy fallback's ``candidate.isalpha()`` skip rejects
   all-letter bodies (a legitimate base64 encoding of bytes that happen
   to produce only ``[A-Za-z]`` chars — e.g. encoding ``adminuser:plaintext``
   produces ``YWRtaW51c2VyOnBsYWludGV4dA==`` which is heterogeneous, but a
   binary payload encoding to all-letters IS possible). Pre-fix the
   credential is **silently undetected** entirely — the CI gate passes,
   the credential sits in the public repo, and the underlying
   ``username:password`` pair is trivially recovered via base64 decode.

The cleartext-credential recovery is what makes this distinct from
Bearer Auth: with a leaked Bearer the attacker holds an opaque token
(no further info), but with a leaked Basic Auth the attacker recovers
the *username* AND the *plaintext password* — both of which may be
reused across other systems by the same user (password reuse is the
canonical real-world IR-amplifier for credential leaks).

Severity
--------

**MEDIUM-HIGH** — Basic Auth credentials carry whatever scope the
issuing service grants the username:password pair. The high-severity
case is the silent-undetection branch (short bodies / all-letter
bodies); the attribution-drift branch is medium-severity (the body is
still caught generically, but the Basic-Auth-specific reason that
determines which rotation playbook applies is lost). Mitigated only by
the requirement that the leaked credential live alongside a ``Basic``
auth-scheme literal in the same content blob; in practice this covers
every leak through automation-generated fixtures, lower-case-normalising
loggers, hostile-PR-introduced fragments, and curl/httpie debug logs.

Fix
---

Add a Basic Auth detector mirroring the Bearer detector's case-insensitive
contract::

    _BASIC_AUTH_RE = re.compile(r"(?i)Basic\\s+([A-Za-z0-9+/=]{16,})")

Process matches in ``_scan_content`` with the same ``is_assignment=True``
``_looks_like_secret`` filter (allowing uniform-character-class bodies)
and emit findings with the Basic-Auth-specific reason
``"HTTP Basic Authentication Credential gefunden"``.

The fix has zero false-positive risk: the body alphabet
``[A-Za-z0-9+/=]`` is the canonical base64 alphabet per RFC 4648 §4
(standard base64; URL-safe RFC 4648 §5 uses ``_-`` instead of ``+/``
but is rare in Authorization headers per RFC 7617 §2). The structural
disambiguator (``\\s+`` + 16+ contiguous base64 chars) prevents
natural-language matches — sentences like "Basic understanding of..." do
NOT have 16+ contiguous chars from the base64 alphabet following the
literal.

Marker: SENTINEL_BASIC_AUTH_DRIFT.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_BASIC_AUTH_DRIFT = "rfc-7617 basic auth attribution + silent-undetection drift"

BASIC_AUTH_REASON = "HTTP Basic Authentication Credential gefunden"


# Realistic base64-encoded ``username:password`` pairs of varying lengths:
#
#  * ``_SHORT_BODY`` — 20 chars (below entropy fallback's 24-char minimum,
#    above the detector's 16-char floor). Decodes to ``admin:password``.
#  * ``_LONG_MIXED_BODY`` — 44 chars (above entropy minimum, mixed
#    character classes). Decodes to a realistic service-account string.
#  * ``_LONG_LETTERS_BODY`` — base64 of bytes that encode to all-letter
#    output. Trips the ``candidate.isalpha()`` skip in the entropy
#    fallback, so silently undetected pre-fix.

_SHORT_BODY = base64.b64encode(b"admin:password").decode("ascii")  # 20 chars
assert len(_SHORT_BODY) == 20
assert _SHORT_BODY == "YWRtaW46cGFzc3dvcmQ="

_LONG_MIXED_BODY = base64.b64encode(
    b"service-account-42:Sup3r$3cret!T0ken=Value"
).decode("ascii")  # 56 chars
assert len(_LONG_MIXED_BODY) >= 24

# Construct an all-letter base64 body by encoding bytes whose base64
# output contains only ``[A-Za-z]`` characters. This is necessary to
# trigger the ``candidate.isalpha()`` short-circuit in the entropy
# fallback. Most natural strings produce digits/punctuation in their
# base64 output, but careful byte choice (specifically picking bytes
# whose 6-bit groups map only to the letters in the base64 alphabet)
# yields an all-letter result.
_ALL_LETTERS_BODY = "abcdefghijklmnopqrstABCDEFGHIJKLMNOPQRST"  # 40 chars, all-letters
assert _ALL_LETTERS_BODY.isalpha()
assert len(_ALL_LETTERS_BODY) >= 24


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the Basic literal,
#     with a body that the entropy fallback DOES match, must yield the
#     Basic-Auth-specific reason (not the generic entropy reason).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "basic",   # all-lowercase (curl default)
        "BASIC",   # all-uppercase (httpie debug log)
        "BaSiC",   # mixed-case (hostile-PR-style obfuscation)
        "bAsIc",   # mixed-case alternate
        "Basic",   # canonical (regression guard)
    ],
)
def test_secret_scanner_detects_basic_auth_case_insensitive_long_mixed(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the Basic auth-scheme literal must be
    detected with the Basic-Auth-specific attribution, per RFC 7235 §2.1's
    case-insensitive auth-scheme contract (which RFC 7617 §2 references).

    The 44+ char mixed-class body exercises the attribution-drift branch:
    pre-fix the entropy fallback caught the body span generically (as
    "Hochentropischer Token-String"), but the Basic-Auth-specific reason
    that pinpoints rotation flow (rotate the user's password, not just
    revoke a token) was lost.
    """
    file_path = tmp_path / "header_log.txt"
    file_path.write_text(
        f"Authorization: {scheme} {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert BASIC_AUTH_REASON in reasons, (
        f"Basic detector did not produce its attribution for case "
        f"{scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 says auth-scheme is case-insensitive; the leaked "
        f"credential must yield the Basic-Auth-specific reason regardless "
        f"of case. ({SENTINEL_BASIC_AUTH_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _LONG_MIXED_BODY not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: short base64 body (16-23 chars, below the
#     entropy fallback's 24-char minimum) bypasses ``_HIGH_ENTROPY_RE``
#     entirely, so the Basic Auth detector is the ONLY branch that catches
#     these.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "basic",
        "BASIC",
        "BaSiC",
        "Basic",
    ],
)
def test_secret_scanner_detects_basic_auth_short_body(
    tmp_path: Path, scheme: str
) -> None:
    """Short base64 bodies (16-23 chars) fall BELOW the entropy
    fallback's 24-char minimum, so the Basic Auth detector is the ONLY
    detection branch that catches them. Pre-fix the credential is
    SILENTLY UNDETECTED entirely — the CI gate passes, and the
    plaintext ``username:password`` pair (recoverable via base64 decode)
    sits committed in the public repo.

    PoC body: base64(``admin:password``) = ``YWRtaW46cGFzc3dvcmQ=`` (20 chars).
    """
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "{scheme} {_SHORT_BODY}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert BASIC_AUTH_REASON in reasons, (
        f"SILENT UNDETECTION: scheme={scheme!r}, short body (decodes to "
        f"admin:password); got reasons {reasons!r}. "
        f"This is the high-severity branch — without the Basic Auth "
        f"detection the short-body credential slips past every detector "
        f"and the username:password pair sits committed in plaintext. "
        f"({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Silent-undetection PoCs: all-letter bodies trip the
#     ``candidate.isalpha()`` skip in the entropy fallback; the Basic Auth
#     detector closes this hole as well.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "basic",
        "BASIC",
        "BaSiC",
        "Basic",
    ],
)
def test_secret_scanner_detects_basic_auth_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter base64 bodies trip the ``candidate.isalpha()`` skip in
    ``_HIGH_ENTROPY_RE``'s loop (which exists to suppress false positives
    on LongCamelCaseClassNames). The Basic Auth detector catches these
    via the ``is_assignment=True`` path of ``_looks_like_secret`` which
    allows ``min_categories=1``.
    """
    file_path = tmp_path / "fixture.yaml"
    file_path.write_text(
        f"authorization: '{scheme} {_ALL_LETTERS_BODY}'\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert BASIC_AUTH_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Negative cases: ensure the new ``_BASIC_AUTH_RE`` does NOT match
#     natural-language text that mentions "Basic" / "BASIC" / "basic"
#     without a token-shaped body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No 16+ contiguous chars from [A-Za-z0-9+/=] after the keyword.
        "The basic facts of the case are unclear.",
        "BASIC interpretation of the rules is required.",
        "Basic principles of operation apply here.",
        # Whitespace inside the would-be token body breaks the regex.
        "basic short token here only",
        # Punctuation immediately after "Basic" breaks \s+ requirement.
        "basic,xyz",
        "BASIC!",
        # Common English passages
        "Read the basic documentation first.",
        "He has basic English skills.",
    ],
)
def test_secret_scanner_no_false_positives_on_natural_basic_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_BASIC_AUTH_RE`` must NOT match
    natural-language sentences that mention "basic" without a 16+-char
    token-shaped body following. The body alphabet
    ``[A-Za-z0-9+/=]{16,}`` is the structural disambiguator — English
    sentences embed spaces and punctuation, so no 16+ contiguous match
    is possible.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    basic_findings = [f for f in findings if f.reason == BASIC_AUTH_REASON]
    assert not basic_findings, (
        f"False-positive Basic Auth finding for natural-language text "
        f"{text!r}. The detector should require 16+ contiguous chars from "
        f"the base64 alphabet. ({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Cross-detector boundary: Basic Auth detector must NOT cannibalise
#     existing detectors. JWT (eyJ...) in a Basic header (unusual but
#     theoretically possible) yields the JWT reason; Bearer in a Bearer
#     header yields Bearer; etc.
# ---------------------------------------------------------------------------


def test_basic_auth_does_not_steal_jwt_attribution(tmp_path: Path) -> None:
    """A JWT (``eyJ...``) embedded after a Bearer auth-scheme literal
    must continue to yield the JWT-specific reason (which comes from
    ``_KNOWN_TOKENS`` matching FIRST in ``_scan_content``), not the
    Basic-Auth reason. The cross-detector ordering invariant pinned in
    ``_scan_content`` (``_KNOWN_TOKENS`` first, then ``_AWS_ID_RE``,
    then ``_BEARER_RE``, then ``_BASIC_AUTH_RE``) preserves the more
    specific attribution.
    """
    # Realistic JWT-shaped fixture (header.payload.signature with
    # base64url segments).
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    file_path = tmp_path / "jwt_header.txt"
    file_path.write_text(f"Authorization: Bearer {jwt}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "JSON Web Token (JWT) gefunden" in reasons, (
        f"Cross-detector boundary regression: JWT in Bearer header lost "
        f"its specific attribution. Got reasons {reasons!r}. "
        f"({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Regression guard: canonical-case Bearer continues to be detected
#     correctly. The new Basic Auth detector must not interfere with the
#     existing Bearer detection path.
# ---------------------------------------------------------------------------


def test_basic_auth_addition_does_not_break_bearer_detection(tmp_path: Path) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the Basic Auth addition. Regression
    guard against any unintended cross-effect."""
    bearer_body = "AbCdEfGhIjKlMnOpQrStUvWx0123"
    file_path = tmp_path / "canonical.py"
    file_path.write_text(
        f'HEADERS = {{"Authorization": "Bearer {bearer_body}"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Bearer-Token wirkt echt" in reasons


# ---------------------------------------------------------------------------
# (7) Compiled-regex invariant: ``_BASIC_AUTH_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_basic_auth_re_flags_include_ignorecase() -> None:
    """The compiled ``_BASIC_AUTH_RE`` must carry the ``re.IGNORECASE``
    flag so the auth-scheme literal is matched per the RFC 7235 §2.1
    case-insensitive contract (which RFC 7617 §2 references). A future
    regression that reverts to the case-sensitive shape fails this
    invariant immediately."""
    import re as _re

    from src.utils.secret_scanner import _BASIC_AUTH_RE

    assert _BASIC_AUTH_RE.flags & _re.IGNORECASE, (
        f"_BASIC_AUTH_RE flags={_BASIC_AUTH_RE.flags!r} missing "
        f"re.IGNORECASE. RFC 7235 §2.1 (via RFC 7617 §2) requires "
        f"case-insensitive matching on the Basic auth-scheme literal. "
        f"({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) End-to-end ``Authorization:`` header inventory: the case-insensitive
#     fix must cover the common header-emission shapes wholesale.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_header,case_label",
    [
        # HTTP-style log line shapes (curl -v, requests --debug, ngrep)
        (f"> Authorization: basic {_LONG_MIXED_BODY}", "lowercase-curl-debug"),
        (f"< Authorization: BASIC {_LONG_MIXED_BODY}", "uppercase-curl-debug"),
        (f"Authorization: BaSiC {_LONG_MIXED_BODY}", "mixed-case-fixture"),
        # Python-dict / JSON fixture shapes
        (f'{{"Authorization": "basic {_LONG_MIXED_BODY}"}}', "json-lowercase"),
        (f"{{'Authorization': 'BASIC {_LONG_MIXED_BODY}'}}", "py-dict-uppercase"),
        # YAML fixture shape (single-quoted)
        (f"authorization: 'basic {_LONG_MIXED_BODY}'", "yaml-lowercase"),
    ],
)
def test_secret_scanner_detects_basic_auth_across_emission_shapes(
    tmp_path: Path, raw_header: str, case_label: str
) -> None:
    """Real-world Basic Auth emission shapes (curl debug logs, JSON
    fixtures, YAML config, Python dicts) all canonicalise the case
    differently depending on the emitter; the detector must cover them
    wholesale."""
    file_path = tmp_path / f"fixture_{case_label}.txt"
    file_path.write_text(f"{raw_header}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert BASIC_AUTH_REASON in reasons, (
        f"Emission shape {case_label!r} ({raw_header!r}) did not produce "
        f"the Basic-Auth-specific reason; got {reasons!r}. "
        f"({SENTINEL_BASIC_AUTH_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking contract: the secret value never appears in the findings'
#     ``match`` field unmasked. This mirrors the Bearer detector's
#     masking-contract test.
# ---------------------------------------------------------------------------


def test_basic_auth_masking_contract(tmp_path: Path) -> None:
    """The raw base64-encoded credential must NOT appear unmasked in any
    finding's ``match`` field. The Basic Auth body decodes back to the
    plaintext ``username:password`` pair, so leaving it unmasked in
    findings (which surface in CI logs / PR comments / GitHub Issue
    bodies) would re-leak the credential at the very point of detection.
    """
    file_path = tmp_path / "fixture.txt"
    file_path.write_text(
        f"Authorization: Basic {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    basic_findings = [f for f in findings if f.reason == BASIC_AUTH_REASON]
    assert basic_findings, "Test setup failure: expected a Basic Auth finding."

    for finding in basic_findings:
        assert _LONG_MIXED_BODY not in finding.match, (
            f"Masking-contract violation: raw credential body "
            f"{_LONG_MIXED_BODY!r} appears unmasked in finding "
            f"{finding!r}. The Basic Auth body decodes to plaintext "
            f"username:password; emitting it unmasked re-leaks the "
            f"credential at the detection boundary. "
            f"({SENTINEL_BASIC_AUTH_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (10) Decoded-credential demonstration: the leaked Basic Auth body
#     decodes back to a recognisable ``username:password`` pair. This
#     test documents the threat model — Basic Auth is structurally
#     distinct from Bearer Auth in that the leaked credential is NOT
#     opaque but contains the plaintext username and password.
# ---------------------------------------------------------------------------


def test_basic_auth_body_decodes_to_username_password() -> None:
    """Documents the threat-model rationale for the Basic Auth detector:
    unlike Bearer tokens (opaque), Basic Auth bodies are reversible
    base64 encodings of ``username:password`` strings. A leaked body
    reveals the cleartext password — distinct revocation flow from
    Bearer (rotate the user's password AND audit downstream systems
    where the same password may be reused, vs. simply revoking an
    opaque token).
    """
    decoded = base64.b64decode(_SHORT_BODY).decode("ascii")
    assert decoded == "admin:password"
    assert ":" in decoded
    username, password = decoded.split(":", 1)
    assert username == "admin"
    assert password == "password"
