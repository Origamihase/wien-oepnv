"""Sentinel drift coverage for the Figma + Tailscale token tier value-shape
detection (``_KNOWN_TOKENS``) AND log-sanitisation (``sanitize_log_message``
plus the downstream ``_sanitize_exception_msg`` chain).

After the 2026-05-17 Supply-Chain / E-Commerce / PaaS / Observability /
Email-Platform round closed the canonical SaaS-tier backlog, two more
high-blast-radius vendor families remain SILENTLY UNCOVERED across BOTH
detection codepaths — neither the secret scanner nor the log sanitiser
attribute tokens for these issuers:

* **Figma Personal Access Token (``figd_<43>``)** — a workspace-design-
  collaboration credential. The Figma issuer header (``X-Figma-Token``)
  is ALREADY enumerated in ``src/utils/http.py:_SENSITIVE_HEADERS`` (so
  cross-origin redirect stripping is wired up), but the corresponding
  TOKEN VALUE shape is NOT in ``_KNOWN_TOKENS`` and NOT in
  ``sanitize_log_message`` — a classic header/value drift: the header
  name reaches the operator log redacted, but a leaked Figma PAT
  embedded in a JSON response body / URL path / exception message
  routed through ``_sanitize_exception_msg`` leaks verbatim into
  operator log streams and the public ``docs/feed_health.json``
  artefact.

* **Tailscale Auth/API/Client/Webhook Key
  (``tskey-(?:auth|api|client|webhook)-<id>-<secret>``)** — the
  canonical Tailscale credential family used to register new tailnet
  nodes (``auth``), call the Tailscale admin REST API (``api``),
  authenticate OAuth clients (``client``), or verify webhook payloads
  (``webhook``). Neither the scanner nor the log sanitiser detects any
  of the four tier variants today. Leaking any of these grants
  tailnet-level network access — an attacker can attach a rogue node
  to the victim's private overlay network (``auth``), reconfigure ACLs
  / DNS / device management (``api``), mint fresh OAuth tokens
  (``client``), or forge tailnet event payloads (``webhook``). Blast
  radius is structurally equivalent to a leaked VPN private key or
  AWS IAM access key for the issuing tailnet's network-control plane.

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"Figma: {pat}")``)
   leaks verbatim to operator log streams.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "figd_<43>"}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex
   (``[a-z0-9_.\\-]*token`` / ``secret`` / ``key`` / etc.) because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /api/teams/<token>/files``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /api/foo?ref=<token>``) bypasses the URL-query-param
   sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a Figma PAT or
   Tailscale auth key as a JSON value, README example, or .env-like
   fixture sails past the scanner's existing ``_KNOWN_TOKENS`` table
   and the entropy fallback both:
   - Figma PAT ``figd_<43>``: the underscore-separator lies INSIDE the
     entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet so the full
     ``figd_<body>`` span matches as a generic ``Hochentropischer
     Token-String`` finding — but the Figma-specific issuer
     attribution that anchors the revocation flow at
     https://www.figma.com/settings/personal-access-tokens is LOST.
   - Tailscale ``tskey-(?:auth|api|client|webhook)-<id>-<secret>``:
     the multiple dash-separated segments bypass the entropy
     fallback's contiguous-match span (dashes ARE in the alphabet
     but Tailscale tokens are typically broken at the dashes by
     the heuristic split inside ``_scan_content``) — the issuer
     attribution that anchors the revocation flow at
     https://login.tailscale.com/admin/settings/keys is LOST AND
     individual ``<id>``/``<secret>`` spans frequently fall below
     the 24-char entropy floor.

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])figd_[A-Za-z0-9_\\-]{43}(?![A-Za-z0-9])`` →
  "Figma Personal Access Token gefunden" / mask preserving
  ``figd_***`` for IR triage.
* ``(?<![A-Za-z0-9])tskey-(?:auth|api|client|webhook)-[A-Za-z0-9]{8,}-[A-Za-z0-9]{20,}(?![A-Za-z0-9])``
  → "Tailscale Key gefunden" / mask preserving
  ``tskey-<type>-***`` for IR triage (the ``<type>`` segment is the
  attribution disambiguator — each tier has a distinct revocation
  semantic, see threat-model above).

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myfigd_<body>``, ``Xtskey-auth-...`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``figd_***``, ``tskey-auth-***``,
``tskey-api-***``, ``tskey-client-***``, ``tskey-webhook-***``) do NOT
match the regex (the ``*`` char is OUTSIDE every body alphabet AND the
masked body length 3 chars is below every per-family floor of 43 /
20 / 8). The Tailscale mask preserves the ``-<type>-`` attribution
segment so the responder can navigate to the correct revocation page
in seconds (auth-key revocation flow lives at the admin settings
"Keys" tab; OAuth-client revocation lives at the "OAuth clients" tab;
webhook secret rotation lives at the "Webhooks" tab — three distinct
sub-pages of https://login.tailscale.com/admin/settings).

Marker: SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT = (
    "SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT: neither _KNOWN_TOKENS nor "
    "sanitize_log_message detected/masked the Figma PAT (figd_<43>) or "
    "Tailscale key (tskey-(auth|api|client|webhook)-<id>-<secret>) shapes. "
    "Bare tokens in committed source AND in operator log streams (plain "
    "text, JSON values with non-sensitive keys, URL paths, URL query "
    "params with non-sensitive names, exception messages) bypassed every "
    "existing detection/masking branch."
)


def _body_b64url(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    base64url ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D"  # 8-char cycle: upper / lower / digit / dash / underscore
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_alnum(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    strict alphanumeric ``[A-Za-z0-9]`` alphabet (Tailscale's documented
    segment alphabet)."""
    chunk = "Aa1B2c3D"
    return (chunk * (length // len(chunk) + 1))[:length]


# Figma PAT fixture: ``figd_<43 chars from [A-Za-z0-9_-]>`` mirrors the
# documented canonical format (cf. trufflehog / gitleaks default rules).
_FIGMA_PAT = "figd_" + _body_b64url(43)

# Tailscale key fixtures — one per documented tier (auth / api / client /
# webhook). Format mirrors the documented canonical shape
# ``tskey-<tier>-<keyID>-<keySecret>`` with keyID 10 chars and keySecret
# 40 chars (real-world keyID is 9-14 alnum, keySecret is 30+ alnum).
_TS_AUTH = "tskey-auth-" + _body_alnum(10) + "-" + _body_alnum(40)
_TS_API = "tskey-api-" + _body_alnum(10) + "-" + _body_alnum(40)
_TS_CLIENT = "tskey-client-" + _body_alnum(10) + "-" + _body_alnum(40)
_TS_WEBHOOK = "tskey-webhook-" + _body_alnum(10) + "-" + _body_alnum(40)


# Sanity check the fixtures.
assert _FIGMA_PAT.startswith("figd_")
assert len(_FIGMA_PAT) == 5 + 43, f"Figma fixture wrong length: {len(_FIGMA_PAT)}"

for _ts, _tier in (
    (_TS_AUTH, "auth"),
    (_TS_API, "api"),
    (_TS_CLIENT, "client"),
    (_TS_WEBHOOK, "webhook"),
):
    assert _ts.startswith(f"tskey-{_tier}-")
    parts = _ts.split("-")
    assert len(parts) == 4, f"Tailscale fixture wrong segment count: {parts}"
    assert parts[0] == "tskey" and parts[1] == _tier
    assert len(parts[2]) >= 8, f"Tailscale keyID too short: {len(parts[2])}"
    assert len(parts[3]) >= 20, f"Tailscale keySecret too short: {len(parts[3])}"


_ALL_TAILSCALE = [
    (_TS_AUTH, "auth"),
    (_TS_API, "api"),
    (_TS_CLIENT, "client"),
    (_TS_WEBHOOK, "webhook"),
]


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family
# ---------------------------------------------------------------------------


def test_drift_premise_scanner_detects_figma_pat() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Figma-specific
    pattern that matches the canonical PAT shape. If a future contributor
    drops the Figma entry this test FAILS first (loud) — preventing
    silent re-drift."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_FIGMA_PAT)
    ]
    assert any("Figma" in r for r in matched_reasons), (
        f"Drift premise FAILED: Figma PAT {_FIGMA_PAT[:15]!r}... is not "
        f"detected by _KNOWN_TOKENS. matched_reasons={matched_reasons}. "
        f"{SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT}"
    )


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
def test_drift_premise_scanner_detects_tailscale_key(token: str, tier: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Tailscale-
    specific pattern that matches each of the four documented tier
    variants (``auth`` / ``api`` / ``client`` / ``webhook``)."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("Tailscale" in r for r in matched_reasons), (
        f"Drift premise FAILED: Tailscale {tier} key {token[:25]!r}... is "
        f"not detected by _KNOWN_TOKENS. matched_reasons={matched_reasons}. "
        f"{SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_figma_pat_in_plain_log_line_is_masked() -> None:
    """Bare Figma PAT in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the
    ``figd_***`` attribution for IR triage."""
    log_line = f"Figma API 403: invalid PAT {_FIGMA_PAT}"
    result = sanitize_log_message(log_line)
    assert _FIGMA_PAT not in result, (
        f"Figma PAT leaked through sanitize_log_message: "
        f"{SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT}"
    )
    assert "figd_***" in result, (
        "Figma PAT mask MUST preserve 'figd_***' attribution for IR triage "
        "(revocation flow at figma.com/settings/personal-access-tokens)"
    )


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
def test_tailscale_key_in_plain_log_line_is_masked(token: str, tier: str) -> None:
    """Bare Tailscale key in plain log text MUST be masked. The mask MUST
    preserve the ``tskey-<tier>-`` attribution so the responder can
    navigate to the correct revocation flow page."""
    log_line = f"Tailscale 401 for key {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Tailscale {tier} key leaked through sanitize_log_message"
    )
    assert f"tskey-{tier}-***" in result, (
        f"Tailscale {tier} key mask MUST preserve the tier attribution "
        f"'tskey-{tier}-***' so the responder lands on the correct "
        f"revocation sub-page (auth/api/client/webhook each live on a "
        f"distinct admin sub-page)"
    )


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_figma_pat_in_json_value_is_masked(key_name: str) -> None:
    """Figma PAT in JSON value with a NON-sensitive key name MUST be
    masked. Pre-fix the JSON-key sensitive-name regex missed keys like
    ``data`` / ``payload`` / ``response_body`` / ``message`` and the
    token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_FIGMA_PAT}"}}'
    result = sanitize_log_message(log_line)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_tailscale_key_in_json_value_is_masked(
    token: str, tier: str, key_name: str
) -> None:
    """Tailscale key in JSON value with a NON-sensitive key name MUST be
    masked. Same drift premise as the Figma JSON test."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"tskey-{tier}-***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_figma_pat_in_url_path_is_masked() -> None:
    """Figma PAT embedded in URL path MUST be masked. Pre-fix the URL
    credential regex required the credential to appear before ``@``;
    path-embedded tokens slipped past."""
    log_line = f"GET /api/v1/teams/{_FIGMA_PAT}/files HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
def test_tailscale_key_in_url_path_is_masked(token: str, tier: str) -> None:
    """Tailscale key embedded in URL path MUST be masked."""
    log_line = f"GET /api/v2/tailnet/example.com/keys/{token} HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"tskey-{tier}-***" in result


def test_figma_pat_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Figma PAT in URL query string with a NON-sensitive parameter name
    (``ref`` / ``commit_sha`` / ``q``) MUST be masked. Pre-fix the URL
    credential regex required ``user:pass@``; query-string tokens with
    non-sensitive parameter names slipped past."""
    log_line = f"GET /foo/bar?ref={_FIGMA_PAT} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
def test_tailscale_key_in_url_query_with_non_sensitive_param_is_masked(
    token: str, tier: str
) -> None:
    """Tailscale key in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"tskey-{tier}-***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_figma_pat_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask Figma PATs."""
    exc_msg = f"HTTPError: 403 — PAT {_FIGMA_PAT} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


@pytest.mark.parametrize("token,tier", _ALL_TAILSCALE)
def test_tailscale_key_through_sanitize_exception_msg(token: str, tier: str) -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask Tailscale keys."""
    exc_msg = f"HTTPError: 401 Unauthorized — tailnet auth {token} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"tskey-{tier}-***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_figma_pat_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_FIGMA_PAT}"
    result = sanitize_log_arg(arg)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


def test_sanitize_log_arg_masks_tailscale_key_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a Tailscale key MUST have
    the key masked. Uses a NON-sensitive attribute name (``audit``) so
    the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_TS_AUTH})"

    result = sanitize_log_arg(_Wrapper())
    assert _TS_AUTH not in result
    assert "tskey-auth-***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Too short Figma body (< 43)
        "figd_" + "A" * 42,
        # Wrong Figma prefix
        "figx_" + "A" * 43,
        # Mid-identifier collision — lookbehind prevents it
        "Xfigd_" + "A" * 43,
        "0figd_" + "A" * 43,
        # Suffix overrun — lookahead prevents it
        "figd_" + "A" * 44,
    ],
)
def test_benign_figma_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / mid-identifier
    collisions / suffix overruns MUST NOT trigger the Figma mask. The
    ``(?<![A-Za-z0-9])`` lookbehind + exact 43-char body + ``(?![A-Za-z0-9])``
    lookahead are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "figd_***" not in result, (
        f"False-positive Figma mask on benign input: {benign!r} → {result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong tier keyword (not in auth|api|client|webhook)
        "tskey-foo-" + "A" * 10 + "-" + "A" * 40,
        "tskey-other-" + "A" * 10 + "-" + "A" * 40,
        # Missing tier - just tskey-<id>-<secret>
        "tskey-" + "A" * 10 + "-" + "A" * 40,
        # Too short keyID (< 8)
        "tskey-auth-" + "A" * 7 + "-" + "A" * 40,
        # Too short keySecret (< 20)
        "tskey-auth-" + "A" * 10 + "-" + "A" * 19,
        # Mid-identifier collision — lookbehind prevents this
        "Xtskey-auth-" + "A" * 10 + "-" + "A" * 40,
        "0tskey-auth-" + "A" * 10 + "-" + "A" * 40,
    ],
)
def test_benign_tailscale_shape_is_not_masked(benign: str) -> None:
    """Negative case: malformed tier keyword / undersized segments / mid-
    identifier collisions MUST NOT trigger the Tailscale mask."""
    result = sanitize_log_message(benign)
    for tier in ("auth", "api", "client", "webhook"):
        assert f"tskey-{tier}-***" not in result, (
            f"False-positive Tailscale {tier} mask on benign input: "
            f"{benign!r} → {result!r}"
        )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "figd_***",
        "tskey-auth-***",
        "tskey-api-***",
        "tskey-client-***",
        "tskey-webhook-***",
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


def test_figma_pat_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_FIGMA_PAT}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _FIGMA_PAT not in first


def test_tailscale_key_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output for each Tailscale tier."""
    for token, tier in _ALL_TAILSCALE:
        log_line = f"Failed: {token}"
        first = sanitize_log_message(log_line)
        second = sanitize_log_message(first)
        assert first == second, f"Tailscale {tier} sanitize not idempotent"
        assert token not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_figma_family() -> None:
    """**Sibling-alignment invariant.** Every Figma PAT shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``.
    Any future Figma-family pattern adjustment to the scanner without a
    companion log-mask adjustment fails this test on the first pytest
    run after the new scanner entry is committed — surfacing the next
    drift family programmatically."""
    figma_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Figma" in reason
    ]
    assert len(figma_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Figma' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Figma PAT shape.
    log_line = f"audit: {_FIGMA_PAT}"
    result = sanitize_log_message(log_line)
    assert _FIGMA_PAT not in result
    assert "figd_***" in result


def test_scanner_and_log_sanitiser_share_tailscale_family() -> None:
    """**Sibling-alignment invariant.** Every Tailscale key shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``
    for all four tier variants (``auth`` / ``api`` / ``client`` /
    ``webhook``)."""
    tailscale_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Tailscale" in reason
    ]
    assert len(tailscale_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Tailscale' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks each Tailscale tier.
    for token, tier in _ALL_TAILSCALE:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Tailscale {tier} key missing from sanitize_log_message mask "
            f"family — log-sanitisation drift vs. scanner _KNOWN_TOKENS"
        )
        assert f"tskey-{tier}-***" in result, (
            f"Tailscale {tier} key mask MUST preserve the per-tier "
            f"attribution 'tskey-{tier}-***' for IR triage"
        )


# ---------------------------------------------------------------------------
# (9) Cross-family disambiguation — Figma must not match Tailscale and v.v.
# ---------------------------------------------------------------------------


def test_figma_pat_not_misattributed_as_tailscale() -> None:
    """A Figma PAT is structurally disjoint from a Tailscale key — the
    ``figd_`` prefix vs. ``tskey-`` prefix are mutually exclusive at the
    leading-char level. Cross-attribution is structurally impossible."""
    result = sanitize_log_message(f"audit: {_FIGMA_PAT}")
    for tier in ("auth", "api", "client", "webhook"):
        assert f"tskey-{tier}-***" not in result, (
            f"Figma PAT misattributed as Tailscale {tier} mask — cross-mutex "
            f"broken"
        )


def test_tailscale_key_not_misattributed_as_figma() -> None:
    """A Tailscale key is structurally disjoint from a Figma PAT."""
    for token, tier in _ALL_TAILSCALE:
        result = sanitize_log_message(f"audit: {token}")
        assert "figd_***" not in result, (
            f"Tailscale {tier} key misattributed as Figma mask — cross-mutex "
            f"broken"
        )
