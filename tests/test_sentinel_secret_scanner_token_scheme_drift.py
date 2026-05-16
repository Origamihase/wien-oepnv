"""Sentinel PoC: missing ``_TOKEN_SCHEME_RE`` detector — leaked
``Authorization: Token <opaque-body>`` credentials (GitHub legacy REST
API alias, Rails / Devise default, Zendesk, Spotify legacy, generic
internal-API ``Token``-scheme consumers) slip past the secret scanner
with attribution drift OR silent undetection, depending on the body
shape.

This round closes the **named-but-deferred** adjacent-detector
candidate from the 2026-05-16 NTLM detector drift round (sibling PR
``security(secret-scanner): add NTLM detector per [MS-NLMP]``). That
round explicitly named the GitHub ``Token`` scheme detector as the
first next-round target::

    **Next-round candidates (named-but-deferred):** **GitHub ``Token``
    scheme detector** (``(?i)token\\s+[A-Za-z0-9_\\-]{36,}``) —
    GitHub's older API alias for Bearer (``Authorization: token
    ghp_...``); the embedded GitHub PAT (``ghp_``-prefixed) already
    matches ``_KNOWN_TOKENS`` via the ``ghp_`` regex, so the
    ``token`` scheme detector is attribution-only for opaque
    non-GitHub tokens that happen to use the alias.

The ``Token`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits. Although
``Token`` is NOT in the IANA HTTP Authentication Scheme Registry
(Basic / Bearer / Digest / HOBA / Mutual / Negotiate / OAuth /
SCRAM-SHA-1 / SCRAM-SHA-256 / vapid are the registered IETF-RFC
entries; NTLM and AWS4-HMAC-SHA256 are vendor entries), it is widely
used in the wild as a de-facto HTTP auth-scheme literal — GitHub's
legacy REST API documentation prescribes ``Authorization: token
<PAT>`` as an accepted alias for ``Bearer``, Rails / Devise's default
``token_authenticatable`` shipped with ``Authorization: Token
<40-char token>``, and many internal REST APIs accept the same
shape. The wire-protocol position determines placement in
``_AUTH_SCHEME_DETECTORS`` per the structural invariant established
by the Basic Auth round (NTLM is also vendor-defined and lives in
``_AUTH_SCHEME_DETECTORS``).

Threat model
------------

A leaked ``Authorization: Token <body>`` (where the body is a 36+
char opaque secret) in committed source / log artefacts / CI debug
snippets / hostile-PR fragments fails the existing detection
branches:

1. **Attribution drift (common case)** — opaque 36+-char
   alphanumeric / underscore / hyphen tokens DO match
   ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically and
   land as ``Hochentropischer Token-String`` findings. The
   Token-scheme attribution is lost — incident-response triage must
   guess whether the leaked entropy span is a Bearer Token (revoke
   at the issuing IdP), a Basic Auth credential (rotate user
   password), an NTLM hash (relay + offline crack), a Kerberos
   ticket (KDC revocation), or a Token-scheme credential (vendor-
   specific revocation per the consuming API's documentation). Five
   distinct IR flows that hinge on per-scheme attribution.

2. **Silent undetection (all-letter body — niche but possible)** —
   the entropy fallback's ``candidate.isalpha()`` short-circuit
   (added to suppress LongCamelCaseClassName false positives)
   rejects bodies composed entirely of ``[A-Za-z]`` characters.
   Pre-fix every leaked ``Token <all-letter-body>`` is **silently
   undetected** entirely.

Real-world emission patterns
----------------------------

- curl ``-v`` debug logs for GitHub REST API calls
  (``Authorization: token ghp_...``).
- Browser dev-tools Network tab HAR exports for sites using
  ``Token`` instead of ``Bearer`` (Rails/Devise default,
  internal API admin UIs).
- ``requests`` library debug logs with urllib3 DEBUG logging.
- Python / Ruby / Go / Node API client docstrings hardcoding tokens
  in test fixtures.
- CI/CD pipeline debug output for ``Token``-scheme consumers.
- Postman / Insomnia / Bruno saved-request export JSON / YAML.
- Kibana / Grafana / Datadog log views ingesting unredacted
  Authorization-header logs.

Severity
--------

**MEDIUM** — attribution-drift case loses the Token-scheme-specific
recovery surface (rotate at the consuming API's vendor settings
page), forcing IR triage to guess between Bearer / Basic / NTLM /
Kerberos / Token revocation flows. The silent-undetection case is
higher severity — the credential sits committed in plaintext with
NO detection at all. Mitigated only by the requirement that the
leaked credential live alongside a ``Token`` auth-scheme literal in
the same content blob; in practice this covers every leak through
GitHub legacy curl debug logs, Rails/Devise client debug output,
HAR exports of Token-scheme-authenticated sites, and Postman /
Insomnia config exports.

Fix
---

Add a Token auth-scheme detector mirroring the NTLM detector's
case-insensitive contract::

    _TOKEN_SCHEME_RE = re.compile(r"(?i)Token\\s+([A-Za-z0-9_\\-]{36,})")

Append to ``_AUTH_SCHEME_DETECTORS`` so the existing
``_scan_auth_scheme_credentials`` helper processes matches uniformly
with the same ``is_assignment=True`` ``_looks_like_secret`` filter
that the Bearer / Basic / Negotiate / NTLM detectors already use.

The 36+ char body floor is the structural disambiguator against
natural-language false positives — the English word ``Token``
appears commonly in code identifiers and prose, but almost never
followed by 36+ contiguous chars from ``[A-Za-z0-9_\\-]``. The
canonical leak shapes are ALL above 36 chars (GitHub PAT
``ghp_<36>`` = 40 chars; GitHub classic 40-char hex;
Rails / Devise 40-char hex; Zendesk 40+ chars). The body alphabet
``[A-Za-z0-9_\\-]`` is the canonical alphanumeric + underscore +
hyphen shape — deliberately MORE restrictive than Bearer's
``[A-Za-z0-9\\-_.]`` (no ``.``) and Basic / Negotiate / NTLM's
``[A-Za-z0-9+/=]`` (no ``+/=``) because JWTs and base64 tokens with
padding are emitted via Bearer / Basic / Negotiate, not via
the Token scheme.

Marker: SENTINEL_TOKEN_SCHEME_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_TOKEN_SCHEME_DRIFT = (
    "http Token-scheme attribution + silent-undetection drift"
)

TOKEN_SCHEME_REASON = "HTTP Token Authentication Credential gefunden"


# Realistic opaque token bodies of varying shapes:
#
#  * ``_LONG_MIXED_BODY`` — 60-char mixed-class alphanumeric body
#    (Rails / Devise default 40-char-hex variant extended with
#    additional entropy). Mimics an opaque non-GitHub Token-scheme
#    credential. Used for the attribution-drift PoCs.
#  * ``_ALL_LETTERS_BODY`` — 40-char all-letter body. Trips the
#    ``candidate.isalpha()`` skip in the entropy fallback so
#    SILENTLY UNDETECTED pre-fix. Used for the silent-undetection PoC.
#  * ``_GITHUB_PAT_BODY`` — GitHub Personal Access Token shape
#    (``ghp_`` + 36 alphanumeric chars = 40 chars total). Used to
#    document the cross-detector ordering invariant — the more-
#    specific GitHub PAT attribution wins over the generic
#    Token-scheme attribution via ``_KNOWN_TOKENS`` matching first.
#  * ``_RAILS_DEVISE_BODY`` — 40-char lowercase hex body. The
#    canonical Rails / Devise ``token_authenticatable`` shape. Mixed
#    chars (hex includes digits + lowercase letters) so it would
#    also trigger the entropy fallback; proves the Token-scheme
#    detector wins over generic attribution.

_LONG_MIXED_BODY = (
    "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMnOpQrStUvWx"
)  # 60 chars
assert len(_LONG_MIXED_BODY) >= 36
# Must mix character classes so it would also trigger entropy fallback
# (proves the Token-scheme detector wins over generic attribution).
assert any(c.isupper() for c in _LONG_MIXED_BODY)
assert any(c.islower() for c in _LONG_MIXED_BODY)
assert any(c.isdigit() for c in _LONG_MIXED_BODY)
assert all(
    c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    for c in _LONG_MIXED_BODY
)

_ALL_LETTERS_BODY = "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlMn"  # 40 chars
assert _ALL_LETTERS_BODY.isalpha()
assert len(_ALL_LETTERS_BODY) >= 36

# Canonical GitHub PAT shape (``ghp_<36>``).
_GITHUB_PAT_BODY = "ghp_AbCdEfGhIjKlMnOpQrStUvWx0123456789AB"
assert _GITHUB_PAT_BODY.startswith("ghp_")
assert len(_GITHUB_PAT_BODY) == 40

# Canonical Rails / Devise ``token_authenticatable`` shape (40-char hex).
_RAILS_DEVISE_BODY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
assert len(_RAILS_DEVISE_BODY) == 40
assert all(c in "0123456789abcdef" for c in _RAILS_DEVISE_BODY)


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the Token literal,
#     with a body that the entropy fallback DOES match, must yield the
#     Token-scheme-specific reason (not the generic entropy reason).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "token",   # all-lowercase (GitHub recommended legacy form)
        "Token",   # title-case (Rails/Devise default)
        "TOKEN",   # all-uppercase
        "ToKeN",   # mixed-case (hostile-PR-style obfuscation)
        "tOkEn",   # mixed-case alternate
    ],
)
def test_secret_scanner_detects_token_scheme_case_insensitive_long_mixed(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the Token auth-scheme literal must be
    detected with the Token-scheme-specific attribution, per RFC 7235
    §2.1's case-insensitive auth-scheme contract (inherited by
    every HTTP auth-scheme literal, including the de-facto-but-not-
    IANA-registered ``Token`` scheme).

    The 60-char mixed-class body exercises the attribution-drift
    branch: pre-fix the entropy fallback caught the body span
    generically (as "Hochentropischer Token-String"), but the
    Token-scheme-specific reason that pinpoints revocation flow
    (rotate at the consuming API's vendor settings page) was lost.
    """
    file_path = tmp_path / "github_legacy_curl.txt"
    file_path.write_text(
        f"Authorization: {scheme} {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Token-scheme detector did not produce its attribution for "
        f"case {scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 says auth-scheme is case-insensitive; the "
        f"leaked credential must yield the Token-scheme-specific "
        f"reason regardless of case. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _LONG_MIXED_BODY not in [f.match for f in findings]


def test_secret_scanner_detects_rails_devise_token_shape(
    tmp_path: Path,
) -> None:
    """The canonical Rails / Devise ``token_authenticatable`` 40-char
    hex shape, the most common Token-scheme emission outside GitHub,
    must yield Token-scheme-specific attribution. Hex bodies include
    digits + lowercase letters (2 character categories), so they DO
    match the entropy fallback — but pre-fix only as generic
    "Hochentropischer Token-String". The Token-scheme detector
    preserves the per-scheme attribution.
    """
    file_path = tmp_path / "rails_curl_debug.txt"
    file_path.write_text(
        f"> Authorization: Token {_RAILS_DEVISE_BODY}\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Rails / Devise Token shape lost Token-scheme attribution; "
        f"got reasons {reasons!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: all-letter bodies trip the
#     ``candidate.isalpha()`` skip in the entropy fallback. The
#     Token-scheme detector closes this hole.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "token",
        "Token",
        "TOKEN",
        "ToKeN",
    ],
)
def test_secret_scanner_detects_token_scheme_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter bodies trip the ``candidate.isalpha()`` skip in
    ``_HIGH_ENTROPY_RE``'s loop (which exists to suppress false
    positives on LongCamelCaseClassNames). The Token-scheme detector
    catches these via the ``is_assignment=True`` path of
    ``_looks_like_secret`` which allows ``min_categories=1``.

    PoC body: 40-char all-letter token. The pre-fix scanner is
    SILENTLY UNDETECTED entirely — no Token-scheme-specific reason
    and no generic entropy reason fires.
    """
    file_path = tmp_path / "fixture.yaml"
    file_path.write_text(
        f"authorization: '{scheme} {_ALL_LETTERS_BODY}'\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the new ``_TOKEN_SCHEME_RE`` does NOT match
#     natural-language text or code identifiers that mention "token" /
#     "Token" / "TOKEN" without a 36+-char token-shaped body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No 36+ contiguous chars from [A-Za-z0-9_\-] after the keyword.
        "We must configure the Token authentication for the legacy backend.",
        "Token is the recommended auth scheme for the GitHub legacy API.",
        "Disable the Token scheme on the public endpoint immediately.",
        # Whitespace inside the would-be token body breaks the regex.
        "token short tokens here only fine and not detected",
        # Punctuation immediately after "Token" breaks \s+ requirement.
        "token,xyz",
        "Token!",
        "Token.",
        "Token:",  # JSON-like "Token: value"
        # Common English passages mentioning Token as a noun.
        "The token expires after 24 hours and must be renewed.",
        "Audit the token usage logs for the past quarter.",
        # Code-shape false-positive candidates (method/identifier names) —
        # no whitespace between Token and the following body so no
        # match per the \s+ separator requirement.
        "function tokenHandler(req, res) {",
        "class TokenAuthenticatorImplementation extends AbstractAuth {",
        "def auto_token_rotation_handler(self, *, timeout=None):",
        "const subTokenStore = new SubTokenStore('memory');",
        # 35-char body — JUST below the 36-char floor.
        "token " + "A" * 35,
        # Body has dot (not in alphabet) — falls below 36 contiguous chars.
        "Token aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc",
        # Body has + (not in alphabet) — base64 padding ≠ Token-scheme alphabet.
        "Token aaaaaaaaaaaaaaaaaaaa+bbbbbbbbbbbbbbbbbbbb",
        # 36-char all-repetition body — fails uniqueness floor.
        "Token " + "a" * 36,
    ],
)
def test_secret_scanner_no_false_positives_on_natural_token_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_TOKEN_SCHEME_RE`` must NOT match
    natural-language sentences that mention "Token" without a
    36+-char token-shaped body following. The body alphabet
    ``[A-Za-z0-9_\\-]{36,}`` is the structural disambiguator —
    English sentences embed spaces and punctuation, so no 36+
    contiguous match is possible.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    token_findings = [f for f in findings if f.reason == TOKEN_SCHEME_REASON]
    assert not token_findings, (
        f"False-positive Token-scheme finding for natural-language "
        f"text {text!r}. The detector should require 36+ contiguous "
        f"chars from [A-Za-z0-9_\\-]. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Cross-detector ordering: Token-scheme detector must NOT cannibalise
#     existing detectors. A GitHub PAT (ghp_...) in a Token header must
#     continue to yield the GitHub Personal Access Token reason from
#     _KNOWN_TOKENS (which is processed BEFORE _AUTH_SCHEME_DETECTORS).
# ---------------------------------------------------------------------------


def test_token_scheme_does_not_steal_github_pat_attribution(
    tmp_path: Path,
) -> None:
    """A GitHub PAT (``ghp_<36>``) embedded after the legacy ``Token``
    auth-scheme literal must continue to yield the GitHub-specific
    reason (which comes from ``_KNOWN_TOKENS`` matching FIRST in
    ``_scan_content``), not the generic Token-scheme reason. The
    cross-detector ordering invariant pinned in ``_scan_content``
    (``_KNOWN_TOKENS`` first, then ``_AWS_ID_RE``, then
    ``_AUTH_SCHEME_DETECTORS``) preserves the more specific
    attribution.

    This is the canonical use case for GitHub's legacy REST API
    convention (``Authorization: token ghp_<PAT>``): the inner
    PAT-shape match wins, so the rotation playbook correctly anchors
    on github.com/settings/tokens rather than a generic
    Token-scheme attribution.
    """
    file_path = tmp_path / "github_legacy_curl.txt"
    file_path.write_text(
        f"Authorization: token {_GITHUB_PAT_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "GitHub Personal Access Token gefunden" in reasons, (
        f"Cross-detector boundary regression: GitHub PAT in Token "
        f"header lost its specific attribution after adding "
        f"Token-scheme detector. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )
    # The generic Token-scheme attribution must NOT also fire for the
    # same span — the more-specific GitHub PAT reason wins via
    # is_covered. (Note: the wider Token-scheme span includes the
    # leading "Token " literal, but its body group span equals the
    # ghp_ span, which is already covered.)
    token_scheme_findings = [
        f for f in findings if f.reason == TOKEN_SCHEME_REASON
    ]
    assert not token_scheme_findings, (
        f"Cross-detector ordering broken: Token-scheme detector also "
        f"fired for a span already covered by the GitHub PAT detector. "
        f"Got findings {findings!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Regression guards: canonical-case Bearer, Basic, Negotiate, and
#     NTLM detectors continue to fire correctly after adding Token. The
#     new detector must not interfere with existing detection paths.
# ---------------------------------------------------------------------------


def test_token_addition_does_not_break_bearer_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the Token-scheme addition. Regression
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
        f"Regression: Bearer detection broke after Token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_addition_does_not_break_basic_auth_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Basic <body>`` form continues to fire the
    Basic-Auth detector after the Token-scheme addition. Sibling
    regression guard within the ``_AUTH_SCHEME_DETECTORS`` table."""
    basic_body = "YWRtaW46cGFzc3dvcmQ="  # base64("admin:password")
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "Basic {basic_body}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HTTP Basic Authentication Credential gefunden" in reasons, (
        f"Regression: Basic Auth detection broke after Token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_addition_does_not_break_negotiate_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Negotiate <body>`` form continues to fire the
    SPNEGO/Negotiate detector after the Token-scheme addition.
    Sibling regression guard within the ``_AUTH_SCHEME_DETECTORS``
    table."""
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
        f"Regression: Negotiate detection broke after Token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_addition_does_not_break_ntlm_detection(tmp_path: Path) -> None:
    """The canonical ``NTLM <body>`` form continues to fire the NTLM
    detector after the Token-scheme addition. Sibling regression
    guard within the ``_AUTH_SCHEME_DETECTORS`` table — the FIFTH
    detector must not interfere with the FOURTH detector's matches."""
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
        f"Regression: NTLM detection broke after Token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Compiled-regex invariant: ``_TOKEN_SCHEME_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_token_scheme_re_flags_include_ignorecase() -> None:
    """The compiled ``_TOKEN_SCHEME_RE`` must carry the
    ``re.IGNORECASE`` flag so the auth-scheme literal is matched per
    the RFC 7235 §2.1 case-insensitive contract that every HTTP
    auth-scheme inherits (including the de-facto-but-not-IANA-
    registered ``Token`` scheme). A future regression that reverts
    to the case-sensitive shape fails this invariant immediately."""
    import re as _re

    from src.utils.secret_scanner import _TOKEN_SCHEME_RE

    assert _TOKEN_SCHEME_RE.flags & _re.IGNORECASE, (
        f"_TOKEN_SCHEME_RE flags={_TOKEN_SCHEME_RE.flags!r} missing "
        f"re.IGNORECASE. RFC 7235 §2.1 requires case-insensitive "
        f"matching on every HTTP auth-scheme literal (including "
        f"de-facto schemes like Token). "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Auth-scheme detector table membership invariant: the new
#     ``_TOKEN_SCHEME_RE`` must be wired into ``_AUTH_SCHEME_DETECTORS``
#     so the canonical ``_scan_auth_scheme_credentials`` helper processes
#     it uniformly. A future regression that adds the regex but forgets
#     the table entry fails this invariant immediately.
# ---------------------------------------------------------------------------


def test_token_scheme_re_membership_in_auth_scheme_detectors() -> None:
    """The compiled ``_TOKEN_SCHEME_RE`` must appear in
    ``_AUTH_SCHEME_DETECTORS`` so the canonical
    ``_scan_auth_scheme_credentials`` helper processes Token-scheme
    matches uniformly with the same ``is_assignment=True``
    ``_looks_like_secret`` filter and ``covered_ranges`` mutation
    contract that the Bearer / Basic / Negotiate / NTLM detectors
    already rely on.

    A future regression that adds the regex constant but forgets the
    tuple entry would silently bypass the auth-scheme processing
    path; this invariant fails the regression immediately."""
    from src.utils.secret_scanner import _AUTH_SCHEME_DETECTORS, _TOKEN_SCHEME_RE

    regexes_in_table = [regex for regex, _reason in _AUTH_SCHEME_DETECTORS]
    assert _TOKEN_SCHEME_RE in regexes_in_table, (
        f"_TOKEN_SCHEME_RE is not in _AUTH_SCHEME_DETECTORS. The "
        f"canonical _scan_auth_scheme_credentials helper iterates the "
        f"table; missing membership = silent regression. Table "
        f"regexes: {regexes_in_table!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )
    # Confirm the bound reason is the canonical one (not a leftover
    # placeholder or a typo'd duplicate of Bearer/Basic/Negotiate/NTLM).
    reasons_in_table = [reason for _regex, reason in _AUTH_SCHEME_DETECTORS]
    assert TOKEN_SCHEME_REASON in reasons_in_table, (
        f"Token-scheme reason {TOKEN_SCHEME_REASON!r} not in "
        f"_AUTH_SCHEME_DETECTORS reasons. Got: {reasons_in_table!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) End-to-end emission-shape inventory: every real-world
#     Authorization-header emission pattern that includes the ``Token``
#     literal must trigger Token-scheme attribution. Each emission
#     shape is documented in the round's threat model and corresponds
#     to a real-world IR-relevant log artefact source.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "emission_shape",
    [
        # curl -v debug output for GitHub REST API (most common
        # legacy emission per GitHub's documentation).
        f"> Authorization: token {_LONG_MIXED_BODY}",
        # Python requests with urllib3 DEBUG logging.
        f'send: b"GET /api/v3/user HTTP/1.1\\r\\nAuthorization: '
        f'Token {_LONG_MIXED_BODY}\\r\\nHost: api.github.com\\r\\n"',
        # Browser HAR export JSON for Token-scheme-authenticated
        # site (Rails / Devise app admin dashboard).
        f'{{"name": "Authorization", "value": "Token {_LONG_MIXED_BODY}"}}',
        # YAML config (Postman / Insomnia / Bruno export).
        f"headers:\n  Authorization: Token {_LONG_MIXED_BODY}",
        # Python API client docstring with hardcoded test fixture.
        f'"""Example: ``headers={{"Authorization": "Token '
        f'{_LONG_MIXED_BODY}"}}``."""',
        # CI/CD pipeline debug log (verbose curl in shell script).
        f"+ curl -H 'Authorization: Token {_LONG_MIXED_BODY}' "
        f"https://api.example.com/v1/account",
        # Kibana / Grafana / Datadog log view ingesting unredacted
        # application Authorization-header log.
        f'[2026-05-16T09:42:13Z] DEBUG: outbound request '
        f'Authorization="Token {_LONG_MIXED_BODY}"',
        # Ruby on Rails default Devise client config.
        f"DefaultHeaders = {{ 'Authorization' => 'Token "
        f"{_LONG_MIXED_BODY}' }}",
        # Go API client struct with hardcoded literal.
        f'req.Header.Set("Authorization", "Token {_LONG_MIXED_BODY}")',
    ],
)
def test_token_scheme_detected_across_emission_shapes(
    tmp_path: Path, emission_shape: str
) -> None:
    """Every real-world emission shape for a leaked Token-scheme
    credential must trigger Token-scheme-specific attribution.
    Documents the IR-relevant log artefact surfaces and pins the
    detector against future drift that would miss any of these
    canonical leak patterns.

    Emission shapes inventory matches the round's threat model:
    curl -v, requests/urllib3 DEBUG, browser HAR, YAML/JSON config,
    docstrings, CI/CD logs, log-management ingest, Ruby/Go API
    clients."""
    file_path = tmp_path / "emission_shape.txt"
    file_path.write_text(f"{emission_shape}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Emission shape did not yield Token-scheme attribution: "
        f"{emission_shape!r}; got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking-contract test: the raw opaque token body must NEVER
#     appear unmasked in the finding output (mirrors Bearer / Basic /
#     Negotiate / NTLM masking-contract tests).
# ---------------------------------------------------------------------------


def test_token_scheme_masking_contract(tmp_path: Path) -> None:
    """Token-scheme findings must mask the raw credential body before
    surfacing — the ``_mask_secret`` helper transforms the full body
    into the canonical ``xxxx***yyyy`` form so the CI logs / GitHub
    PR comment / pre-commit hook output never carry the unredacted
    plaintext credential.

    Regression guard against accidentally serialising the raw
    credential into Finding.match: a future refactor that bypasses
    the ``_mask_secret`` call in ``scan_repository`` would silently
    leak every detected credential into CI output."""
    file_path = tmp_path / "leak_artefact.txt"
    file_path.write_text(
        f"Authorization: Token {_LONG_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    # Token-scheme finding must exist.
    token_findings = [f for f in findings if f.reason == TOKEN_SCHEME_REASON]
    assert token_findings, (
        f"Expected at least one Token-scheme finding; got "
        f"{findings!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )

    # The raw body must NOT appear verbatim in any finding's match
    # field. Masking ensures only a redacted form (e.g., "AbCd***UvWx")
    # surfaces.
    for finding in findings:
        assert _LONG_MIXED_BODY not in finding.match, (
            f"Masking contract VIOLATED: raw credential body appears "
            f"in finding.match={finding.match!r}. The unredacted "
            f"credential must never reach CI output. "
            f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (10) Auth-scheme detector ordering invariant: the Token-scheme
#      detector lives AT THE END of ``_AUTH_SCHEME_DETECTORS`` (after
#      Bearer / Basic / Negotiate / NTLM). This is the canonical
#      Bearer-alias position — the more-specific IETF-RFC-defined
#      schemes match first, and the de-facto-but-not-IANA-registered
#      Token literal serves as the catch-all attribution carrier for
#      anything that still leaks via the ``Token`` literal. The
#      ordering pinned here prevents accidental table-rearrangement
#      from suppressing more-specific Bearer / Basic / Negotiate /
#      NTLM matches via the wider Token-scheme span.
# ---------------------------------------------------------------------------


def test_token_scheme_detector_table_position() -> None:
    """The Token-scheme detector must be the LAST entry in
    ``_AUTH_SCHEME_DETECTORS`` — the canonical Bearer-alias position.
    This invariant ensures that more-specific IETF-RFC-defined
    schemes (Bearer / Basic / Negotiate) and the high-impact
    vendor scheme (NTLM) match first within the auth-scheme table,
    and the de-facto Token literal serves as the catch-all for
    opaque Token-scheme credentials that no more-specific detector
    has claimed.

    A future regression that reshuffles the table such that
    Token-scheme matches BEFORE Bearer / Basic / Negotiate / NTLM
    would cause the wider Token-scheme span to suppress those
    more-specific schemes via covered_ranges; this invariant fails
    that regression immediately."""
    from src.utils.secret_scanner import _AUTH_SCHEME_DETECTORS, _TOKEN_SCHEME_RE

    last_regex, last_reason = _AUTH_SCHEME_DETECTORS[-1]
    assert last_regex is _TOKEN_SCHEME_RE, (
        f"Token-scheme detector is not the last entry in "
        f"_AUTH_SCHEME_DETECTORS. Position invariant broken — "
        f"more-specific schemes (Bearer / Basic / Negotiate / NTLM) "
        f"may be suppressed by the wider Token-scheme span. Last "
        f"regex: {last_regex!r}, last reason: {last_reason!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )
