"""Sentinel PoC: missing ``_TOKEN_SCHEME_RE`` detector — the GitHub-style
``Authorization: token <body>`` HTTP authentication scheme (de facto
alias for Bearer popularised by the GitHub REST API and inherited by
Gitea / Forgejo / DigitalOcean / various self-hosted Git hosts) slips
past the secret scanner with attribution drift OR silent undetection,
depending on the body shape.

This round closes the **named-but-deferred** first-priority
adjacent-detector candidate from the 2026-05-16 NTLM detector drift
round (sibling PR ``security(secret-scanner): add NTLM detector per
[MS-NLMP]``). That round explicitly named the GitHub ``Token`` scheme
detector as the first next-round target::

    **Next-round candidates (named-but-deferred):** **GitHub ``Token``
    scheme detector** (``(?i)token\\s+[A-Za-z0-9_\\-]{36,}``) —
    GitHub's older API alias for Bearer (``Authorization: token
    ghp_...``); the embedded GitHub PAT (``ghp_``-prefixed) already
    matches ``_KNOWN_TOKENS`` via the ``ghp_`` regex, so the ``token``
    scheme detector is attribution-only for opaque non-GitHub tokens
    that happen to use the alias.

The ``token`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits (even though
``token`` is not a registered IANA HTTP Authentication Scheme — it is a
de facto convention popularised by GitHub's REST API documentation and
inherited by every Git-host fork that mirrors GitHub's HTTP API shape:
Gitea, Forgejo, Codeberg, Gogs, sourcehut's mirror endpoints, plus
DigitalOcean's older API v1 docs and various other tools). ``token
<body>`` / ``TOKEN <body>`` / ``Token <body>`` / ``ToKeN <body>`` are
all legitimate canonical HTTP Authorization-header shapes on the wire.

Threat model
------------

A leaked ``Authorization: token <body>`` (where the body is an
opaque API token — typically a 36-char alphanumeric GitHub PAT body or
a 40-char hex legacy GitHub token from before April 2021, but also any
opaque token from Gitea/Forgejo/DigitalOcean/other ``token``-scheme
issuers) in committed source / log artefacts / CI debug snippets /
hostile-PR fragments fails the existing detection branches in two
distinct ways:

1. **Attribution drift (common case)** — opaque API tokens with mixed
   character classes (the typical 36+ char alphanumeric+hyphen body)
   DO match ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically
   and land as ``Hochentropischer Token-String`` findings. The
   token-scheme-specific attribution is lost — incident-response
   triage must guess whether the leaked entropy span is a GitHub PAT
   (rotate at github.com/settings/tokens, audit
   ``GET /user`` API calls for the token's usage history), a Gitea
   token (rotate at ``/user/settings/applications``, audit Gitea's
   ``OAuth2 Applications`` audit log), a DigitalOcean token (rotate at
   cloud.digitalocean.com/account/api/tokens, audit Spaces / Droplet /
   Kubernetes API calls), or some other opaque secret.

2. **Silent undetection (uniform-character-class body)** — the
   entropy fallback's ``candidate.isalpha()`` short-circuit (added to
   suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. Such bodies arise
   from custom token generators that emit alphabetical-only IDs, hash-
   encoded tokens that happen to land in the letter-only sub-alphabet,
   or short test fixtures. Pre-fix every leaked ``token
   <all-letter-body>`` is **silently undetected** entirely.

Note on attribution overlap with ``_KNOWN_TOKENS``: a leaked
``Authorization: token ghp_xxxx...`` carries TWO matchable spans —
the ``ghp_<36>`` GitHub PAT span (matched by ``_KNOWN_TOKENS``) and
the ``token <body>`` HTTP auth-scheme span (matched by this new
detector). The ``_KNOWN_TOKENS`` matcher runs FIRST in
``_scan_content``, so the GitHub-PAT-specific reason wins via the
``covered_ranges`` arbitration — the new token-scheme detector
yields its attribution only for tokens that do NOT match any
``_KNOWN_TOKENS`` prefix (opaque non-prefixed tokens). This mirrors
the cross-detector boundary established by the Bearer / Basic /
Negotiate / NTLM rounds: more-specific issuer attribution always
wins.

Real-world emission patterns
----------------------------

- GitHub REST API examples in legacy curl scripts:
  ``curl -H "Authorization: token ghp_xxx" https://api.github.com/user``
- ``hub`` CLI configuration files (``~/.config/hub``) emitting
  ``oauth_token: <body>`` in YAML — the underlying transport uses the
  ``token`` HTTP scheme.
- Gitea / Forgejo / Codeberg / Gogs API examples mirroring the GitHub
  shape: ``curl -H "Authorization: token <gitea-pat>"``.
- DigitalOcean API v1 docs (legacy):
  ``curl -H "Authorization: token <do-token>" https://api.digitalocean.com``.
- CI/CD workflow files with hard-coded fallback tokens:
  ``- run: curl -H "Authorization: token $TOKEN" ...``.
- Python ``requests`` debug logs (``logging.DEBUG`` on
  ``urllib3.connectionpool``) emitting the full Authorization header.
- Browser dev-tools Network tab exports (HAR files) capturing
  intranet self-hosted Git host API calls.
- Documentation snippets in READMEs and wikis copy-pasting curl
  examples with real (live!) tokens left in.

Severity
--------

**MEDIUM-HIGH** — opaque API token credentials grant authenticated
access to the issuing service. The blast radius depends on the token's
permission scope: GitHub PATs can range from read-only ``public_repo``
to full ``repo + admin:org + admin:gpg_key + admin:public_key + ...``
(every operation the user can perform via the API including private
repo access, secret extraction, branch protection bypass, workflow
modification leading to RCE in CI). Gitea/Forgejo tokens have similar
scope ranges. DigitalOcean tokens grant full account access by
default. Mitigated only by the requirement that the leaked credential
live alongside a ``token`` auth-scheme literal in the same content
blob; in practice this covers every leak through legacy curl examples,
CI workflow templates, HAR exports of API-using intranet apps,
``requests`` debug output, and copy-pasted documentation.

Fix
---

Add a ``token`` auth-scheme detector mirroring the existing
auth-scheme detectors' case-insensitive contract::

    _TOKEN_SCHEME_RE = re.compile(r"(?i)token\\s+([A-Za-z0-9_\\-]{36,})")

Append to ``_AUTH_SCHEME_DETECTORS`` LAST (after Bearer / Basic /
Negotiate / NTLM) so the existing ``_scan_auth_scheme_credentials``
helper processes matches uniformly with the same
``is_assignment=True`` ``_looks_like_secret`` filter that the existing
detectors use. The placement order matters for the cross-detector
boundary: more-specific schemes (Bearer / Basic / Negotiate / NTLM)
must run FIRST so their attribution wins via ``covered_ranges``
arbitration when a token-shaped body sits inside a Bearer / Basic /
etc. header.

The 36+ char body floor is the structural disambiguator against
natural-language false positives — the word ``token`` is common in
English prose and code comments, but is essentially never followed by
36+ contiguous chars from the ``[A-Za-z0-9_\\-]`` alphabet in natural
text (English words break at whitespace and punctuation). The floor
is calibrated to the GitHub PAT body length (``ghp_<36>`` = 40 total
chars, with the 36-char body part captured by the detector when the
PAT prefix is absent — the legacy 40-char hex GitHub token from
before April 2021 also exceeds the 36-char floor). The body alphabet
``[A-Za-z0-9_\\-]`` covers GitHub's PAT alphabet, Gitea/Forgejo's PAT
alphabet (both inherit GitHub's URL-safe alphanumeric+hyphen+underscore
shape per their REST API specs), and DigitalOcean's hex token shape.
The downstream ``_looks_like_secret(candidate, is_assignment=True)``
heuristic (``min_categories=1`` in the auth-scheme path) provides the
second-layer filter for any token-shaped string that does happen to
follow a ``token``-prefixed natural-language passage.

Marker: SENTINEL_TOKEN_SCHEME_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_TOKEN_SCHEME_DRIFT = "http token-scheme attribution + silent-undetection drift"

TOKEN_SCHEME_REASON = "HTTP Token-Scheme Authentication Credential gefunden"


# Realistic token bodies of varying lengths and shapes:
#
#  * ``_OPAQUE_MIXED_BODY`` — 56-char mixed-class alphanumeric body
#    that does NOT match any ``_KNOWN_TOKENS`` prefix. Mimics a
#    self-hosted Gitea / Forgejo PAT or a generic opaque token from a
#    non-prefixed third-party API. Used for the attribution-drift
#    PoCs (without the fix, lands as generic ``Hochentropischer
#    Token-String``).
#  * ``_ALL_LETTERS_BODY`` — 40-char all-letter body. Trips the
#    ``candidate.isalpha()`` skip in the entropy fallback so SILENTLY
#    UNDETECTED pre-fix. Used for the silent-undetection PoC.
#  * ``_LEGACY_HEX_BODY`` — 40-char hex token mimicking the legacy
#    GitHub token shape from before April 2021 (when GitHub introduced
#    the ``ghp_`` / ``gho_`` / ``ghu_`` / ``ghs_`` / ``ghr_`` prefix
#    family). Legacy tokens are still in circulation on long-running
#    services. Used to prove backward-compatible detection.

_OPAQUE_MIXED_BODY = "aB3xY7nQ9pK2vL5wM8rZ4sT6uV1iC0eF8gH2jD4kP9oN7q"  # 46 mixed-class
assert len(_OPAQUE_MIXED_BODY) >= 36
assert any(c.isupper() for c in _OPAQUE_MIXED_BODY)
assert any(c.islower() for c in _OPAQUE_MIXED_BODY)
assert any(c.isdigit() for c in _OPAQUE_MIXED_BODY)
assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in _OPAQUE_MIXED_BODY)
# Must NOT match _KNOWN_TOKENS prefixes (gh*_, glpat-, AIza, sk_*, etc.).
assert not _OPAQUE_MIXED_BODY.startswith(("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "glpat-", "AIza", "sk_", "rk_"))

# All-letter body — trips the entropy fallback's ``candidate.isalpha()``
# skip. Must be >= 36 chars to satisfy the token-scheme detector's
# body-length floor.
_ALL_LETTERS_BODY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"  # 40 chars
assert _ALL_LETTERS_BODY.isalpha()
assert len(_ALL_LETTERS_BODY) >= 36

# Legacy 40-char hex GitHub token shape (pre-April-2021). Hex tokens
# mix two character classes (lowercase + digits) so the entropy
# fallback would catch them generically, but the token-scheme-specific
# attribution is lost without this detector.
_LEGACY_HEX_BODY = "abcdef0123456789abcdef0123456789abcdef01"  # 40 hex chars
assert len(_LEGACY_HEX_BODY) >= 36
assert all(c in "0123456789abcdef" for c in _LEGACY_HEX_BODY)


# ---------------------------------------------------------------------------
# (1) Attribution-drift PoCs: every case variation of the token literal,
#     with a body that the entropy fallback DOES match, must yield the
#     token-scheme-specific reason (not the generic entropy reason).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "token",   # all-lowercase (GitHub docs canonical case)
        "TOKEN",   # all-uppercase
        "Token",   # title-case (per RFC 7235 §2.1 case-insensitive contract)
        "ToKeN",   # mixed-case (hostile-PR-style obfuscation)
        "tOkEn",   # mixed-case alternate
    ],
)
def test_secret_scanner_detects_token_scheme_case_insensitive_opaque(
    tmp_path: Path, scheme: str
) -> None:
    """Every case variation of the ``token`` auth-scheme literal must be
    detected with the token-scheme-specific attribution, per RFC 7235
    §2.1's case-insensitive auth-scheme contract.

    The 46-char opaque mixed-class body exercises the attribution-drift
    branch: pre-fix the entropy fallback caught the body span
    generically (as "Hochentropischer Token-String"), but the
    token-scheme-specific reason that pinpoints the issuer-family
    rotation flow (GitHub / Gitea / Forgejo / DigitalOcean / etc.) was
    lost.
    """
    file_path = tmp_path / "github_api_log.txt"
    file_path.write_text(
        f"Authorization: {scheme} {_OPAQUE_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Token-scheme detector did not produce its attribution for case "
        f"{scheme!r}; got reasons {reasons!r}. "
        f"RFC 7235 §2.1 says auth-scheme is case-insensitive; the "
        f"leaked credential must yield the token-scheme-specific reason "
        f"regardless of case. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )
    # Confirm raw secret never appears in findings (redaction contract).
    assert _OPAQUE_MIXED_BODY not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Silent-undetection PoCs: all-letter bodies trip the
#     ``candidate.isalpha()`` skip in the entropy fallback. The
#     token-scheme detector closes this hole.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scheme",
    [
        "token",
        "TOKEN",
        "Token",
        "ToKeN",
    ],
)
def test_secret_scanner_detects_token_scheme_all_letters_body(
    tmp_path: Path, scheme: str
) -> None:
    """All-letter bodies trip the ``candidate.isalpha()`` skip in
    ``_HIGH_ENTROPY_RE``'s loop (which exists to suppress false
    positives on LongCamelCaseClassNames). The token-scheme detector
    catches these via the ``is_assignment=True`` path of
    ``_looks_like_secret`` which allows ``min_categories=1``.

    PoC body: 40-char all-letter token body. The pre-fix scanner is
    SILENTLY UNDETECTED entirely — no token-scheme-specific reason and
    no generic entropy reason fires.
    """
    file_path = tmp_path / "fixture.yaml"
    file_path.write_text(
        f"authorization: '{scheme} {_ALL_LETTERS_BODY}'\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"SILENT UNDETECTION via isalpha() skip: scheme={scheme!r}, "
        f"body={_ALL_LETTERS_BODY!r} (all letters); got reasons "
        f"{reasons!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Legacy-hex token PoC: the pre-April-2021 GitHub token shape
#     (40-char hex string with no prefix) must be detected with the
#     token-scheme-specific attribution when wrapped in a ``token``
#     auth-scheme literal.
# ---------------------------------------------------------------------------


def test_secret_scanner_detects_token_scheme_legacy_hex_body(
    tmp_path: Path,
) -> None:
    """The legacy 40-char hex GitHub token shape (still in circulation
    on long-running services) must yield the token-scheme attribution
    when wrapped in a ``token`` auth-scheme literal. Without this
    detector, the body matches the generic entropy fallback as
    ``Hochentropischer Token-String``, losing the token-scheme-specific
    rotation flow (GitHub auth-token rotation flow at
    github.com/settings/tokens).
    """
    file_path = tmp_path / "legacy_gh_curl.sh"
    file_path.write_text(
        f'curl -H "Authorization: token {_LEGACY_HEX_BODY}" '
        f"https://api.github.com/user\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Legacy 40-char hex GitHub token wrapped in ``token`` scheme "
        f"not detected with token-scheme attribution. Got reasons "
        f"{reasons!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Negative cases: ensure the new ``_TOKEN_SCHEME_RE`` does NOT
#     match natural-language text or code identifiers that mention
#     "token" / "Token" / "TOKEN" without a 36+-char token-shaped body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No 36+ contiguous chars from [A-Za-z0-9_-] after the keyword.
        "We must rotate the token every 90 days for compliance.",
        "Token expiration is configured in the IdP settings panel.",
        "Disable the token if you suspect it has been compromised.",
        # Whitespace inside the would-be token body breaks the regex.
        "token foo bar baz qux quux 123456789012345",
        # Punctuation immediately after "token" breaks \s+ requirement.
        "token,foo",
        "token=foo",  # this would be caught by _SENSITIVE_ASSIGN_RE, not _TOKEN_SCHEME_RE
        "token.foo",
        "token!",
        "token.",
        "token:",
        # Common English passages mentioning "token" as a word.
        "The token is stored in the keychain for future API calls.",
        "Configure your access token in the settings dialog.",
        "A token-based authentication scheme is preferred over basic auth.",
        # Code-shape false-positive candidates (method/identifier names) —
        # no whitespace between "token" and the following body so no
        # match per the \s+ separator requirement.
        "function tokenizeRequestHeaderForOAuthRefresh(req, res) {",
        "class TokenAuthenticatorImplementation extends AbstractAuth {",
        "def parse_token_field_from_request_header(self, request):",
        # 35-char body — JUST below the 36-char floor.
        "token " + "A" * 35,
    ],
)
def test_secret_scanner_no_false_positives_on_natural_token_text(
    tmp_path: Path, text: str
) -> None:
    """The case-insensitive ``_TOKEN_SCHEME_RE`` must NOT match
    natural-language sentences that mention "token" without a 36+ char
    token-shaped body following. The body alphabet
    ``[A-Za-z0-9_\\-]{36,}`` is the structural disambiguator — English
    sentences embed spaces and punctuation, so no 36+ contiguous match
    is possible.
    """
    file_path = tmp_path / "natural_text.md"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    token_scheme_findings = [f for f in findings if f.reason == TOKEN_SCHEME_REASON]
    assert not token_scheme_findings, (
        f"False-positive token-scheme finding for natural-language text "
        f"{text!r}. The detector should require 36+ contiguous chars "
        f"from the alphanumeric+hyphen+underscore alphabet. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Cross-detector boundary: token-scheme detector must NOT
#     cannibalise the GitHub PAT attribution. A leaked ``token
#     ghp_xxx...`` carries TWO matchable spans — the ``ghp_<36>``
#     GitHub PAT span (matched by ``_KNOWN_TOKENS``, runs FIRST) and
#     the ``token <body>`` HTTP auth-scheme span (matched by the new
#     detector). The GitHub-PAT-specific reason MUST win via
#     ``covered_ranges`` arbitration since ``_KNOWN_TOKENS`` runs
#     before ``_AUTH_SCHEME_DETECTORS`` in ``_scan_content``.
# ---------------------------------------------------------------------------


def test_token_scheme_does_not_steal_github_pat_attribution(
    tmp_path: Path,
) -> None:
    """A GitHub PAT (``ghp_<36 chars>``) embedded after a ``token``
    auth-scheme literal must continue to yield the GitHub-PAT-specific
    reason (which comes from ``_KNOWN_TOKENS`` matching FIRST in
    ``_scan_content``), not the token-scheme-specific reason. The
    cross-detector ordering invariant pinned in ``_scan_content``
    (``_KNOWN_TOKENS`` first, then ``_AWS_ID_RE``, then
    ``_AUTH_SCHEME_DETECTORS``) preserves the more specific issuer
    attribution.
    """
    ghp_body = "ghp_" + "A" * 36
    file_path = tmp_path / "github_curl.sh"
    file_path.write_text(
        f'curl -H "Authorization: token {ghp_body}" '
        f"https://api.github.com/user\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "GitHub Personal Access Token gefunden" in reasons, (
        f"Cross-detector boundary regression: GitHub PAT in ``token`` "
        f"scheme header lost its specific attribution after adding "
        f"token-scheme detector. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_scheme_does_not_steal_jwt_attribution(tmp_path: Path) -> None:
    """A JWT (``eyJ...``) embedded after a ``token`` auth-scheme literal
    must continue to yield the JWT-specific reason (which comes from
    ``_KNOWN_TOKENS`` matching FIRST in ``_scan_content``), not the
    token-scheme-specific reason. The JWT detector's
    three-segment-with-dots regex catches the canonical JWT shape
    regardless of the surrounding auth-scheme literal.
    """
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    file_path = tmp_path / "jwt_in_token_scheme.txt"
    # Note: JWT contains "." which is NOT in [A-Za-z0-9_\-], so the
    # _TOKEN_SCHEME_RE regex itself stops at the first ".", capturing
    # only the first segment. But the JWT detector in _KNOWN_TOKENS
    # matches the whole eyJ...JWT regardless and wins via covered_ranges.
    file_path.write_text(f"Authorization: token {jwt}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert "JSON Web Token (JWT) gefunden" in reasons, (
        f"Cross-detector boundary regression: JWT in ``token`` scheme "
        f"header lost its JWT-specific attribution after adding the "
        f"token-scheme detector. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Regression guards: canonical-case Bearer, Basic, Negotiate, and
#     NTLM detectors continue to fire correctly after adding the
#     token-scheme detector. The new detector must not interfere with
#     existing detection paths.
# ---------------------------------------------------------------------------


def test_token_scheme_addition_does_not_break_bearer_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Bearer <body>`` form continues to fire the
    Bearer-Token detector after the token-scheme addition. Regression
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
        f"Regression: Bearer detection broke after token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_scheme_addition_does_not_break_basic_auth_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Basic <body>`` form continues to fire the
    Basic-Auth detector after the token-scheme addition. Sibling
    regression guard within the ``_AUTH_SCHEME_DETECTORS`` table."""
    basic_body = "YWRtaW46cGFzc3dvcmQ="  # base64("admin:password")
    file_path = tmp_path / "fixture.json"
    file_path.write_text(
        f'{{"authorization": "Basic {basic_body}"}}\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "HTTP Basic Authentication Credential gefunden" in reasons, (
        f"Regression: Basic Auth detection broke after token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_scheme_addition_does_not_break_negotiate_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``Negotiate <body>`` form continues to fire the
    SPNEGO/Negotiate detector after the token-scheme addition. Sibling
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
        f"Regression: Negotiate detection broke after token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_scheme_addition_does_not_break_ntlm_detection(
    tmp_path: Path,
) -> None:
    """The canonical ``NTLM <body>`` form continues to fire the NTLM
    detector after the token-scheme addition. Sibling regression guard
    within the ``_AUTH_SCHEME_DETECTORS`` table.

    Body mirrors the realistic NTLMSSP Type 3 mixed-class body from
    the 2026-05-16 NTLM round (digits + upper + lower + ``+/=`` so the
    entropy uniqueness floor is satisfied).
    """
    ntlm_body = (
        "TlRMTVNTUAADAAAAGAAYAHIAAAAYABgAigAAABQAFABIAAAADAAMAFwAAAASABIA"
        "aAAAABAAEACiAAAAVQBzAGUAcgBOAGEAbQBlAEQAbwBtAGEAaQBuAFcAbwByAGsA"
        "cwB0AGEAdABpAG8AbgBOAEEEEDQGN6JhKlJgwpV1nMxn/wEBAAAAAAAAtMW5sAm5"
        "0gEgZkBhcDkyNgAAAAACAAgAcgBlAGEAbABtAAAAAAAAAAAAAAA="
    )
    file_path = tmp_path / "ntlm_capture.txt"
    file_path.write_text(
        f"Authorization: NTLM {ntlm_body}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "NTLM Authentication Credential gefunden" in reasons, (
        f"Regression: NTLM detection broke after token-scheme "
        f"addition. Got reasons {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Compiled-regex invariant: ``_TOKEN_SCHEME_RE`` flags include
#     ``re.IGNORECASE`` (programmatic pin against future drift back to
#     case-sensitive shape).
# ---------------------------------------------------------------------------


def test_token_scheme_re_flags_include_ignorecase() -> None:
    """The compiled ``_TOKEN_SCHEME_RE`` must carry the
    ``re.IGNORECASE`` flag so the auth-scheme literal is matched per the
    RFC 7235 §2.1 case-insensitive contract that every HTTP auth-scheme
    inherits (even non-IANA-registered de facto schemes like GitHub's
    ``token``). A future regression that reverts to the case-sensitive
    shape fails this invariant immediately."""
    import re as _re

    from src.utils.secret_scanner import _TOKEN_SCHEME_RE

    assert _TOKEN_SCHEME_RE.flags & _re.IGNORECASE, (
        f"_TOKEN_SCHEME_RE flags={_TOKEN_SCHEME_RE.flags!r} missing "
        f"re.IGNORECASE. RFC 7235 §2.1 requires case-insensitive "
        f"matching on every HTTP auth-scheme literal (including de "
        f"facto schemes like GitHub's ``token``). "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (8) Auth-scheme detector table membership invariant: the new
#     ``_TOKEN_SCHEME_RE`` must be wired into ``_AUTH_SCHEME_DETECTORS``
#     so the canonical ``_scan_auth_scheme_credentials`` helper
#     processes it uniformly. A future regression that adds the regex
#     but forgets the table entry fails this invariant immediately.
# ---------------------------------------------------------------------------


def test_token_scheme_re_is_in_auth_scheme_detectors_table() -> None:
    """The structural invariant from the 2026-05-16 Basic Auth /
    Negotiate / NTLM rounds: every HTTP auth-scheme detector MUST be a
    member of ``_AUTH_SCHEME_DETECTORS`` so the canonical processing
    path in ``_scan_auth_scheme_credentials`` applies uniformly (same
    ``_looks_like_secret`` filter, same coverage-range arbitration). A
    regression that adds ``_TOKEN_SCHEME_RE`` but forgets the table
    binding ships the dead regex and the detection gap stays open."""
    from src.utils.secret_scanner import (
        _AUTH_SCHEME_DETECTORS,
        _TOKEN_SCHEME_RE,
    )

    detector_regexes = [regex for regex, _ in _AUTH_SCHEME_DETECTORS]
    assert _TOKEN_SCHEME_RE in detector_regexes, (
        f"_TOKEN_SCHEME_RE is not bound in _AUTH_SCHEME_DETECTORS. Per "
        f"the structural invariant, every auth-scheme regex MUST be a "
        f"tuple entry. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )

    detector_reasons = [reason for _, reason in _AUTH_SCHEME_DETECTORS]
    assert TOKEN_SCHEME_REASON in detector_reasons, (
        f"Expected reason {TOKEN_SCHEME_REASON!r} not found in "
        f"_AUTH_SCHEME_DETECTORS. Reasons present: "
        f"{detector_reasons!r}. ({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


def test_token_scheme_re_runs_after_more_specific_schemes() -> None:
    """The ``_TOKEN_SCHEME_RE`` must appear AFTER the more-specific
    auth-scheme detectors in ``_AUTH_SCHEME_DETECTORS`` so the
    ``covered_ranges`` arbitration in ``_scan_auth_scheme_credentials``
    preserves the more-specific attribution when a token-shaped body
    sits inside a Bearer / Basic / Negotiate / NTLM header. This is the
    structural ordering invariant for the cross-detector boundary."""
    from src.utils.secret_scanner import (
        _AUTH_SCHEME_DETECTORS,
        _BASIC_AUTH_RE,
        _BEARER_RE,
        _NEGOTIATE_RE,
        _NTLM_RE,
        _TOKEN_SCHEME_RE,
    )

    detector_regexes = [regex for regex, _ in _AUTH_SCHEME_DETECTORS]
    token_idx = detector_regexes.index(_TOKEN_SCHEME_RE)
    for more_specific in (_BEARER_RE, _BASIC_AUTH_RE, _NEGOTIATE_RE, _NTLM_RE):
        more_specific_idx = detector_regexes.index(more_specific)
        assert more_specific_idx < token_idx, (
            f"Ordering invariant violation: {more_specific.pattern!r} "
            f"(more-specific scheme) appears at index {more_specific_idx} "
            f"but _TOKEN_SCHEME_RE appears at index {token_idx}. The "
            f"generic ``token`` scheme must come LAST so more-specific "
            f"schemes win via covered_ranges arbitration. "
            f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
        )


# ---------------------------------------------------------------------------
# (9) End-to-end emission-shape inventory: real-world GitHub /
#     Gitea / Forgejo / DigitalOcean / hub-cli emission shapes — the
#     case-insensitive fix must cover the common header-emission
#     shapes wholesale.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_header,case_label",
    [
        # curl -v debug log shapes (GitHub REST API canonical)
        (
            f"> Authorization: token {_OPAQUE_MIXED_BODY}",
            "lowercase-curl-debug",
        ),
        (
            f"< Authorization: TOKEN {_OPAQUE_MIXED_BODY}",
            "uppercase-server-debug",
        ),
        (
            f"Authorization: Token {_OPAQUE_MIXED_BODY}",
            "title-case-fixture",
        ),
        # Browser HAR export shape (JSON nested header structure)
        (
            f'{{"name": "Authorization", "value": "token {_OPAQUE_MIXED_BODY}"}}',
            "har-export-lowercase",
        ),
        # Python-dict / JSON fixture shape (requests test harness)
        (
            f'{{"Authorization": "token {_OPAQUE_MIXED_BODY}"}}',
            "json-canonical-case",
        ),
        # YAML fixture shape (Ansible / Kubernetes / GitHub Actions)
        (
            f"authorization: 'token {_OPAQUE_MIXED_BODY}'",
            "yaml-lowercase",
        ),
        # GitHub Actions workflow / .gitconfig debug shape
        (
            f"        Authorization: token {_OPAQUE_MIXED_BODY}",
            "indented-actions-log",
        ),
        # urllib3.connectionpool DEBUG log shape
        (
            f"DEBUG urllib3.connectionpool: header: Authorization: token {_OPAQUE_MIXED_BODY}",
            "urllib3-debug",
        ),
        # Gitea / Forgejo API example
        (
            f"curl -H 'Authorization: token {_OPAQUE_MIXED_BODY}' https://gitea.example.com/api/v1/repos",
            "gitea-curl-example",
        ),
        # DigitalOcean legacy API v1 example
        (
            f"curl -X GET -H 'Authorization: token {_OPAQUE_MIXED_BODY}' https://api.digitalocean.com/droplets",
            "digitalocean-v1-example",
        ),
    ],
)
def test_secret_scanner_detects_token_scheme_across_emission_shapes(
    tmp_path: Path, raw_header: str, case_label: str
) -> None:
    """Real-world ``token`` auth-scheme emission shapes (curl debug
    logs, browser HAR exports, JSON/YAML fixtures, urllib3 debug
    traces, GitHub Actions logs, Gitea/Forgejo/DigitalOcean curl
    examples) all canonicalise the case differently depending on the
    emitter; the detector must cover them wholesale."""
    file_path = tmp_path / f"fixture_{case_label}.txt"
    file_path.write_text(f"{raw_header}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert TOKEN_SCHEME_REASON in reasons, (
        f"Emission shape {case_label!r} ({raw_header!r}) did not "
        f"produce the token-scheme-specific reason; got {reasons!r}. "
        f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (10) Masking contract: the secret value never appears in the
#      findings' ``match`` field unmasked. This mirrors the Bearer /
#      Basic Auth / Negotiate / NTLM detectors' masking-contract
#      tests.
# ---------------------------------------------------------------------------


def test_token_scheme_masking_contract(tmp_path: Path) -> None:
    """The raw opaque API token body must NOT appear unmasked in any
    finding's ``match`` field. Leaving it unmasked in findings (which
    surface in CI logs / PR comments / GitHub Issue bodies) would
    re-leak the credential at the very point of detection.
    """
    file_path = tmp_path / "fixture.txt"
    file_path.write_text(
        f"Authorization: token {_OPAQUE_MIXED_BODY}\n", encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    token_findings = [f for f in findings if f.reason == TOKEN_SCHEME_REASON]
    assert token_findings, "Test setup failure: expected a token-scheme finding."

    for finding in token_findings:
        assert _OPAQUE_MIXED_BODY not in finding.match, (
            f"Masking-contract violation: raw token body "
            f"{_OPAQUE_MIXED_BODY!r} appears unmasked in finding "
            f"{finding!r}. The opaque API token grants authenticated "
            f"access to the issuing service; emitting it unmasked "
            f"re-leaks the credential at the detection boundary. "
            f"({SENTINEL_TOKEN_SCHEME_DRIFT})"
        )
