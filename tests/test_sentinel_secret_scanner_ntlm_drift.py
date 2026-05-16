"""Sentinel PoC: missing ``_NTLM_RE`` detector — Microsoft NTLM HTTP
authentication credential leaks (``Authorization: NTLM <base64-encoded
NTLMSSP message>``) slip past the secret scanner with attribution drift
OR silent undetection, depending on the body shape.

This round closes the **named-but-deferred** adjacent-detector candidate
from the 2026-05-16 SPNEGO/Negotiate detector drift round (sibling PR
``security(secret-scanner): add Negotiate detector per RFC 4559 SPNEGO``).
That round explicitly named NTLM as the first next-round target::

    **Next-round candidates (named-but-deferred):** **NTLM detector**
    (``(?i)NTLM\\s+[A-Za-z0-9+/=]{50,}``) — Microsoft NTLM directly as
    an HTTP auth-scheme (without SPNEGO wrapping); used by IIS for
    legacy clients and SMB-over-HTTP scenarios. The literal ``NTLM``
    is vendor-specific per [MS-NLMP] (not IETF-RFC-defined like
    Negotiate), creating a structural classification question: belongs
    to ``_AUTH_SCHEME_DETECTORS`` (because it IS an HTTP auth-scheme
    literal) OR ``_KNOWN_TOKENS`` (because [MS-NLMP] is a vendor spec,
    not an IETF RFC)? Per the structural invariant established by the
    Basic Auth round ("HTTP-auth-scheme literals go to
    ``_AUTH_SCHEME_DETECTORS``"), NTLM should go in the auth-scheme
    table — the canonical processing path already handles it without
    modification.

The ``NTLM`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits (RFC 4559 §4 lists
``NTLM`` alongside ``Negotiate`` as recognised HTTP auth-scheme
literals; even though [MS-NLMP] is a vendor spec not an IETF RFC, the
HTTP layer treats the literal per RFC 7235 §2.1). ``ntlm <body>`` /
``NTLM <body>`` / ``NtLm <body>`` are all legitimate canonical HTTP
Authorization-header shapes on the wire.

Threat model
------------

A leaked ``Authorization: NTLM <body>`` (where the body is a
base64-encoded NTLMSSP message — Type 1 negotiate, Type 2 challenge, or
Type 3 authenticate per [MS-NLMP]) in committed source / log artefacts /
CI debug snippets / hostile-PR fragments fails the existing detection
branches:

1. **Attribution drift (common case)** — long NTLMSSP Type 3
   (authenticate) messages (350-1000+ bytes raw, base64-encoded to
   470-1500+ chars) DO match ``_HIGH_ENTROPY_RE``
   (``[A-Za-z0-9+/=_-]{24,}``) generically and land as
   ``Hochentropischer Token-String`` findings. The NTLM-specific
   attribution is lost — incident-response triage must guess whether
   the leaked entropy span is an NTLMv2 response (rotate user password,
   audit domain controller for NetNTLMv2 relay attempts, force user
   re-authentication via password change), a Kerberos AP-REQ (revoke
   service ticket at the KDC), a Bearer token (revoke at the issuing
   IdP), a Basic Auth credential (rotate user password — different
   recovery surface than NTLM), or some other opaque secret. Each has
   a different revocation flow.

2. **Silent undetection (all-letter body — niche but possible)** —
   the entropy fallback's ``candidate.isalpha()`` short-circuit (added
   to suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. Such bodies arise
   from NTLMSSP messages whose base64 output happens to land in the
   letter-only sub-alphabet (rare in practice but possible for short
   test fixtures or hand-crafted PoC payloads). Pre-fix every leaked
   ``NTLM <all-letter-body>`` is **silently undetected** entirely.

The structurally critical distinction from Bearer/Basic Auth is that a
leaked NTLM Type 3 message carries the **NTLMv2 challenge-response**
— a NetNTLMv2 hash that:

- **Relay attack** — replayable via ``ntlmrelayx`` against any
  service in the same Windows domain that accepts NTLM authentication
  (SMB, LDAP, HTTP, MSSQL). The attacker gains the leaked user's
  effective access to those services without ever cracking the
  password. Mitigated by SMB signing / LDAP channel binding / EPA on
  the target, but those mitigations are not universal.
- **Offline cracking** — the NetNTLMv2 hash is subject to offline
  dictionary / brute-force / rule-based attack with ``hashcat`` (mode
  5600) or ``john`` (``netntlmv2`` format). Weak passwords are
  recovered within minutes on modern GPUs (NTLMv2 throughput on a
  single RTX 4090 is ~50 GH/s for masking attacks). The plaintext
  password is the canonical password-reuse-amplifier surface.
- **Domain reconnaissance** — the NTLMSSP Type 3 message carries
  the unencrypted ``UserName`` and ``Workstation`` fields plus the
  ``DomainName`` from the Type 1 negotiate, revealing the target
  Windows domain structure and a valid user-domain-workstation tuple
  for further attacks.

The blast radius of an undetected NTLM leak therefore EXCEEDS a Basic
Auth leak in the worst case — NTLM Type 3 enables relay attacks even
WITHOUT cracking the password, while a Basic Auth leak requires the
attacker to actually use the recovered ``user:password`` pair.

Real-world emission patterns
----------------------------

- IIS HTTP request logs with ``--debug`` flag turned on (the entire
  Authorization header is logged verbatim).
- Browser dev-tools Network tab exports (``Save HAR with content``)
  capture the Authorization header verbatim into the HAR JSON for
  intranet NTLM-authenticated sites.
- Wireshark / tshark capture exports rendered as text show the full
  base64 body — common for SMB-over-HTTP / WebDAV / SharePoint
  captures.
- WinRM debug logs (``Set-PSDebug -Trace 2``) emit the Authorization
  header during NTLM negotiate / challenge / authenticate round trips.
- ``curl -v --ntlm -u user:pass`` debug logs.
- Python ``requests`` with ``requests-ntlm`` debug mode
  (``logging.DEBUG`` on the ``requests_ntlm`` logger).
- Spring Security ``org.springframework.security.kerberos`` debug
  logging (intercepts NTLM as a fallback when SPNEGO is unavailable).
- ELK Stack ingest of ``WWW-Authenticate: NTLM`` response headers.
- Active Directory diagnostic event logs (Event ID 4624 with
  authentication type 3 / 5 / 7).

Severity
--------

**MEDIUM-HIGH** — NTLM Type 3 credentials grant authenticated access
to the target service principal AND enable relay attacks against other
domain services even WITHOUT password cracking. Even when SMB signing /
EPA / LDAP channel binding mitigate relay against the most sensitive
services, the password-reuse amplifier remains (a cracked NTLMv2 hash
yields the plaintext password). Mitigated only by the requirement that
the leaked credential live alongside an ``NTLM`` auth-scheme literal in
the same content blob; in practice this covers every leak through IIS
debug logs, browser HAR exports, Wireshark captures, WinRM
diagnostics, and curl/requests-ntlm debug output.

Fix
---

Add an NTLM auth-scheme detector mirroring the Negotiate detector's
case-insensitive contract::

    _NTLM_RE = re.compile(r"(?i)NTLM\\s+([A-Za-z0-9+/=]{50,})")

Append to ``_AUTH_SCHEME_DETECTORS`` so the existing
``_scan_auth_scheme_credentials`` helper processes matches uniformly
with the same ``is_assignment=True`` ``_looks_like_secret`` filter that
the Bearer / Basic Auth / Negotiate detectors already use.

The 50+ char body floor is the structural disambiguator against
natural-language false positives — the literal ``NTLM`` is an acronym
that does not appear in natural English prose followed by 50+ chars
from the base64 alphabet. Real NTLMSSP messages are ALL above 50 chars
(Type 1 = 40 bytes raw = 56 base64 chars minimum; Type 3 = 350-1000+
bytes raw = 470-1500+ base64 chars). The structural anchor is the
``TlRMTVNTUA`` base64 prefix (base64 of the NTLMSSP\\0 magic per
[MS-NLMP] §2.2 NTLM Messages Header).

Marker: SENTINEL_NTLM_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_NTLM_DRIFT = "ms-nlmp ntlm attribution + silent-undetection drift"

NTLM_REASON = "NTLM Authentication Credential gefunden"


# Realistic NTLMSSP base64 tokens of varying lengths and shapes:
#
#  * ``_LONG_MIXED_BODY`` — 240-char mixed-class base64 body, NTLMSSP-
#    magic prefixed. Mimics a real NTLMSSP Type 3 (Authenticate)
#    message carrying the NetNTLMv2 challenge-response and the
#    UserName / Workstation / DomainName fields. Used for the
#    attribution-drift PoCs.
#  * ``_ALL_LETTERS_BODY`` — 62-char all-letter base64 body. Trips
#    the ``candidate.isalpha()`` skip in the entropy fallback so
#    SILENTLY UNDETECTED pre-fix. Used for the silent-undetection PoC.
#  * ``_NTLMSSP_TYPE1_BODY`` — realistic NTLMSSP Type 1 (Negotiate)
#    base64 body starting with the canonical ``TlRMTVNTUA`` prefix
#    (base64 of the ASCII NTLMSSP magic ``0x4e 0x54 0x4c 0x4d 0x53
#    0x53 0x50 0x00`` per [MS-NLMP] §2.2). Used to document the
#    structural anchor for NTLMSSP-wrapped tokens.

_LONG_MIXED_BODY = (
    "TlRMTVNTUAADAAAAGAAYAHIAAAAYABgAigAAABQAFABIAAAADAAMAFwAAAASABIA"
    "aAAAABAAEACiAAAAVQBzAGUAcgBOAGEAbQBlAEQAbwBtAGEAaQBuAFcAbwByAGsA"
    "cwB0AGEAdABpAG8AbgBOAEEEEDQGN6JhKlJgwpV1nMxn/wEBAAAAAAAAtMW5sAm5"
    "0gEgZkBhcDkyNgAAAAACAAgAcgBlAGEAbABtAAAAAAAAAAAAAAA="
)
assert len(_LONG_MIXED_BODY) >= 200
# Must mix character classes so it would also trigger entropy fallback
# (proves the NTLM detector wins over generic attribution).
assert any(c.isupper() for c in _LONG_MIXED_BODY)
assert any(c.islower() for c in _LONG_MIXED_BODY)
assert any(c.isdigit() for c in _LONG_MIXED_BODY)
assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in _LONG_MIXED_BODY)
# Canonical NTLMSSP prefix per [MS-NLMP] §2.2 Header (NTLMSSP\0 magic).
assert _LONG_MIXED_BODY.startswith("TlRMTVNTUA")

# All-letter base64 body — trips the entropy fallback's
# ``candidate.isalpha()`` skip. Must be >= 50 chars to satisfy the
# NTLM detector's body-length floor.
_ALL_LETTERS_BODY = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
)  # 62 chars
assert _ALL_LETTERS_BODY.isalpha()
assert len(_ALL_LETTERS_BODY) >= 50

# Canonical NTLMSSP Type 1 (Negotiate) base64 body — starts with the
# ``TlRMTVNTUA`` prefix (base64 of the ASCII NTLMSSP magic per
# [MS-NLMP] §2.2 Header). The Type 1 message is 40 bytes raw = 56
# base64 chars, the minimum realistic NTLMSSP message size.
_NTLMSSP_TYPE1_BODY = (
    "TlRMTVNTUAABAAAAl4II4gAAAAAAAAAAAAAAAAAAAAAGAbEdAAAADw=="
)
assert _NTLMSSP_TYPE1_BODY.startswith("TlRMTVNTUA")
assert len(_NTLMSSP_TYPE1_BODY) >= 50


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the NTLM literal,
#     with a body that the entropy fallback DOES match, must yield the
#     NTLM-specific reason (not the generic entropy reason).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "ntlm",   # all-lowercase (curl default for --ntlm)
        "NTLM",   # canonical (RFC 4559 §4 / [MS-NLMP] example)
        "Ntlm",   # title-case
        "NtLm",   # mixed-case (hostile-PR-style obfuscation)
        "nTlM",   # mixed-case alternate
    ],
)
def test_secret_scanner_detects_ntlm_case_insensitive_long_mixed(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the NTLM auth-scheme literal must be
    detected with the NTLM-specific attribution, per RFC 7235 §2.1's
    case-insensitive auth-scheme contract.

    The 240-char mixed-class body exercises the attribution-drift
    branch: pre-fix the entropy fallback caught the body span
    generically (as "Hochentropischer Token-String"), but the
    NTLM-specific reason that pinpoints revocation flow (rotate user
    password, audit domain controller for NetNTLMv2 relay attempts,
    force user re-auth) was lost.
    """
    file_path = tmp_path / "iis_request_log.txt"
    file_path.write_text(
        f"Authorization: {scheme} {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NTLM_REASON in reasons, (
        f"NTLM detector did not produce its attribution for case "
        f"{scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 says auth-scheme is case-insensitive; the "
        f"leaked credential must yield the NTLM-specific reason "
        f"regardless of case. ({SENTINEL_NTLM_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _LONG_MIXED_BODY not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: all-letter bodies trip the
#     ``candidate.isalpha()`` skip in the entropy fallback. The NTLM
#     detector closes this hole.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "ntlm",
        "NTLM",
        "Ntlm",
        "NtLm",
    ],
)
def test_secret_scanner_detects_ntlm_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter base64 bodies trip the ``candidate.isalpha()`` skip in
    ``_HIGH_ENTROPY_RE``'s loop (which exists to suppress false
    positives on LongCamelCaseClassNames). The NTLM detector catches
    these via the ``is_assignment=True`` path of ``_looks_like_secret``
    which allows ``min_categories=1``.

    PoC body: 62-char all-letter base64 token. The pre-fix scanner is
    SILENTLY UNDETECTED entirely — no NTLM-specific reason and no
    generic entropy reason fires.
    """
    file_path = tmp_path / "fixture.yaml"
    file_path.write_text(
        f"authorization: '{scheme} {_ALL_LETTERS_BODY}'\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NTLM_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the new ``_NTLM_RE`` does NOT match
#     natural-language text or code identifiers that mention "ntlm" /
#     "NTLM" / "Ntlm" without a 50+-char token-shaped body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No 50+ contiguous chars from [A-Za-z0-9+/=] after the keyword.
        "We must configure NTLM authentication for the legacy backend.",
        "NTLM is deprecated in favour of Kerberos.",
        "Disable NTLM on the domain controller immediately.",
        # Whitespace inside the would-be token body breaks the regex.
        "ntlm short tokens here only fine and not detected",
        # Punctuation immediately after "NTLM" breaks \s+ requirement.
        "ntlm,xyz",
        "NTLM!",
        "NTLM.",
        # Common English passages mentioning NTLM as an acronym.
        "The NTLM protocol uses a three-message challenge-response handshake.",
        "Audit the NTLM Type 3 message for the NetNTLMv2 hash recovery surface.",
        # Code-shape false-positive candidates (method/identifier names) —
        # no whitespace between NTLM and the following body so no
        # match per the \s+ separator requirement.
        "function ntlmHandshakeNegotiator(req, res) {",
        "class NtlmAuthenticatorImplementation extends AbstractAuth {",
        "def auto_ntlm_protocol_version(self, *, timeout=None):",
        # 49-char body — JUST below the 50-char floor.
        "ntlm " + "A" * 49,
    ],
)
def test_secret_scanner_no_false_positives_on_natural_ntlm_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_NTLM_RE`` must NOT match natural-language
    sentences that mention "NTLM" without a 50+-char token-shaped body
    following. The body alphabet ``[A-Za-z0-9+/=]{50,}`` is the
    structural disambiguator — English sentences embed spaces and
    punctuation, so no 50+ contiguous match is possible.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    ntlm_findings = [f for f in findings if f.reason == NTLM_REASON]
    assert not ntlm_findings, (
        f"False-positive NTLM finding for natural-language text "
        f"{text!r}. The detector should require 50+ contiguous chars "
        f"from the base64 alphabet. ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Cross-detector boundary: NTLM detector must NOT cannibalise
#     existing detectors. JWT (eyJ...) in a Bearer header must continue
#     to yield the JWT reason from _KNOWN_TOKENS.
# ---------------------------------------------------------------------------


def test_ntlm_does_not_steal_jwt_attribution(tmp_path: Path) -> None:
    """A JWT (``eyJ...``) embedded after a Bearer auth-scheme literal
    must continue to yield the JWT-specific reason (which comes from
    ``_KNOWN_TOKENS`` matching FIRST in ``_scan_content``), not the
    NTLM-specific reason. The cross-detector ordering invariant pinned
    in ``_scan_content`` (``_KNOWN_TOKENS`` first, then ``_AWS_ID_RE``,
    then ``_AUTH_SCHEME_DETECTORS``) preserves the more specific
    attribution.
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
        f"its specific attribution after adding NTLM detector. "
        f"Got reasons {reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Regression guards: canonical-case Bearer, Basic, and Negotiate
#     detectors continue to fire correctly after adding NTLM. The new
#     detector must not interfere with existing detection paths.
# ---------------------------------------------------------------------------


def test_ntlm_addition_does_not_break_bearer_detection(tmp_path: Path) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the NTLM addition. Regression guard
    against any unintended cross-effect from the new detector in the
    ``_AUTH_SCHEME_DETECTORS`` table."""
    bearer_body = "AbCdEfGhIjKlMnOpQrStUvWx0123"
    file_path = tmp_path / "canonical.py"
    file_path.write_text(
        f'HEADERS = {{"Authorization": "Bearer {bearer_body}"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Bearer-Token wirkt echt" in reasons, (
        f"Regression: Bearer detection broke after NTLM addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


def test_ntlm_addition_does_not_break_basic_auth_detection(tmp_path: Path) -> None:
    """The canonical ``Basic <body>`` form continues to fire the
    Basic-Auth detector after the NTLM addition. Sibling regression
    guard within the ``_AUTH_SCHEME_DETECTORS`` table."""
    basic_body = "YWRtaW46cGFzc3dvcmQ="  # base64("admin:password")
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "Basic {basic_body}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HTTP Basic Authentication Credential gefunden" in reasons, (
        f"Regression: Basic Auth detection broke after NTLM addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


def test_ntlm_addition_does_not_break_negotiate_detection(tmp_path: Path) -> None:
    """The canonical ``Negotiate <body>`` form continues to fire the
    SPNEGO/Negotiate detector after the NTLM addition. Sibling
    regression guard within the ``_AUTH_SCHEME_DETECTORS`` table."""
    negotiate_body = (
        "YIIGTQYJKoZIhvcSAQICAQBuggY8MIIGOKADAgEFoQMCAQ6iBwMFACAAAACj"
    )
    file_path = tmp_path / "kerberos_capture.txt"
    file_path.write_text(
        f"Authorization: Negotiate {negotiate_body}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "SPNEGO/Negotiate Authentication Token gefunden" in reasons, (
        f"Regression: Negotiate detection broke after NTLM addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Compiled-regex invariant: ``_NTLM_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_ntlm_re_flags_include_ignorecase() -> None:
    """The compiled ``_NTLM_RE`` must carry the ``re.IGNORECASE`` flag
    so the auth-scheme literal is matched per the RFC 7235 §2.1
    case-insensitive contract that every HTTP auth-scheme inherits. A
    future regression that reverts to the case-sensitive shape fails
    this invariant immediately."""
    import re as _re

    from src.utils.secret_scanner import _NTLM_RE

    assert _NTLM_RE.flags & _re.IGNORECASE, (
        f"_NTLM_RE flags={_NTLM_RE.flags!r} missing re.IGNORECASE. "
        f"RFC 7235 §2.1 requires case-insensitive matching on every "
        f"HTTP auth-scheme literal (including vendor schemes like "
        f"NTLM). ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Auth-scheme detector table membership invariant: the new
#     ``_NTLM_RE`` must be wired into ``_AUTH_SCHEME_DETECTORS`` so the
#     canonical ``_scan_auth_scheme_credentials`` helper processes it
#     uniformly. A future regression that adds the regex but forgets
#     the table entry fails this invariant immediately.
# ---------------------------------------------------------------------------


def test_ntlm_re_is_in_auth_scheme_detectors_table() -> None:
    """The structural invariant from the 2026-05-16 Basic Auth drift
    round (and confirmed by the 2026-05-16 Negotiate round): every HTTP
    auth-scheme detector MUST be a member of ``_AUTH_SCHEME_DETECTORS``
    so the canonical processing path in
    ``_scan_auth_scheme_credentials`` applies uniformly (same
    ``_looks_like_secret`` filter, same coverage-range arbitration). A
    regression that adds ``_NTLM_RE`` but forgets the table binding
    ships the dead regex and the detection gap stays open."""
    from src.utils.secret_scanner import _AUTH_SCHEME_DETECTORS, _NTLM_RE

    detector_regexes = [regex for regex, _ in _AUTH_SCHEME_DETECTORS]
    assert _NTLM_RE in detector_regexes, (
        f"_NTLM_RE is not bound in _AUTH_SCHEME_DETECTORS. Per the "
        f"structural invariant, every auth-scheme regex MUST be a "
        f"tuple entry. ({SENTINEL_NTLM_DRIFT})"
    )

    detector_reasons = [reason for _, reason in _AUTH_SCHEME_DETECTORS]
    assert NTLM_REASON in detector_reasons, (
        f"Expected reason {NTLM_REASON!r} not found in "
        f"_AUTH_SCHEME_DETECTORS. Reasons present: "
        f"{detector_reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) End-to-end emission-shape inventory: real-world IIS / WinRM /
#     curl --ntlm / HAR-export / Wireshark-export shapes — the
#     case-insensitive fix must cover the common header-emission
#     shapes wholesale.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_header,case_label",
    [
        # HTTP-style log line shapes (curl -v --ntlm, requests --debug)
        (f"> Authorization: ntlm {_LONG_MIXED_BODY}", "lowercase-curl-debug"),
        (f"< Authorization: NTLM {_LONG_MIXED_BODY}", "uppercase-iis-debug"),
        (f"Authorization: NtLm {_LONG_MIXED_BODY}", "mixed-case-fixture"),
        # Browser HAR export shape (JSON nested header structure)
        (
            f'{{"name": "Authorization", "value": "ntlm {_LONG_MIXED_BODY}"}}',
            "har-export-lowercase",
        ),
        # Python-dict / JSON fixture shape (requests-ntlm test harness)
        (
            f'{{"Authorization": "NTLM {_LONG_MIXED_BODY}"}}',
            "json-canonical-case",
        ),
        # YAML fixture shape (Ansible / Kubernetes secret manifest)
        (
            f"authorization: 'NTLM {_LONG_MIXED_BODY}'",
            "yaml-uppercase",
        ),
        # WinRM debug-trace shape (PowerShell Set-PSDebug)
        (
            f"DEBUG: Authorization: NTLM {_LONG_MIXED_BODY}",
            "winrm-debug-trace",
        ),
        # Wireshark text-export shape
        (
            f"    Authorization: NTLM {_LONG_MIXED_BODY}",
            "wireshark-indented",
        ),
        # WWW-Authenticate response header shape (NTLM Type 2 challenge)
        (
            f"WWW-Authenticate: NTLM {_LONG_MIXED_BODY}",
            "www-authenticate-challenge",
        ),
    ],
)
def test_secret_scanner_detects_ntlm_across_emission_shapes(
    tmp_path: Path, raw_header: str, case_label: str
) -> None:
    """Real-world NTLM emission shapes (IIS debug logs, browser HAR
    exports, JSON/YAML fixtures, WinRM debug traces, Wireshark
    captures, WWW-Authenticate response headers) all canonicalise the
    case differently depending on the emitter; the detector must cover
    them wholesale."""
    file_path = tmp_path / f"fixture_{case_label}.txt"
    file_path.write_text(f"{raw_header}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NTLM_REASON in reasons, (
        f"Emission shape {case_label!r} ({raw_header!r}) did not "
        f"produce the NTLM-specific reason; got {reasons!r}. "
        f"({SENTINEL_NTLM_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking contract: the secret value never appears in the
#     findings' ``match`` field unmasked. This mirrors the Bearer /
#     Basic Auth / Negotiate detectors' masking-contract tests.
# ---------------------------------------------------------------------------


def test_ntlm_masking_contract(tmp_path: Path) -> None:
    """The raw base64-encoded NTLMSSP message must NOT appear unmasked
    in any finding's ``match`` field. The NTLM body decodes back to
    the binary NTLMSSP message (Type 1 / Type 2 / Type 3) which carries
    the NetNTLMv2 challenge-response in Type 3, so leaving it unmasked
    in findings (which surface in CI logs / PR comments / GitHub
    Issue bodies) would re-leak the credential at the very point of
    detection.
    """
    file_path = tmp_path / "fixture.txt"
    file_path.write_text(
        f"Authorization: NTLM {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    ntlm_findings = [f for f in findings if f.reason == NTLM_REASON]
    assert ntlm_findings, "Test setup failure: expected an NTLM finding."

    for finding in ntlm_findings:
        assert _LONG_MIXED_BODY not in finding.match, (
            f"Masking-contract violation: raw NTLMSSP body "
            f"{_LONG_MIXED_BODY!r} appears unmasked in finding "
            f"{finding!r}. The NTLM body decodes to a NetNTLMv2 "
            f"hash; emitting it unmasked re-leaks the credential at "
            f"the detection boundary. ({SENTINEL_NTLM_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (10) NTLMSSP structural-prefix demonstration: realistic NTLMSSP
#      messages start with ``TlRMTVNTUA`` (base64 of the ASCII
#      ``NTLMSSP\0`` magic per [MS-NLMP] §2.2 Header). This test
#      documents the structural anchor and ensures the detector fires
#      on realistically-shaped Type 1 / Type 2 / Type 3 messages.
# ---------------------------------------------------------------------------


def test_ntlm_detects_ntlmssp_type1_shaped_body(tmp_path: Path) -> None:
    """A realistic NTLMSSP Type 1 (Negotiate) message base64-encodes
    to a body starting with ``TlRMTVNTUA`` (base64 of the ASCII
    NTLMSSP magic ``0x4e 0x54 0x4c 0x4d 0x53 0x53 0x50 0x00``). This
    test pins detection on the canonical NTLMSSP shape.

    Documentation only — the detector matches purely on ``NTLM\\s+``
    plus the body alphabet, NOT on the ``TlRMTVNTUA`` prefix (which
    would tightly constrain to canonical NTLMSSP and miss
    partially-corrupted / proprietary / vendor-extended NTLM variants
    on the wire).
    """
    file_path = tmp_path / "ntlm_type1_capture.txt"
    file_path.write_text(
        f"Authorization: NTLM {_NTLMSSP_TYPE1_BODY}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert NTLM_REASON in reasons, (
        f"NTLMSSP Type 1 shaped body (TlRMTVNTUA-prefixed) not "
        f"detected. Got reasons {reasons!r}. ({SENTINEL_NTLM_DRIFT})"
    )

    # The TlRMTVNTUA prefix is a useful incident-response anchor —
    # identifies the NTLMSSP magic and distinguishes from Kerberos
    # AP-REQ (which uses the ``YII`` prefix = base64 of the ASN.1
    # SEQUENCE outer tag ``0x60 0x82``).
    assert _NTLMSSP_TYPE1_BODY.startswith("TlRMTVNTUA")
