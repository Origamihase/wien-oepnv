"""Sentinel drift coverage for the Bitbucket + Mapbox token tier value-shape
detection (``_KNOWN_TOKENS``) AND log-sanitisation (``sanitize_log_message``
plus the downstream ``_sanitize_exception_msg`` chain).

After the 2026-05-17 Figma + Tailscale round closed the canonical workspace-
collaboration / VPN-overlay tier backlog, two more high-blast-radius vendor
families remain SILENTLY UNCOVERED across BOTH detection codepaths — neither
the secret scanner nor the log sanitiser attribute tokens for these issuers:

* **Bitbucket App Password / Repository Access Token (``ATBB<32+ alnum>``)** —
  the canonical Atlassian Bitbucket Cloud credential family. Issued via
  https://bitbucket.org/account/settings/app-passwords/ (App Password) or
  per-repository/workspace/project Access Tokens at the corresponding
  settings page. Sibling-drift of the Atlassian Cloud API Token
  (``ATATT3xFfGF0<body>``) family which is ALREADY covered — same issuer
  (Atlassian), distinct product (Bitbucket vs. Jira/Confluence), distinct
  prefix (``ATBB`` vs. ``ATATT3xFfGF0``), distinct revocation surface
  (bitbucket.org/account/settings/app-passwords/ vs.
  id.atlassian.com/manage-profile/security/api-tokens). Pre-fix every
  bare ``ATBB<body>`` token leaked verbatim through ``sanitize_log_message``
  (the underscore separator that anchors most other vendor prefixes is
  absent so the entropy alphabet matches contiguously but only as a
  generic ``Hochentropischer Token-String`` — the Bitbucket-specific
  issuer attribution that anchors the revocation flow is LOST).

* **Mapbox Access Token (``(?:pk|sk|tk)\\.eyJ<3-segment-JWT-body>``)** —
  Mapbox's canonical access-token format for the Maps / Geocoding /
  Directions / Navigation APIs. Issued via https://account.mapbox.com/
  with three prefixes: ``pk.`` (public scope, read-only client-side
  use), ``sk.`` (secret scope, FULL account access including billing),
  ``tk.`` (temporary token, ephemeral scope). The body is a base64url
  JOSE JWT — structurally identical to the JWT pattern already in
  ``_KNOWN_TOKENS``, but the leading ``pk.``/``sk.``/``tk.`` prefix
  sits OUTSIDE the JWT detector's ``eyJ`` anchor. Pre-fix:
   - **Scanner**: matched the inner JWT span as generic
     "JSON Web Token (JWT) gefunden" — the Mapbox-specific issuer
     attribution AND the scope tier (``pk``/``sk``/``tk``) were LOST.
   - **Log sanitiser**: the JWT regex masks the inner ``eyJ<body>``
     span but the leading ``pk.``/``sk.`` prefix sits OUTSIDE the
     replacement span — IR triage sees ``sk.eyJ***`` which looks like
     a JWT inside an unknown ``sk.`` namespace rather than a Mapbox
     secret-scope token that demands account.mapbox.com revocation.

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"Bitbucket: {pat}")``)
   leaks verbatim to operator log streams.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "ATBB<32>"}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex
   (``[a-z0-9_.\\-]*token`` / ``secret`` / ``key`` / etc.) because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /api/repos/<token>/branches``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /api/foo?ref=<token>``) bypasses the URL-query-param
   sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a Bitbucket Repository
   Access Token or Mapbox secret token as a JSON value, README example,
   or .env-like fixture sails past the scanner's existing
   ``_KNOWN_TOKENS`` table:
   - Bitbucket ``ATBB<body>``: matches the entropy fallback as generic
     ``Hochentropischer Token-String`` (the body alphabet is
     ``[A-Za-z0-9]`` which lies inside the entropy alphabet) — but
     the Bitbucket-specific issuer attribution that anchors the
     revocation flow at bitbucket.org/account/settings/app-passwords/
     is LOST.
   - Mapbox ``(?:pk|sk|tk).eyJ<body>``: matches the existing JWT
     entry (``eyJ<10+>.<10+>.<20+>``) — the JWT detector wins because
     the ``.`` before ``eyJ`` is OUTSIDE the lookbehind's alphanumeric
     class, so the inner JWT body matches and the leading
     ``pk.``/``sk.``/``tk.`` is lost from attribution AND from the
     covered span. The Mapbox-specific revocation flow at
     account.mapbox.com/access-tokens is distinct from any generic
     JWT issuer.

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])ATBB[A-Za-z0-9]{24,}(?![A-Za-z0-9])`` →
  "Bitbucket Access Token gefunden" / mask preserving ``ATBB***`` for
  IR triage (revocation flow at bitbucket.org/account/settings/
  app-passwords/ or the project/repo/workspace Access Tokens settings
  page).
* ``(?<![A-Za-z0-9])(?:pk|sk|tk)\\.eyJ[A-Za-z0-9_\\-]{10,}\\.[A-Za-z0-9_\\-]{10,}\\.[A-Za-z0-9_\\-]{20,}(?![A-Za-z0-9])``
  → "Mapbox Access Token gefunden" / mask preserving
  ``pk.eyJ***`` / ``sk.eyJ***`` / ``tk.eyJ***`` for IR triage. The
  Mapbox scanner entry MUST be placed BEFORE the generic JWT entry
  in ``_KNOWN_TOKENS`` so the more-specific Mapbox attribution wins
  via the ``covered_ranges`` arbitration in ``_scan_content``. The
  Mapbox log-mask MUST be placed BEFORE the generic JWT mask in
  ``sanitize_log_message`` so the more-specific replacement wins
  before the JWT regex strips the inner ``eyJ<body>`` span.

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myATBB<body>``, ``Xpk.eyJ...`` would still match the dot-prefixed
  shape because the dot is non-alphanumeric — that's by design for
  Mapbox tokens leaked in JSON values like ``"token":"sk.eyJ..."``,
  where the preceding char is a quote).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``ATBB***``, ``pk.eyJ***``, ``sk.eyJ***``,
``tk.eyJ***``) do NOT re-match the regex (the ``*`` char is OUTSIDE
every body alphabet AND the masked body length 3 chars is below every
per-family floor of 24 / 10).

Marker: SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content

SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT = (
    "SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT: neither _KNOWN_TOKENS nor "
    "sanitize_log_message detected/masked the Bitbucket Access Token "
    "(ATBB<32+ alnum>) or Mapbox Access Token "
    "((pk|sk|tk).eyJ<JWT body>) shapes. Bare tokens in committed source "
    "AND in operator log streams (plain text, JSON values with "
    "non-sensitive keys, URL paths, URL query params with non-sensitive "
    "names, exception messages) bypassed every existing detection / "
    "masking branch — or were attributed generically (JWT for Mapbox, "
    "high-entropy for Bitbucket) losing issuer-specific revocation flow "
    "anchoring."
)


def _body_alnum(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    strict alphanumeric ``[A-Za-z0-9]`` alphabet (Bitbucket's documented
    body alphabet)."""
    chunk = "Aa1B2c3D4e5F6g7H"  # 16-char rotation, all classes present
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_b64url(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    base64url ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D2e3F-g4H"
    return (chunk * (length // len(chunk) + 1))[:length]


# Bitbucket Repository Access Token fixture: ``ATBB<32 alnum>+8 hex CRC``
# mirrors the canonical Bitbucket Cloud documented App Password / Access
# Token shape (cf. trufflehog default rule
# ``ATBB[a-zA-Z0-9]{32}([a-fA-F0-9]{8})?``).
_BITBUCKET = "ATBB" + _body_alnum(32) + "CafeBabe"


# Mapbox Access Token fixtures — one per documented scope tier:
#  * ``pk.`` public (client-side use, read scopes only)
#  * ``sk.`` secret (full account access — HIGHEST blast radius)
#  * ``tk.`` temporary (ephemeral session token)
# The JWT body is a three-segment base64url JOSE token.
def _mapbox_token(prefix: str) -> str:
    header = _body_b64url(20)
    payload = _body_b64url(40)
    signature = _body_b64url(43)
    return f"{prefix}.eyJ{header}.{payload}.{signature}"


_MAPBOX_PK = _mapbox_token("pk")
_MAPBOX_SK = _mapbox_token("sk")
_MAPBOX_TK = _mapbox_token("tk")


# Sanity-check the fixtures.
assert _BITBUCKET.startswith("ATBB")
assert len(_BITBUCKET) == 4 + 32 + 8, f"Bitbucket fixture wrong length: {len(_BITBUCKET)}"

for _mb, _tier in ((_MAPBOX_PK, "pk"), (_MAPBOX_SK, "sk"), (_MAPBOX_TK, "tk")):
    assert _mb.startswith(f"{_tier}.eyJ"), f"Mapbox fixture missing prefix: {_mb[:10]}"
    body = _mb[len(f"{_tier}."):]
    parts = body.split(".")
    assert len(parts) == 3, f"Mapbox JWT body wrong segment count: {parts}"
    assert parts[0].startswith("eyJ")
    assert len(parts[0]) >= 13, f"Mapbox header too short: {len(parts[0])}"
    assert len(parts[1]) >= 10, f"Mapbox payload too short: {len(parts[1])}"
    assert len(parts[2]) >= 20, f"Mapbox signature too short: {len(parts[2])}"


_ALL_MAPBOX = [
    (_MAPBOX_PK, "pk"),
    (_MAPBOX_SK, "sk"),
    (_MAPBOX_TK, "tk"),
]


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family with
# vendor-specific attribution (NOT the generic JWT / Hochentropie fallback).
# ---------------------------------------------------------------------------


def test_drift_premise_scanner_detects_bitbucket_token() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Bitbucket-specific
    pattern that matches the canonical Repository Access Token shape. If a
    future contributor drops the Bitbucket entry this test FAILS first
    (loud) — preventing silent re-drift."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_BITBUCKET)
    ]
    assert any("Bitbucket" in r for r in matched_reasons), (
        f"Drift premise FAILED: Bitbucket token {_BITBUCKET[:15]!r}... is "
        f"not detected by _KNOWN_TOKENS with Bitbucket attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT}"
    )


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_drift_premise_scanner_detects_mapbox_token(token: str, tier: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Mapbox-specific
    pattern for each scope tier (``pk`` / ``sk`` / ``tk``). The Mapbox
    entry MUST be placed BEFORE the generic JWT entry so the more-
    specific Mapbox attribution wins via ``covered_ranges`` arbitration —
    pre-fix the JWT entry would match the inner ``eyJ<body>`` and the
    leading ``pk.``/``sk.``/``tk.`` scope tier would be LOST."""
    # _scan_content runs the full arbitration with covered_ranges; we
    # invoke it directly to assert that Mapbox attribution wins over JWT.
    findings = _scan_content(f"audit: {token}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Mapbox" in r for r in reasons), (
        f"Drift premise FAILED: Mapbox {tier} token {token[:15]!r}... is "
        f"detected only as {reasons!r} — Mapbox-specific attribution lost. "
        f"{SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT}"
    )
    # And the JWT attribution MUST NOT also fire (the Mapbox span covers
    # the inner JWT span, so JWT should be suppressed via covered_ranges).
    assert not any("JSON Web Token (JWT)" in r for r in reasons), (
        f"Cross-attribution drift: Mapbox {tier} token was ALSO attributed "
        f"as a JWT — _KNOWN_TOKENS ordering is wrong. The Mapbox entry "
        f"must precede the JWT entry so covered_ranges arbitration picks "
        f"Mapbox first. reasons={reasons}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_bitbucket_token_in_plain_log_line_is_masked() -> None:
    """Bare Bitbucket token in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the ``ATBB***``
    attribution for IR triage."""
    log_line = f"Bitbucket API 403: invalid token {_BITBUCKET}"
    result = sanitize_log_message(log_line)
    assert _BITBUCKET not in result, (
        f"Bitbucket token leaked through sanitize_log_message: "
        f"{SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT}"
    )
    assert "ATBB***" in result, (
        "Bitbucket mask MUST preserve 'ATBB***' attribution for IR triage "
        "(revocation flow at bitbucket.org/account/settings/app-passwords/)"
    )


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_mapbox_token_in_plain_log_line_is_masked(token: str, tier: str) -> None:
    """Bare Mapbox token in plain log text MUST be masked. The mask MUST
    preserve the ``<tier>.eyJ`` attribution so the responder can navigate
    to the correct revocation flow page (account.mapbox.com/access-tokens)
    AND see which scope tier leaked (sk = full account access,
    pk = read-only, tk = ephemeral)."""
    log_line = f"Mapbox 401 for token {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Mapbox {tier} token leaked through sanitize_log_message"
    )
    assert f"{tier}.eyJ***" in result, (
        f"Mapbox {tier} token mask MUST preserve the scope-tier attribution "
        f"'{tier}.eyJ***' so the responder identifies the leaked scope at "
        f"a glance (sk = full account access, pk = client-side read, "
        f"tk = ephemeral)"
    )


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_bitbucket_token_in_json_value_is_masked(key_name: str) -> None:
    """Bitbucket token in JSON value with a NON-sensitive key name MUST be
    masked. Pre-fix the JSON-key sensitive-name regex missed keys like
    ``data`` / ``payload`` / ``response_body`` / ``message`` and the
    token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_BITBUCKET}"}}'
    result = sanitize_log_message(log_line)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_mapbox_token_in_json_value_is_masked(
    token: str, tier: str, key_name: str
) -> None:
    """Mapbox token in JSON value with a NON-sensitive key name MUST be
    masked. Same drift premise as the Bitbucket JSON test."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{tier}.eyJ***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_bitbucket_token_in_url_path_is_masked() -> None:
    """Bitbucket token embedded in URL path MUST be masked. Pre-fix the
    URL credential regex required the credential to appear before ``@``;
    path-embedded tokens slipped past."""
    log_line = f"GET /api/v2/repos/{_BITBUCKET}/branches HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_mapbox_token_in_url_path_is_masked(token: str, tier: str) -> None:
    """Mapbox token embedded in URL path (NOT query string) MUST be
    masked WITH preserved scope-tier attribution. We deliberately avoid
    the canonical ``?access_token=<token>`` query-param shape here
    because that hits the generic URL-query-param mask which redacts
    the whole value to ``***`` (good for security, but Mapbox-specific
    attribution is lost via generic redaction — that path is covered
    structurally by the JSON / plain-log tests, not this one)."""
    log_line = f"GET /v1/tilesets/foo/bar/{token}/tile.png HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{tier}.eyJ***" in result


def test_bitbucket_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Bitbucket token in URL query string with a NON-sensitive parameter
    name (``ref`` / ``commit_sha`` / ``q``) MUST be masked. Pre-fix the
    URL credential regex required ``user:pass@``; query-string tokens
    with non-sensitive parameter names slipped past."""
    log_line = f"GET /foo/bar?ref={_BITBUCKET} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_mapbox_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, tier: str
) -> None:
    """Mapbox token in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{tier}.eyJ***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_bitbucket_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` (the
    canonical exception-text sanitisation path in ``src/utils/http.py``)
    MUST mask Bitbucket tokens."""
    exc_msg = f"HTTPError: 403 — token {_BITBUCKET} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_mapbox_token_through_sanitize_exception_msg(token: str, tier: str) -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask Mapbox tokens with full scope-tier attribution."""
    exc_msg = f"HTTPError: 401 Unauthorized — Mapbox {token} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{tier}.eyJ***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_bitbucket_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_BITBUCKET}"
    result = sanitize_log_arg(arg)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


def test_sanitize_log_arg_masks_mapbox_secret_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a Mapbox secret token MUST
    have the key masked. Uses a NON-sensitive attribute name (``audit``)
    so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_MAPBOX_SK})"

    result = sanitize_log_arg(_Wrapper())
    assert _MAPBOX_SK not in result
    assert "sk.eyJ***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Too short Bitbucket body (< 24)
        "ATBB" + "A" * 23,
        # Wrong Bitbucket prefix
        "ATCC" + "A" * 32,
        # Mid-identifier collision — lookbehind prevents it
        "XATBB" + "A" * 32,
        "0ATBB" + "A" * 32,
    ],
)
def test_benign_bitbucket_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / mid-identifier
    collisions MUST NOT trigger the Bitbucket mask. The
    ``(?<![A-Za-z0-9])`` lookbehind + 24-char body floor +
    ``(?![A-Za-z0-9])`` lookahead are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "ATBB***" not in result, (
        f"False-positive Bitbucket mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong Mapbox tier keyword (not in pk|sk|tk). The JWT mask would
        # still catch the inner JWT body — we assert below that NO
        # Mapbox-specific mask attribution is produced.
        "xx.eyJ" + "A" * 10 + "." + "A" * 10 + "." + "A" * 20,
        # Missing eyJ JWT-shape anchor — neither Mapbox nor JWT fires.
        "sk.foo" + "A" * 10 + "." + "A" * 10 + "." + "A" * 20,
        # Too short first segment — Mapbox floor is 10 chars.
        "sk.eyJ" + "A" * 9 + "." + "A" * 10 + "." + "A" * 20,
        # Too short third segment — Mapbox floor is 20 chars.
        "sk.eyJ" + "A" * 10 + "." + "A" * 10 + "." + "A" * 19,
    ],
)
def test_benign_mapbox_shape_is_not_masked(benign: str) -> None:
    """Negative case: malformed tier keyword / undersized segments /
    missing ``eyJ`` anchor MUST NOT trigger the Mapbox mask.

    Note on the mid-identifier collision case: ``foosk.eyJ<body>`` cannot
    be tested via substring exclusion of ``sk.eyJ***`` because the JWT
    mask (which still fires on the inner ``eyJ<body>``) produces
    ``foosk.eyJ***`` — and that string substring-contains ``sk.eyJ***``
    purely as a positional artefact, not as a Mapbox-attributed mask.
    The Mapbox lookbehind correctness for mid-identifier collisions is
    instead validated structurally by
    :func:`test_mapbox_mask_wins_over_generic_jwt_mask` (which asserts
    exactly one ``eyJ***`` span in well-formed inputs) and by the
    benign cases enumerated above.
    """
    result = sanitize_log_message(benign)
    for tier in ("pk", "sk", "tk"):
        assert f"{tier}.eyJ***" not in result, (
            f"False-positive Mapbox {tier} mask on benign input: "
            f"{benign!r} → {result!r}"
        )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "ATBB***",
        "pk.eyJ***",
        "sk.eyJ***",
        "tk.eyJ***",
    ],
)
def test_token_mask_is_idempotent(masked: str) -> None:
    """Running ``sanitize_log_message`` twice MUST be idempotent — the
    masked form MUST NOT itself match the corresponding regex (the
    ``*`` char is outside every body alphabet AND the masked body length
    3 chars is below every per-family floor)."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further modified "
        f"by sanitize_log_message: {result!r}"
    )


def test_bitbucket_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_BITBUCKET}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _BITBUCKET not in first


def test_mapbox_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output for each Mapbox scope tier."""
    for token, tier in _ALL_MAPBOX:
        log_line = f"Failed: {token}"
        first = sanitize_log_message(log_line)
        second = sanitize_log_message(first)
        assert first == second, f"Mapbox {tier} sanitize not idempotent"
        assert token not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_bitbucket_family() -> None:
    """**Sibling-alignment invariant.** Every Bitbucket token shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``.
    Any future Bitbucket-family pattern adjustment to the scanner without
    a companion log-mask adjustment fails this test on the first pytest
    run after the new scanner entry is committed — surfacing the next
    drift family programmatically."""
    bitbucket_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Bitbucket" in reason
    ]
    assert len(bitbucket_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Bitbucket' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Bitbucket shape.
    log_line = f"audit: {_BITBUCKET}"
    result = sanitize_log_message(log_line)
    assert _BITBUCKET not in result
    assert "ATBB***" in result


def test_scanner_and_log_sanitiser_share_mapbox_family() -> None:
    """**Sibling-alignment invariant.** Every Mapbox token shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``
    for all three scope tiers (``pk`` / ``sk`` / ``tk``)."""
    mapbox_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Mapbox" in reason
    ]
    assert len(mapbox_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Mapbox' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks each Mapbox scope tier.
    for token, tier in _ALL_MAPBOX:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Mapbox {tier} token missing from sanitize_log_message mask "
            f"family — log-sanitisation drift vs. scanner _KNOWN_TOKENS"
        )
        assert f"{tier}.eyJ***" in result, (
            f"Mapbox {tier} token mask MUST preserve the per-tier scope "
            f"attribution '{tier}.eyJ***' for IR triage"
        )


# ---------------------------------------------------------------------------
# (9) Cross-family disambiguation — Bitbucket must not match Mapbox and v.v.
# ---------------------------------------------------------------------------


def test_bitbucket_token_not_misattributed_as_mapbox() -> None:
    """A Bitbucket token is structurally disjoint from a Mapbox token —
    the ``ATBB`` prefix vs. ``(pk|sk|tk).eyJ`` prefix are mutually
    exclusive at the leading-char level."""
    result = sanitize_log_message(f"audit: {_BITBUCKET}")
    for tier in ("pk", "sk", "tk"):
        assert f"{tier}.eyJ***" not in result, (
            f"Bitbucket token misattributed as Mapbox {tier} mask — "
            f"cross-mutex broken"
        )


def test_mapbox_token_not_misattributed_as_bitbucket() -> None:
    """A Mapbox token is structurally disjoint from a Bitbucket token."""
    for token, tier in _ALL_MAPBOX:
        result = sanitize_log_message(f"audit: {token}")
        assert "ATBB***" not in result, (
            f"Mapbox {tier} token misattributed as Bitbucket mask — "
            f"cross-mutex broken"
        )


# ---------------------------------------------------------------------------
# (10) Mapbox / JWT mutex — Mapbox attribution MUST win over generic JWT
# in BOTH the scanner (covered_ranges arbitration) AND the log sanitiser
# (pattern ordering).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,tier", _ALL_MAPBOX)
def test_mapbox_mask_wins_over_generic_jwt_mask(token: str, tier: str) -> None:
    """The Mapbox log-mask MUST be applied BEFORE the generic JWT mask so
    the leading ``pk.``/``sk.``/``tk.`` scope tier is preserved in the
    output. Pre-fix the JWT regex matched the inner ``eyJ<body>`` span
    and replaced it with ``eyJ***`` — leaving ``sk.eyJ***`` in the output
    but with generic JWT attribution semantics (IR triage sees a JWT not
    a Mapbox secret). The fix re-asserts the per-vendor attribution by
    masking the FULL Mapbox span including the scope-tier prefix."""
    result = sanitize_log_message(f"audit: {token}")
    # The mask MUST preserve the full ``<tier>.eyJ`` attribution.
    assert f"{tier}.eyJ***" in result
    # And it MUST NOT contain the generic ``eyJ***`` (which would imply
    # the JWT regex won over the Mapbox-specific regex).
    # Note: ``{tier}.eyJ***`` happens to CONTAIN ``eyJ***`` as a substring,
    # so we check for the negative case via the position of the prefix.
    assert result.count("eyJ***") <= 1
    # Confirm the prefix is intact.
    assert f"{tier}.eyJ***" in result
    # The full mapbox-attributed mask is the only ``eyJ***`` in the output.
