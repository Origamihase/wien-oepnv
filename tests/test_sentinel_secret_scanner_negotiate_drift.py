"""Sentinel PoC: missing ``_NEGOTIATE_RE`` detector — RFC 4559 SPNEGO /
Kerberos HTTP authentication credential leaks (``Authorization: Negotiate
<base64-encoded GSSAPI token>``) slip past the secret scanner with
attribution drift OR silent undetection, depending on the body shape.

This round closes the **named-but-deferred** adjacent-detector candidate
from the 2026-05-16 Basic Auth detector drift round (sibling PR
``security(secret-scanner): add Basic Auth detector per RFC 7617``). That
round explicitly named the next-round target::

    **Next-round candidates (named-but-deferred):** ...
    **SPNEGO detector** (`(?i)Negotiate\\s+[A-Za-z0-9+/=]{50,}`) —
    RFC 4559 Kerberos via SPNEGO; the body is a base64-encoded GSSAPI
    token, body lengths typically much longer than Basic Auth (200+ chars
    per Kerberos ticket).

RFC 4559 §4 defines the SPNEGO HTTP authentication scheme. The
``Negotiate`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits, so ``NEGOTIATE
<body>`` / ``negotiate <body>`` / ``NeGoTiAtE <body>`` are all
legitimate canonical HTTP Authorization-header shapes on the wire.

Threat model
------------

A leaked ``Authorization: Negotiate <body>`` (where the body is a
base64-encoded GSSAPI token wrapping either a Kerberos AP-REQ or an
NTLMSSP message) in committed source / log artefacts / CI debug
snippets / hostile-PR fragments fails the existing detection branches:

1. **Attribution drift (common case)** — long Kerberos tokens (200+
   chars typically; AS-REQ / AS-REP / AP-REQ messages encode the
   ASN.1-DER outer envelope plus the encrypted authenticator) DO match
   ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically and
   land as ``Hochentropischer Token-String`` findings. The
   SPNEGO-specific attribution is lost — incident-response triage must
   guess whether the leaked entropy span is a Kerberos AP-REQ (revoke
   service ticket at the KDC, force user re-authentication, audit the
   service principal for replay activity within the ticket's ~8-10h
   lifetime), an NTLM Type 3 response (rotate user password, audit
   downstream services for NetNTLMv2 relay), a Bearer token (revoke at
   the issuing IdP), a Basic Auth credential (rotate user password),
   or some other opaque secret. Each has a different revocation flow.

2. **Silent undetection (all-letter body — niche but possible)** —
   the entropy fallback's ``candidate.isalpha()`` short-circuit (added
   to suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. Such bodies arise
   from GSSAPI tokens whose base64 output happens to land in the
   letter-only sub-alphabet (rare but possible for short test tokens
   or hand-crafted PoC fixtures). Pre-fix every leaked ``Negotiate
   <all-letter-body>`` is **silently undetected** entirely.

The structurally critical distinction from Bearer/Basic Auth is that a
leaked Negotiate token carries the encrypted service ticket and the
authenticator's timestamp. An attacker who recovers the token within
the ticket validity window (typical 8-10h KDC default per RFC 4120
§5.3) can:

- **Replay the ticket** as the original user against the target service.
  The 5-minute authenticator clock-skew check (RFC 4120 §3.2) does not
  fully mitigate replay attacks on short timescales.
- **Pre-authentication offline cracking** (AS-REP roasting / AS-REQ
  pre-auth roasting) if the leaked token contains an AS-REP message
  with encrypted PA-DATA — the encrypted portion can be subjected to
  offline dictionary / brute-force attack with tools like ``hashcat``
  (mode 18200 / 19600).
- **NTLMSSP exposure** when the Negotiate envelope wraps an NTLMSSP
  message — NTLMv2 responses leaked in Type 3 messages are vulnerable
  to NetNTLMv2 relay attacks and offline cracking.
- **Domain reconnaissance** — the SPNEGO ``mech-types`` field
  identifies the available authentication mechanisms (Kerberos /
  NTLMSSP / NEGOEX) at the target, plus the realm name in clear
  base64-decoded form (the ASN.1 ``realm`` field is a public part of
  the AS-REQ message).

Real-world emission patterns
----------------------------

- IIS HTTP request logs with ``--debug`` flag turned on (the entire
  Authorization header is logged verbatim per ``UseAppPoolQueueLength``
  diagnostic mode).
- Browser dev-tools Network tab exports (``Save HAR with content``)
  capture the Authorization header verbatim into the HAR JSON.
- Wireshark / tshark capture exports rendered as text show the full
  base64 body.
- WinRM debug logs (``Set-PSDebug -Trace 2``) emit the Authorization
  header during the negotiation round trip.
- ``curl -v --negotiate -u :`` debug logs.
- Python ``requests`` with ``requests-kerberos`` debug mode
  (``logging.DEBUG`` on the ``requests_kerberos`` logger).
- Spring Security ``org.springframework.security.kerberos`` debug
  logging.
- ELK Stack ingest of ``WWW-Authenticate`` response headers.

Severity
--------

**MEDIUM-HIGH** — SPNEGO/Kerberos credentials grant access to the
target service principal for the ticket's validity window. Even when
the inner ticket is encrypted with the service's long-term key, replay
within the validity window (typical 8-10h) yields authenticated access
as the original user. Mitigated only by the requirement that the
leaked credential live alongside a ``Negotiate`` auth-scheme literal
in the same content blob; in practice this covers every leak through
IIS debug logs, browser HAR exports, Wireshark captures, WinRM
diagnostics, and curl/requests-kerberos debug output.

Fix
---

Add a Negotiate auth-scheme detector mirroring the Basic Auth
detector's case-insensitive contract::

    _NEGOTIATE_RE = re.compile(r"(?i)Negotiate\\s+([A-Za-z0-9+/=]{50,})")

Append to ``_AUTH_SCHEME_DETECTORS`` so the existing
``_scan_auth_scheme_credentials`` helper processes matches uniformly
with the same ``is_assignment=True`` ``_looks_like_secret`` filter that
the Bearer / Basic Auth detectors already use.

The 50+ char body floor is the structural disambiguator against
natural-language false positives — the English word "negotiate"
appears commonly in code comments and prose, but never followed by 50+
contiguous chars from the standard base64 alphabet ``[A-Za-z0-9+/=]``
(English words break at whitespace and punctuation). Real Kerberos
tokens are 200+ chars base64-encoded per RFC 4120's AS-REP / AP-REQ
ASN.1 DER envelope size.

Marker: SENTINEL_NEGOTIATE_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_NEGOTIATE_DRIFT = "rfc-4559 spnego/negotiate attribution + silent-undetection drift"

NEGOTIATE_REASON = "SPNEGO/Negotiate Authentication Token gefunden"


# Realistic GSSAPI tokens of varying lengths and shapes:
#
#  * ``_LONG_MIXED_BODY`` — 240-char mixed-class base64. Mimics a
#    Kerberos AP-REQ (typical 1500-3000 byte ASN.1 DER -> 2000-4000
#    base64 chars, but a truncated capture / partial log line yields
#    shorter shapes). Used for attribution-drift PoCs.
#  * ``_ALL_LETTERS_BODY`` — 60-char all-letter base64 body. Trips the
#    ``candidate.isalpha()`` skip in the entropy fallback so SILENTLY
#    UNDETECTED pre-fix. Used for the silent-undetection PoC.
#  * ``_KERBEROS_AP_REQ_PREFIX_BODY`` — realistic-shape body starting
#    with the canonical Kerberos AP-REQ base64 prefix ``YII`` (which
#    is base64 of the ASN.1 SEQUENCE tag 0x60 0x82). Used to document
#    the structural anchor.

_LONG_MIXED_BODY = (
    "YIIGTQYJKoZIhvcSAQICAQBuggY8MIIGOKADAgEFoQMCAQ6iBwMFACAAAACjggUtY"
    "IIFKTCCBSWgAwIBBaERGw9XSEVOLkVYQU1QTEUuT1JHoiQwIqADAgECoRswGRsESF"
    "RUUBsRd2ViLmV4YW1wbGUub3JnLm9yZ6OCBOAwggTcoAMCARKhAwIBA6KCBM4Egg"
    "TKgwYqEC9Yxa0LJg5L6Yzfu+r2jxKsCi0VgEHk0L0BNJ8GxqGsv5GxqGsv5GxqGsv"
)
assert len(_LONG_MIXED_BODY) >= 200
# Must mix character classes so it would also trigger entropy fallback
# (proves the SPNEGO detector wins over generic attribution).
assert any(c.isupper() for c in _LONG_MIXED_BODY)
assert any(c.islower() for c in _LONG_MIXED_BODY)
assert any(c.isdigit() for c in _LONG_MIXED_BODY)
assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in _LONG_MIXED_BODY)

# All-letter base64 body — trips the entropy fallback's
# ``candidate.isalpha()`` skip. Must be >= 50 chars to satisfy the
# Negotiate detector's body-length floor.
_ALL_LETTERS_BODY = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
)  # 62 chars
assert _ALL_LETTERS_BODY.isalpha()
assert len(_ALL_LETTERS_BODY) >= 50

# Canonical Kerberos AP-REQ base64 prefix (``YII`` = base64(0x60, 0x82)
# = the ASN.1 SEQUENCE tag with definite-length-form-2-octets header
# per RFC 4120 §5.3 AP-REQ MessageType).
_KERBEROS_AP_REQ_PREFIX_BODY = (
    "YIIGTQYJKoZIhvcSAQICAQBuggY8MIIGOKADAgEFoQMCAQ6iBwMFACAAAACjggUt"
)
assert _KERBEROS_AP_REQ_PREFIX_BODY.startswith("YII")
assert len(_KERBEROS_AP_REQ_PREFIX_BODY) >= 50


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the Negotiate
#     literal, with a body that the entropy fallback DOES match, must
#     yield the SPNEGO-specific reason (not the generic entropy reason).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "negotiate",   # all-lowercase (curl default)
        "NEGOTIATE",   # all-uppercase (httpie / IIS debug log)
        "Negotiate",   # canonical (RFC 4559 §4 example)
        "NeGoTiAtE",   # mixed-case (hostile-PR-style obfuscation)
        "nEgOtIaTe",   # mixed-case alternate
    ],
)
def test_secret_scanner_detects_negotiate_case_insensitive_long_mixed(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the Negotiate auth-scheme literal must be
    detected with the SPNEGO-specific attribution, per RFC 7235 §2.1's
    case-insensitive auth-scheme contract (which RFC 4559 §4 inherits).

    The 240-char mixed-class body exercises the attribution-drift
    branch: pre-fix the entropy fallback caught the body span
    generically (as "Hochentropischer Token-String"), but the
    SPNEGO-specific reason that pinpoints revocation flow (KDC ticket
    revocation, force re-auth, audit service principal for replay
    within ticket lifetime) was lost.
    """
    file_path = tmp_path / "iis_request_log.txt"
    file_path.write_text(
        f"Authorization: {scheme} {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NEGOTIATE_REASON in reasons, (
        f"Negotiate detector did not produce its attribution for case "
        f"{scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 (via RFC 4559 §4) says auth-scheme is case-"
        f"insensitive; the leaked credential must yield the SPNEGO-"
        f"specific reason regardless of case. ({SENTINEL_NEGOTIATE_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _LONG_MIXED_BODY not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: all-letter bodies trip the
#     ``candidate.isalpha()`` skip in the entropy fallback. The Negotiate
#     detector closes this hole.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "negotiate",
        "NEGOTIATE",
        "Negotiate",
        "NeGoTiAtE",
    ],
)
def test_secret_scanner_detects_negotiate_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter base64 bodies trip the ``candidate.isalpha()`` skip in
    ``_HIGH_ENTROPY_RE``'s loop (which exists to suppress false
    positives on LongCamelCaseClassNames). The Negotiate detector
    catches these via the ``is_assignment=True`` path of
    ``_looks_like_secret`` which allows ``min_categories=1``.

    PoC body: 62-char all-letter base64 token. The pre-fix scanner is
    SILENTLY UNDETECTED entirely — no Negotiate-specific reason and no
    generic entropy reason fires.
    """
    file_path = tmp_path / "fixture.yaml"
    file_path.write_text(
        f"authorization: '{scheme} {_ALL_LETTERS_BODY}'\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NEGOTIATE_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the new ``_NEGOTIATE_RE`` does NOT match
#     natural-language text that mentions "negotiate" / "NEGOTIATE" /
#     "Negotiate" without a 50+-char token-shaped body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No 50+ contiguous chars from [A-Za-z0-9+/=] after the keyword.
        "We must negotiate the contract terms first.",
        "NEGOTIATE before the deadline arrives.",
        "Negotiate the price down to a reasonable value.",
        # Whitespace inside the would-be token body breaks the regex.
        "negotiate short tokens here only fine",
        # Punctuation immediately after "Negotiate" breaks \s+ requirement.
        "negotiate,xyz",
        "NEGOTIATE!",
        # Common English passages
        "The negotiate function takes three arguments and returns a result.",
        "He has to negotiate with the vendor before signing the contract today.",
        # Code-shape false-positive candidates (method/identifier names) —
        # no whitespace between Negotiate and the following body so no
        # match per the \s+ separator requirement.
        "function negotiateContentType(req, res) {",
        "class NegotiateAuthenticator extends AbstractAuth {",
        "def auto_negotiate_protocol_version(self, *, timeout=None):",
        # 49-char body — JUST below the 50-char floor.
        "negotiate " + "A" * 49,
    ],
)
def test_secret_scanner_no_false_positives_on_natural_negotiate_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_NEGOTIATE_RE`` must NOT match
    natural-language sentences that mention "negotiate" without a
    50+-char token-shaped body following. The body alphabet
    ``[A-Za-z0-9+/=]{50,}`` is the structural disambiguator — English
    sentences embed spaces and punctuation, so no 50+ contiguous match
    is possible.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    negotiate_findings = [f for f in findings if f.reason == NEGOTIATE_REASON]
    assert not negotiate_findings, (
        f"False-positive Negotiate finding for natural-language text "
        f"{text!r}. The detector should require 50+ contiguous chars "
        f"from the base64 alphabet. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Cross-detector boundary: Negotiate Auth detector must NOT
#     cannibalise existing detectors. JWT (eyJ...) in a Bearer header
#     must continue to yield the JWT reason from _KNOWN_TOKENS.
# ---------------------------------------------------------------------------


def test_negotiate_does_not_steal_jwt_attribution(tmp_path: Path) -> None:
    """A JWT (``eyJ...``) embedded after a Bearer auth-scheme literal
    must continue to yield the JWT-specific reason (which comes from
    ``_KNOWN_TOKENS`` matching FIRST in ``_scan_content``), not the
    SPNEGO-specific reason. The cross-detector ordering invariant
    pinned in ``_scan_content`` (``_KNOWN_TOKENS`` first, then
    ``_AWS_ID_RE``, then ``_AUTH_SCHEME_DETECTORS``) preserves the
    more specific attribution.
    """
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
        f"its specific attribution after adding Negotiate detector. "
        f"Got reasons {reasons!r}. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Regression guards: canonical-case Bearer and Basic detectors
#     continue to fire correctly after adding Negotiate. The new
#     detector must not interfere with existing detection paths.
# ---------------------------------------------------------------------------


def test_negotiate_addition_does_not_break_bearer_detection(tmp_path: Path) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the Negotiate addition. Regression
    guard against any unintended cross-effect from the new detector in
    the ``_AUTH_SCHEME_DETECTORS`` table."""
    bearer_body = "AbCdEfGhIjKlMnOpQrStUvWx0123"
    file_path = tmp_path / "canonical.py"
    file_path.write_text(
        f'HEADERS = {{"Authorization": "Bearer {bearer_body}"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Bearer-Token wirkt echt" in reasons, (
        f"Regression: Bearer detection broke after Negotiate addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


def test_negotiate_addition_does_not_break_basic_auth_detection(tmp_path: Path) -> None:
    """The canonical ``Basic <body>`` form continues to fire the
    Basic-Auth detector after the Negotiate addition. Sibling
    regression guard within the ``_AUTH_SCHEME_DETECTORS`` table."""
    basic_body = "YWRtaW46cGFzc3dvcmQ="  # base64("admin:password")
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "Basic {basic_body}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HTTP Basic Authentication Credential gefunden" in reasons, (
        f"Regression: Basic Auth detection broke after Negotiate "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Compiled-regex invariant: ``_NEGOTIATE_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_negotiate_re_flags_include_ignorecase() -> None:
    """The compiled ``_NEGOTIATE_RE`` must carry the ``re.IGNORECASE``
    flag so the auth-scheme literal is matched per the RFC 7235 §2.1
    case-insensitive contract (which RFC 4559 §4 inherits). A future
    regression that reverts to the case-sensitive shape fails this
    invariant immediately."""
    import re as _re

    from src.utils.secret_scanner import _NEGOTIATE_RE

    assert _NEGOTIATE_RE.flags & _re.IGNORECASE, (
        f"_NEGOTIATE_RE flags={_NEGOTIATE_RE.flags!r} missing "
        f"re.IGNORECASE. RFC 7235 §2.1 (via RFC 4559 §4) requires "
        f"case-insensitive matching on the Negotiate auth-scheme "
        f"literal. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Auth-scheme detector table membership invariant: the new
#     ``_NEGOTIATE_RE`` must be wired into ``_AUTH_SCHEME_DETECTORS``
#     so the canonical ``_scan_auth_scheme_credentials`` helper
#     processes it uniformly. A future regression that adds the regex
#     but forgets the table entry fails this invariant immediately.
# ---------------------------------------------------------------------------


def test_negotiate_re_is_in_auth_scheme_detectors_table() -> None:
    """The structural invariant from the 2026-05-16 Basic Auth drift
    round: every HTTP auth-scheme detector MUST be a member of
    ``_AUTH_SCHEME_DETECTORS`` so the canonical processing path in
    ``_scan_auth_scheme_credentials`` applies uniformly (same
    ``_looks_like_secret`` filter, same coverage-range arbitration).
    A regression that adds ``_NEGOTIATE_RE`` but forgets the table
    binding ships the dead regex and the detection gap stays open."""
    from src.utils.secret_scanner import _AUTH_SCHEME_DETECTORS, _NEGOTIATE_RE

    detector_regexes = [regex for regex, _ in _AUTH_SCHEME_DETECTORS]
    assert _NEGOTIATE_RE in detector_regexes, (
        f"_NEGOTIATE_RE is not bound in _AUTH_SCHEME_DETECTORS. "
        f"Per the structural invariant, every auth-scheme regex MUST "
        f"be a tuple entry. ({SENTINEL_NEGOTIATE_DRIFT})"
    )

    detector_reasons = [reason for _, reason in _AUTH_SCHEME_DETECTORS]
    assert NEGOTIATE_REASON in detector_reasons, (
        f"Expected reason {NEGOTIATE_REASON!r} not found in "
        f"_AUTH_SCHEME_DETECTORS. Reasons present: "
        f"{detector_reasons!r}. ({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) End-to-end emission-shape inventory: real-world IIS / WinRM /
#     curl --negotiate / HAR-export / Wireshark-export shapes — the
#     case-insensitive fix must cover the common header-emission
#     shapes wholesale.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_header,case_label",
    [
        # HTTP-style log line shapes (curl -v --negotiate, requests --debug)
        (f"> Authorization: negotiate {_LONG_MIXED_BODY}", "lowercase-curl-debug"),
        (f"< Authorization: NEGOTIATE {_LONG_MIXED_BODY}", "uppercase-iis-debug"),
        (f"Authorization: NeGoTiAtE {_LONG_MIXED_BODY}", "mixed-case-fixture"),
        # Browser HAR export shape (JSON nested header structure)
        (
            f'{{"name": "Authorization", "value": "negotiate {_LONG_MIXED_BODY}"}}',
            "har-export-lowercase",
        ),
        # Python-dict / JSON fixture shape (kerberos test harness)
        (
            f'{{"Authorization": "Negotiate {_LONG_MIXED_BODY}"}}',
            "json-canonical-case",
        ),
        # YAML fixture shape (Ansible / Kubernetes secret manifest)
        (
            f"authorization: 'NEGOTIATE {_LONG_MIXED_BODY}'",
            "yaml-uppercase",
        ),
        # WinRM debug-trace shape (PowerShell Set-PSDebug)
        (
            f"DEBUG: Authorization: Negotiate {_LONG_MIXED_BODY}",
            "winrm-debug-trace",
        ),
        # Wireshark text-export shape
        (
            f"    Authorization: Negotiate {_LONG_MIXED_BODY}",
            "wireshark-indented",
        ),
    ],
)
def test_secret_scanner_detects_negotiate_across_emission_shapes(
    tmp_path: Path, raw_header: str, case_label: str
) -> None:
    """Real-world Negotiate emission shapes (IIS debug logs, browser
    HAR exports, JSON/YAML fixtures, WinRM debug traces, Wireshark
    captures) all canonicalise the case differently depending on the
    emitter; the detector must cover them wholesale."""
    file_path = tmp_path / f"fixture_{case_label}.txt"
    file_path.write_text(f"{raw_header}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NEGOTIATE_REASON in reasons, (
        f"Emission shape {case_label!r} ({raw_header!r}) did not produce "
        f"the SPNEGO-specific reason; got {reasons!r}. "
        f"({SENTINEL_NEGOTIATE_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking contract: the secret value never appears in the
#     findings' ``match`` field unmasked. This mirrors the Bearer and
#     Basic Auth detectors' masking-contract tests.
# ---------------------------------------------------------------------------


def test_negotiate_masking_contract(tmp_path: Path) -> None:
    """The raw base64-encoded GSSAPI token must NOT appear unmasked in
    any finding's ``match`` field. The Negotiate body decodes back to
    the binary GSSAPI token (Kerberos AP-REQ or NTLMSSP message), so
    leaving it unmasked in findings (which surface in CI logs / PR
    comments / GitHub Issue bodies) would re-leak the credential at
    the very point of detection.
    """
    file_path = tmp_path / "fixture.txt"
    file_path.write_text(
        f"Authorization: Negotiate {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    negotiate_findings = [f for f in findings if f.reason == NEGOTIATE_REASON]
    assert negotiate_findings, "Test setup failure: expected a Negotiate finding."

    for finding in negotiate_findings:
        assert _LONG_MIXED_BODY not in finding.match, (
            f"Masking-contract violation: raw GSSAPI token body "
            f"{_LONG_MIXED_BODY!r} appears unmasked in finding "
            f"{finding!r}. The Negotiate body decodes to a Kerberos "
            f"AP-REQ / NTLMSSP message; emitting it unmasked re-leaks "
            f"the credential at the detection boundary. "
            f"({SENTINEL_NEGOTIATE_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (10) Kerberos AP-REQ structural-prefix demonstration: realistic
#      Kerberos GSSAPI tokens start with ``YII`` (base64 of the
#      ASN.1 SEQUENCE outer tag 0x60 0x82). This test documents the
#      structural anchor and ensures the detector fires on
#      realistically-shaped tokens.
# ---------------------------------------------------------------------------


def test_negotiate_detects_kerberos_ap_req_shaped_body(tmp_path: Path) -> None:
    """A realistic Kerberos AP-REQ GSSAPI token base64-encodes to a
    body starting with ``YII`` (base64 of the ASN.1 DER SEQUENCE outer
    tag 0x60 plus the definite-length-form-2-octets header 0x82). This
    test pins detection on the canonical Kerberos shape.

    Documentation only — the detector matches purely on
    ``Negotiate\\s+`` plus the body alphabet, NOT on the ``YII``
    prefix (which would constrain to Kerberos and miss NTLMSSP-wrapped
    Negotiate tokens). The test asserts coverage of the realistic
    Kerberos shape without requiring the prefix anchor at the regex
    level.
    """
    file_path = tmp_path / "kerberos_capture.txt"
    file_path.write_text(
        f"Authorization: Negotiate {_KERBEROS_AP_REQ_PREFIX_BODY}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NEGOTIATE_REASON in reasons, (
        f"Kerberos AP-REQ shaped body (YII-prefixed) not detected. "
        f"Got reasons {reasons!r}. ({SENTINEL_NEGOTIATE_DRIFT})"
    )

    # The YII prefix is a useful incident-response anchor (identifies
    # Kerberos vs NTLMSSP; NTLMSSP base64 starts with ``TlRMTVNTUA``
    # = base64("NTLMSSP\0")). Document for future Sentinel rounds.
    assert _KERBEROS_AP_REQ_PREFIX_BODY.startswith("YII")
