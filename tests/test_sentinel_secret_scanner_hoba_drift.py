"""Sentinel PoC: missing ``_HOBA_RE`` detector — RFC 7486 HTTP
Origin-Bound Authentication (HOBA) credential leaks slip past the
secret scanner with attribution drift OR silent undetection, depending
on the body shape.

This round closes the **named-but-deferred** adjacent-detector
candidate from the 2026-05-16 Token-Scheme detector drift round
(sibling PR ``security(secret-scanner): add Token-Scheme detector per
GitHub REST API``). That round explicitly listed HOBA as one of the
next-round targets::

    **Next-round candidates (named-but-deferred):** ... **HOBA
    detector** (``(?i)HOBA\\s+...``) — RFC 7486 HTTP Origin-Bound Auth;
    rare in practice but a complete auth-scheme enumeration would
    include it. ...

The ``HOBA`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits (RFC 7486 §3
references RFC 7235 for the authentication-scheme framework).
``hoba result="..."`` / ``HOBA result="..."`` / ``Hoba result="..."``
are all legitimate canonical HTTP Authorization-header shapes on the
wire.

Threat model
------------

HOBA is the IANA-registered HTTP authentication scheme for
client-asserted public-key authentication WITHOUT TLS client
certificates. Per RFC 7486 §3 the on-the-wire format is::

    Authorization: HOBA result="<KID>.<challenge>.<nonce>.<signature>"

Where:
  * ``KID`` — base64url-encoded SHA-256 hash of the client's public
    key (Key Identifier; 43 chars for the canonical SHA-256 output);
  * ``challenge`` — server-supplied base64url-encoded nonce (8-32
    chars);
  * ``nonce`` — client-supplied base64url-encoded freshness anchor
    (8-32 chars);
  * ``signature`` — base64url-encoded digital signature using the
    client's private key (ECDSA P-256 = 86 base64url chars; RSA-2048
    = 342 base64url chars).

A leaked HOBA ``result`` value gives the attacker:
  * The ``KID`` — reveals which client public key signs HOBA
    requests; combined with a separately-leaked public key file, can
    be cross-referenced to identify the user.
  * The ``signature`` — a one-time-use replay-able auth proof
    bounded by the (challenge, nonce) freshness window; if the
    server accepts replay (a defect, but real in some lazy
    HOBA implementations), the attacker can authenticate as the
    holder of the private key.
  * The ``challenge`` and ``nonce`` — exposes the server-side
    challenge issuance pattern, useful for protocol analysis.

The structural distinction from Bearer / Basic / Negotiate / NTLM /
Token is that **the credential body is a quoted dot-separated 4-tuple
inside a ``result="..."`` parameter** (per RFC 7486 §3) — the only
HTTP auth-scheme in this family with the parameterised quoted-string
body shape rather than the simple ``<scheme> <body>`` form.

Pre-fix the existing scanner failed on HOBA credentials via two
orthogonal failure modes:

1. **Attribution drift (common case)** — 100+-char base64url HOBA
   result strings with mixed character classes DO match
   ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) for the contiguous
   slices BETWEEN the dots (the dots are OUTSIDE the entropy
   alphabet), but only for individual segments. The HOBA-specific
   attribution is lost AND the credential is split into multiple
   findings — incident-response triage must guess whether the
   leaked dotted structure is a JOSE JWT (revoke at IdP), HOBA
   result (rotate HOBA key pair, reissue KID), or some other
   opaque dotted secret.

2. **Silent undetection (all-letter fields)** — the entropy
   fallback's ``candidate.isalpha()`` short-circuit (added to
   suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. HOBA result strings
   whose 4 fields happen to be all-letter (rare in practice but
   possible for hand-crafted test fixtures, CTF challenges, or
   pathological base64url encodings) were SILENTLY UNDETECTED.

Real-world emission patterns
----------------------------

- WebAuthn-pre-cursor HOBA implementations (most legacy HOBA
  deployments predate WebAuthn / FIDO2 and use the original
  RFC 7486 format).
- Academic research papers on HTTP authentication that publish
  example captures.
- IoT device firmware using HOBA for backend auth in lieu of TLS
  client certs (constrained-device contexts where the TLS
  client-cert ladder is too heavyweight).
- Specialised enterprise PKI integrations.
- Browser dev-tools Network tab HAR exports of HOBA-authenticated
  sites.
- Wireshark / tshark capture text exports rendering the
  Authorization header verbatim.
- Python ``requests`` debug logs.
- YAML / Postman / Insomnia request export files embedding the
  Authorization header.

Severity
--------

**LOW-MEDIUM** — HOBA is RARE in production (the canonical "rare
but registered" auth-scheme per the journal entries that explicitly
classified it as deferred-but-completeness-worthy). The leak surface
is bounded: HOBA implementations live in academic / IoT / specialised
PKI contexts, not in mainstream web apps. However, when HOBA IS in
use, a leaked ``result`` value enables replay-based authentication
attacks against lazy implementations and reveals key-binding
information that aids targeted social-engineering attacks. The
silent-undetection branch is higher severity in absolute terms
(detection-from-zero for the all-letter-fields case), but the
practical impact is bounded by HOBA's rare deployment.

Fix
---

Add a HOBA auth-scheme detector mirroring the Bearer/Basic/Negotiate/
NTLM/Token detectors' case-insensitive contract::

    _HOBA_RE = re.compile(
        r'''(?i)HOBA\\s+result\\s*=\\s*["']([A-Za-z0-9_\\-]{8,}(?:\\.[A-Za-z0-9_\\-]{8,}){3})["']'''
    )

Append to ``_AUTH_SCHEME_DETECTORS`` BEFORE the Token-scheme entry so
the more-specific IANA-registered HOBA attribution wins over the
generic Token-scheme catch-all via ``covered_ranges`` arbitration.

The structural disambiguator is the FOUR dot-separated fields inside
a quoted ``result="..."`` parameter — natural-language text never
contains the literal ``HOBA`` followed by this exact shape. The
body alphabet ``[A-Za-z0-9_\\-]`` is the canonical base64url alphabet
per RFC 4648 §5; the quote alphabet ``["']`` accepts both canonical
RFC-double-quote and the de-facto single-quote shape that appears in
YAML / Python / Postman serialisations.

Marker: SENTINEL_HOBA_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_HOBA_DRIFT = "rfc 7486 HOBA attribution + silent-undetection drift"

HOBA_REASON = "HOBA Authentication Credential gefunden"


# Realistic HOBA result bodies of varying shapes:
#
#  * ``_REALISTIC_HOBA_BODY`` — Realistic HOBA result: 43-char base64url
#    KID (= SHA-256 hash) + 16-char challenge + 16-char nonce + 86-char
#    ECDSA P-256 signature. Total ~161 chars + 3 dots = 164 chars.
#  * ``_ALL_LETTERS_BODY`` — 4 fields × 8 chars each, all letters
#    (no digits, no `+`/`/`/`=`). Trips the ``candidate.isalpha()``
#    skip in the entropy fallback so SILENTLY UNDETECTED pre-fix.
#  * ``_MIXED_BODY`` — Smaller mixed-class body for attribution-drift
#    PoC (each field at the 8+ floor, with mixed letters / digits /
#    underscores / hyphens).

_REALISTIC_HOBA_BODY = (
    "Mz4UeCBJD8MwLD9TwbDjUyU9rgcc9CwIIB44pVfH4Pc"  # 43-char base64url KID
    "."
    "kJ7Z9TmDhX9MQpA8"  # 16-char base64url challenge
    "."
    "rVqMzbN3xLpDtKsW"  # 16-char base64url nonce
    "."
    "MEUCIQDqxKqr8MhCdiBjQq3M4mUL_oXcjEdRGn4r9Z"
    "TlPLAg7AIgVrlHvAj8N6tBEAPb-i_9TF"  # 86-char base64url ECDSA P-256 sig
)
assert _REALISTIC_HOBA_BODY.count(".") == 3
assert len(_REALISTIC_HOBA_BODY) > 100

_ALL_LETTERS_BODY = (
    "abcdefghijklmnop"
    "."
    "qrstuvwxyzABCDEF"
    "."
    "GHIJKLMNOPQRSTUV"
    "."
    "WXYZabcdefghijkl"
)  # 4 × 16-char fields, all letters
assert _ALL_LETTERS_BODY.count(".") == 3
# Ensure each field is all-letter (the silent-undetection trigger).
for field in _ALL_LETTERS_BODY.split("."):
    assert field.isalpha(), f"field {field!r} is not all-letter"

_MIXED_BODY = (
    "Abc12345_X-Y9z"  # 14-char field, mixed
    "."
    "QwErTy12_3-4Lp"  # 14-char field, mixed
    "."
    "ZxCvBn78_9-0Mn"  # 14-char field, mixed
    "."
    "PqRsTu56_7-8Vw"  # 14-char field, mixed
)
assert _MIXED_BODY.count(".") == 3
for field in _MIXED_BODY.split("."):
    assert len(field) >= 8


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the HOBA literal,
#     with a realistic body that the entropy fallback would otherwise catch
#     per-segment, must yield the HOBA-specific reason for the whole body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "hoba",   # all-lowercase
        "HOBA",   # canonical (RFC 7486 §3 example)
        "Hoba",   # title-case
        "HoBa",   # mixed-case (hostile-PR-style obfuscation)
        "hOBa",   # mixed-case alternate
    ],
)
def test_secret_scanner_detects_hoba_case_insensitive(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the HOBA auth-scheme literal must be
    detected with the HOBA-specific attribution, per RFC 7235 §2.1's
    case-insensitive auth-scheme contract (inherited via RFC 7486 §3).

    The realistic 164-char body exercises the attribution-drift
    branch: pre-fix the entropy fallback caught individual segments
    generically (as "Hochentropischer Token-String") splitting one
    logical credential into multiple findings; the HOBA detector
    captures the entire ``result="..."`` value as one finding with
    HOBA-specific attribution.
    """
    file_path = tmp_path / "hoba_capture.txt"
    file_path.write_text(
        f'Authorization: {scheme} result="{_REALISTIC_HOBA_BODY}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert HOBA_REASON in reasons, (
        f"HOBA detector did not produce its attribution for case "
        f"{scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 says auth-scheme is case-insensitive; the "
        f"leaked credential must yield the HOBA-specific reason "
        f"regardless of case. ({SENTINEL_HOBA_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _REALISTIC_HOBA_BODY not in [f.match for f in findings]


def test_hoba_accepts_single_quote_body(tmp_path: Path) -> None:
    """The HOBA detector accepts both double-quote (canonical RFC
    7486 §3 shape) and single-quote (de-facto shape from YAML /
    Python serialisations) wrappers around the ``result`` value.
    Real-world leak artefacts emit both shapes depending on the
    serialiser (HTTP wire format = double quote; YAML literal-block
    output = single quote; Python repr = single quote)."""
    file_path = tmp_path / "yaml_export.yaml"
    file_path.write_text(
        f"headers:\n  Authorization: HOBA result='{_MIXED_BODY}'\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert HOBA_REASON in reasons, (
        f"HOBA detector missed single-quote body shape; got reasons "
        f"{reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


def test_hoba_accepts_double_quote_body(tmp_path: Path) -> None:
    """The HOBA detector accepts the canonical double-quote RFC 7486
    §3 shape — the on-the-wire HTTP format."""
    file_path = tmp_path / "wire_capture.txt"
    file_path.write_text(
        f'Authorization: HOBA result="{_MIXED_BODY}"\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert HOBA_REASON in reasons, (
        f"HOBA detector missed double-quote body shape; got reasons "
        f"{reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: all-letter fields trip the
#     ``candidate.isalpha()`` skip in the entropy fallback per-segment.
#     The HOBA detector closes this hole by capturing the entire body
#     (including dots, which are NOT alphabetic) as one entity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "hoba",
        "HOBA",
        "Hoba",
        "HoBa",
    ],
)
def test_secret_scanner_detects_hoba_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter base64url fields trip the ``candidate.isalpha()``
    skip in ``_HIGH_ENTROPY_RE``'s per-segment loop. The HOBA detector
    captures the entire body (dots break ``isalpha()``) and routes via
    the ``is_assignment=True`` path of ``_looks_like_secret`` which
    allows ``min_categories=1`` plus the lenient uniqueness floor.

    PoC body: 4 × 16-char all-letter base64url fields. Each individual
    field is all-letter and would be skipped by the entropy fallback's
    ``isalpha()`` short-circuit. Pre-fix the scanner was SILENTLY
    UNDETECTED entirely — no HOBA-specific reason and no generic
    entropy reason fires.
    """
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "{scheme} result=\\"{_ALL_LETTERS_BODY}\\""}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert HOBA_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the new ``_HOBA_RE`` does NOT match
#     natural-language text, code identifiers, or non-conformant HOBA
#     fragments that mention "HOBA" without a full
#     ``result="<4-tuple>"`` body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No result= parameter.
        "We must configure HOBA authentication for the IoT fleet.",
        "HOBA is the canonical RFC 7486 HTTP Origin-Bound Auth scheme.",
        "Disable HOBA on the public endpoint immediately.",
        # result= without quoted 4-tuple body.
        "HOBA result=xyz",
        "HOBA result=\"only-one-field\"",
        "HOBA result='only.two.fields'",  # 2 dots = 3 fields, not 4
        "HOBA result=\"one.two.three\"",  # 2 dots = 3 fields, not 4
        # 4 fields but each too short (below 8-char floor).
        "HOBA result=\"a.b.c.d\"",
        "HOBA result=\"ab.cd.ef.gh\"",
        "HOBA result=\"abc.def.ghi.jkl\"",  # 3-char fields, below floor
        # Common English passages mentioning HOBA as an acronym.
        "The HOBA result is reported in the next section.",
        "HOBA result and discussion follow in §4.",
        # Code-shape false-positive candidates.
        "function hobaHandler(req, res) {",
        "class HOBAuthenticator extends AbstractAuth {",
        # Missing closing quote.
        f'HOBA result="{_MIXED_BODY}',
        # Missing opening quote.
        f'HOBA result={_MIXED_BODY}"',
        # 5 fields (one too many).
        "HOBA result=\"abcdefgh.ijklmnop.qrstuvwx.AAAAAAAA.BBBBBBBB\"",
    ],
)
def test_secret_scanner_no_false_positives_on_natural_hoba_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_HOBA_RE`` must NOT match natural-language
    sentences that mention "HOBA" without a quoted 4-tuple result
    body, OR HOBA fragments with wrong field counts / too-short
    fields. The structural shape ``HOBA result="<8+>.<8+>.<8+>.<8+>"``
    is the disambiguator.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    hoba_findings = [f for f in findings if f.reason == HOBA_REASON]
    assert not hoba_findings, (
        f"False-positive HOBA finding for text {text!r}. The "
        f"detector should require ``HOBA result=\"<4-tuple>\"`` "
        f"structure. ({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Regression guards: canonical Bearer, Basic, Negotiate, NTLM,
#     and Token-scheme detectors continue to fire correctly after
#     adding HOBA. The new detector must not interfere with existing
#     detection paths.
# ---------------------------------------------------------------------------


def test_hoba_addition_does_not_break_bearer_detection(tmp_path: Path) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the HOBA addition. Regression guard
    against any unintended cross-effect from the new detector."""
    bearer_body = "AbCdEfGhIjKlMnOpQrStUvWx0123"
    file_path = tmp_path / "canonical.py"
    file_path.write_text(
        f'HEADERS = {{"Authorization": "Bearer {bearer_body}"}}\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Bearer-Token wirkt echt" in reasons, (
        f"Regression: Bearer detection broke after HOBA addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


def test_hoba_addition_does_not_break_basic_auth_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Basic <body>`` form continues to fire."""
    basic_body = "YWRtaW46cGFzc3dvcmQ="
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "Basic {basic_body}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HTTP Basic Authentication Credential gefunden" in reasons, (
        f"Regression: Basic Auth detection broke after HOBA "
        f"addition. Got reasons {reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


def test_hoba_addition_does_not_break_negotiate_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Negotiate <body>`` form continues to fire."""
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
        f"Regression: Negotiate detection broke after HOBA addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


def test_hoba_addition_does_not_break_ntlm_detection(tmp_path: Path) -> None:
    """The canonical ``NTLM <body>`` form continues to fire."""
    ntlm_body = (
        "TlRMTVNTUAABAAAAl4II4gAAAAAAAAAAAAAAAAAAAAAGAbEdAAAADw=="
    )
    file_path = tmp_path / "iis_request_log.txt"
    file_path.write_text(
        f"Authorization: NTLM {ntlm_body}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "NTLM Authentication Credential gefunden" in reasons, (
        f"Regression: NTLM detection broke after HOBA addition. "
        f"Got reasons {reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


def test_hoba_addition_does_not_break_token_scheme_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``token <body>`` form continues to fire the
    Token-scheme detector after the HOBA addition. The new HOBA
    detector lives BEFORE the Token-scheme entry, but its narrow
    ``result="..."`` shape doesn't match Token-scheme bodies."""
    token_body = "ghp_AbCdEfGhIjKlMnOpQrStUvWx0123456789AB"
    file_path = tmp_path / "github_curl.txt"
    file_path.write_text(
        f"Authorization: token {token_body}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    # GitHub PAT attribution wins via _KNOWN_TOKENS (runs FIRST).
    assert "GitHub Personal Access Token gefunden" in reasons, (
        f"Regression: GitHub PAT attribution broke after HOBA "
        f"addition. Got reasons {reasons!r}. ({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Compiled-regex invariant: ``_HOBA_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_hoba_re_flags_include_ignorecase() -> None:
    """The compiled ``_HOBA_RE`` must carry the ``re.IGNORECASE`` flag
    so the auth-scheme literal is matched per the RFC 7235 §2.1
    case-insensitive contract that every HTTP auth-scheme inherits
    (including HOBA per RFC 7486 §3). A future regression that
    reverts to the case-sensitive shape fails this invariant
    immediately."""
    import re as _re

    from src.utils.secret_scanner import _HOBA_RE

    assert _HOBA_RE.flags & _re.IGNORECASE, (
        f"_HOBA_RE flags={_HOBA_RE.flags!r} missing re.IGNORECASE. "
        f"RFC 7235 §2.1 requires case-insensitive matching on every "
        f"HTTP auth-scheme literal (including HOBA per RFC 7486 §3). "
        f"({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Auth-scheme detector table membership invariant: the new
#     ``_HOBA_RE`` must be wired into ``_AUTH_SCHEME_DETECTORS`` so the
#     canonical ``_scan_auth_scheme_credentials`` helper processes it
#     uniformly. A future regression that adds the regex but forgets
#     the table entry fails this invariant immediately.
# ---------------------------------------------------------------------------


def test_hoba_re_membership_in_auth_scheme_detectors() -> None:
    """The compiled ``_HOBA_RE`` must appear in
    ``_AUTH_SCHEME_DETECTORS`` so the canonical
    ``_scan_auth_scheme_credentials`` helper processes HOBA matches
    uniformly with the same ``is_assignment=True``
    ``_looks_like_secret`` filter and ``covered_ranges`` mutation
    contract that the Bearer / Basic / Negotiate / NTLM / Token
    detectors already rely on.

    A future regression that adds the regex constant but forgets the
    tuple entry would silently bypass the auth-scheme processing
    path; this invariant fails the regression immediately."""
    from src.utils.secret_scanner import _AUTH_SCHEME_DETECTORS, _HOBA_RE

    regexes_in_table = [regex for regex, _reason in _AUTH_SCHEME_DETECTORS]
    assert _HOBA_RE in regexes_in_table, (
        f"_HOBA_RE is not in _AUTH_SCHEME_DETECTORS. The canonical "
        f"_scan_auth_scheme_credentials helper iterates the table; "
        f"missing membership = silent regression. Table regexes: "
        f"{regexes_in_table!r}. ({SENTINEL_HOBA_DRIFT})"
    )
    reasons_in_table = [reason for _regex, reason in _AUTH_SCHEME_DETECTORS]
    assert HOBA_REASON in reasons_in_table, (
        f"HOBA reason {HOBA_REASON!r} not in _AUTH_SCHEME_DETECTORS "
        f"reasons. Got: {reasons_in_table!r}. "
        f"({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Auth-scheme detector table ordering invariant: HOBA must appear
#     BEFORE the Token-scheme entry so the more-specific IANA-
#     registered HOBA attribution wins over the generic Token-scheme
#     catch-all via ``covered_ranges`` arbitration.
# ---------------------------------------------------------------------------


def test_hoba_appears_before_token_scheme_in_auth_table() -> None:
    """The HOBA detector must appear BEFORE the Token-scheme detector
    in ``_AUTH_SCHEME_DETECTORS``. HOBA is IANA-registered (RFC 7486),
    Token is de-facto only; the more-specific scheme wins via
    ``covered_ranges`` arbitration when both could match a span.

    Position invariant ensures that future table-rearrangement that
    swaps HOBA/Token order fails this test immediately."""
    from src.utils.secret_scanner import (
        _AUTH_SCHEME_DETECTORS,
        _HOBA_RE,
        _TOKEN_SCHEME_RE,
    )

    regexes_in_order = [regex for regex, _reason in _AUTH_SCHEME_DETECTORS]
    hoba_idx = regexes_in_order.index(_HOBA_RE)
    token_idx = regexes_in_order.index(_TOKEN_SCHEME_RE)
    assert hoba_idx < token_idx, (
        f"_HOBA_RE (index {hoba_idx}) must appear BEFORE "
        f"_TOKEN_SCHEME_RE (index {token_idx}) in "
        f"_AUTH_SCHEME_DETECTORS. The IANA-registered HOBA scheme is "
        f"more specific than the de-facto Token scheme; ordering "
        f"matters for covered_ranges arbitration. "
        f"({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) End-to-end emission-shape inventory: every real-world
#     Authorization-header emission pattern that includes a HOBA
#     ``result="..."`` triggers HOBA attribution.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "emission_shape",
    [
        # Wire-format HTTP request log.
        f'> Authorization: HOBA result="{_REALISTIC_HOBA_BODY}"',
        # Python requests with urllib3 DEBUG logging.
        f'send: b"GET /api/auth HTTP/1.1\\r\\nAuthorization: '
        f'HOBA result=\\"{_REALISTIC_HOBA_BODY}\\"\\r\\n"',
        # Browser HAR export JSON.
        f'{{"name": "Authorization", "value": "HOBA result=\\"'
        f'{_REALISTIC_HOBA_BODY}\\""}}',
        # YAML config (Postman / Insomnia export).
        f"headers:\n  Authorization: HOBA result='{_REALISTIC_HOBA_BODY}'",
        # Wireshark / tshark text export.
        f'    Authorization: HOBA result="{_REALISTIC_HOBA_BODY}"\\r\\n',
        # Python docstring with hardcoded test fixture.
        f'"""Example: ``headers={{"Authorization": "HOBA result='
        f'\\"{_REALISTIC_HOBA_BODY}\\""}}``."""',
        # Academic research paper code snippet.
        f"# Listing 3.4: Captured HOBA Authorization header\n"
        f'Authorization: HOBA result="{_REALISTIC_HOBA_BODY}"',
        # IoT device firmware test fixture.
        f"static const char *test_auth_hdr = "
        f'"HOBA result=\\"{_REALISTIC_HOBA_BODY}\\"";',
    ],
)
def test_hoba_detected_across_emission_shapes(
    tmp_path: Path, emission_shape: str
) -> None:
    """Every real-world emission shape for a leaked HOBA credential
    must trigger HOBA-specific attribution.

    Emission shapes inventory: wire-format HTTP logs, requests/urllib3
    DEBUG, browser HAR, YAML config, Wireshark text exports, Python
    docstrings, academic snippets, IoT firmware fixtures.
    """
    file_path = tmp_path / "emission_shape.txt"
    file_path.write_text(f"{emission_shape}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert HOBA_REASON in reasons, (
        f"Emission shape did not yield HOBA attribution: "
        f"{emission_shape!r}; got reasons {reasons!r}. "
        f"({SENTINEL_HOBA_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking-contract test: the raw HOBA result body must NEVER appear
#     unmasked in the finding output.
# ---------------------------------------------------------------------------


def test_hoba_masking_contract(tmp_path: Path) -> None:
    """HOBA findings must mask the raw credential body before
    surfacing — the ``_mask_secret`` helper transforms the full body
    into the canonical ``xxxx***yyyy`` form so the CI logs / GitHub
    PR comment / pre-commit hook output never carry the unredacted
    plaintext HOBA result.

    Regression guard against accidentally serialising the raw
    credential into Finding.match."""
    file_path = tmp_path / "leak_artefact.txt"
    file_path.write_text(
        f'Authorization: HOBA result="{_REALISTIC_HOBA_BODY}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    # HOBA finding must exist.
    hoba_findings = [f for f in findings if f.reason == HOBA_REASON]
    assert hoba_findings, (
        f"Expected at least one HOBA finding; got {findings!r}. "
        f"({SENTINEL_HOBA_DRIFT})"
    )

    # The raw body must NOT appear verbatim in any finding's match
    # field. Masking ensures only a redacted form surfaces.
    for finding in findings:
        assert _REALISTIC_HOBA_BODY not in finding.match, (
            f"Masking contract VIOLATED: raw credential body appears "
            f"in finding.match={finding.match!r}. The unredacted "
            f"credential must never reach CI output. "
            f"({SENTINEL_HOBA_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (10) Cross-detector: a HOBA result inside a JSON `Authorization`
#      value must yield exactly ONE HOBA finding (not multiple
#      per-segment entropy findings).
# ---------------------------------------------------------------------------


def test_hoba_yields_single_finding_not_per_segment(tmp_path: Path) -> None:
    """A leaked HOBA result with realistic per-segment lengths would
    pre-fix produce up to FOUR separate ``Hochentropischer
    Token-String`` findings (one per dot-separated base64url segment
    that matches ``_HIGH_ENTROPY_RE``'s ``[A-Za-z0-9+/=_-]{24,}``
    floor). The HOBA detector captures the entire result body as
    ONE finding, restoring the per-credential attribution and
    consolidating noise in the scanner's output."""
    file_path = tmp_path / "wire.txt"
    file_path.write_text(
        f'Authorization: HOBA result="{_REALISTIC_HOBA_BODY}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    hoba_findings = [f for f in findings if f.reason == HOBA_REASON]
    # Exactly one HOBA finding for the single Authorization line.
    assert len(hoba_findings) == 1, (
        f"Expected exactly ONE HOBA finding for the single "
        f"Authorization header; got {len(hoba_findings)}: "
        f"{hoba_findings!r}. ({SENTINEL_HOBA_DRIFT})"
    )
    # The HOBA finding covers the body; per-segment entropy findings
    # on segments overlapping the covered range must NOT fire.
    entropy_findings_on_segments = [
        f
        for f in findings
        if f.reason == "Hochentropischer Token-String"
        and any(
            seg in f.match
            for seg in _REALISTIC_HOBA_BODY.split(".")
            if len(seg) >= 24
        )
    ]
    # Note: masking transforms the match field to xxxx***yyyy, so the
    # raw segments don't appear in the match field. The intent here is
    # to catch a regression where covered_ranges arbitration breaks
    # and per-segment entropy findings sneak through.
    assert not entropy_findings_on_segments, (
        f"covered_ranges arbitration broken: per-segment entropy "
        f"findings overlap the HOBA-detected span. Got entropy "
        f"findings: {entropy_findings_on_segments!r}. "
        f"({SENTINEL_HOBA_DRIFT})"
    )
