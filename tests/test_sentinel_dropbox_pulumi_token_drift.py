"""Sentinel drift coverage for the Dropbox + Pulumi token tier value-shape
detection (``_KNOWN_TOKENS``) AND log-sanitisation (``sanitize_log_message``
plus the downstream ``_sanitize_exception_msg`` chain).

After the 2026-05-18 Bitbucket + Mapbox round closed the source-control /
geospatial-vendor tier, two more high-blast-radius vendor families remain
SILENTLY UNCOVERED across BOTH detection codepaths — neither the secret
scanner nor the log sanitiser attribute tokens for these issuers:

* **Dropbox Short-Lived Access Token (``sl.<base64url body>``)** — the
  canonical Dropbox OAuth2 short-lived access-token format (post-2021
  rollout; replaces the legacy 64-char-alphanumeric long-lived tokens).
  Issued by the ``oauth2/token`` endpoint with ``grant_type=refresh_token``
  and consumed by every Dropbox HTTP API endpoint (``/2/files/*``,
  ``/2/sharing/*``, ``/2/team/*``) for full file-storage / sharing /
  team-admin access. The dot separating the ``sl`` issuer prefix from
  the base64url body sits OUTSIDE the entropy fallback's
  ``[A-Za-z0-9+/=_-]`` alphabet so the prefix is stripped from any
  ``_HIGH_ENTROPY_RE`` match — pre-fix the body alone matched as
  generic ``Hochentropischer Token-String`` and the Dropbox-specific
  issuer attribution that anchors the revocation flow at
  dropbox.com/developers/apps was LOST.

* **Pulumi Access Token (``pul-<40 lowercase hex>``)** — the canonical
  Pulumi Personal Access Token format. Issued via app.pulumi.com/account/
  tokens for full Pulumi Cloud API access: read/write every stack's
  state (which encodes EVERY secret persisted by the IaC pipeline —
  cloud provider credentials, database passwords, third-party API
  keys, TLS private keys for issued certificates), trigger arbitrary
  ``pulumi up`` operations (modify production infrastructure), and
  exfiltrate the org's complete deployment-history audit log. Format
  matches the canonical trufflehog / gitleaks / detect-secrets default
  rule (``pul-[a-f0-9]{40}``). Pre-fix the entropy fallback matched
  the full ``pul-<body>`` span as generic ``Hochentropischer
  Token-String`` (both ``-`` and the lowercase hex body lie inside
  the entropy alphabet), losing the Pulumi-specific issuer
  attribution.

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"Dropbox: {token}")``)
   leaks verbatim to operator log streams and the public
   ``docs/feed_health.json`` artefact.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "sl.<body>"}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /api/files/pul-<body>/stacks``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /api/foo?ref=<token>``) bypasses the URL-query-param
   sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a Dropbox short-lived
   token or Pulumi access token as a JSON value, README example, or
   .env-like fixture sails past the scanner's existing
   ``_KNOWN_TOKENS`` table:
   - Dropbox ``sl.<body>``: the ``.`` separator falls OUTSIDE the
     entropy alphabet so only the body span matches
     ``_HIGH_ENTROPY_RE`` — the ``sl.`` issuer prefix is LOST from
     attribution, and the operator sees a generic
     "Hochentropischer Token-String" finding without knowing it
     demands dropbox.com/developers/apps revocation.
   - Pulumi ``pul-<40 hex>``: both ``-`` and lowercase hex are in
     the entropy alphabet so the full ``pul-<body>`` span matches
     ``_HIGH_ENTROPY_RE`` as generic high-entropy — the
     Pulumi-specific issuer attribution that anchors the
     app.pulumi.com/account/tokens revocation flow is LOST.

Blast radius per leaked credential:

* **Dropbox short-lived access token**: file-storage / sharing /
  team-admin scope per the issuing app's configured permissions.
  Full file read = data exfiltration (customer documents, source
  code backups, hand-typed credentials in plain-text notes,
  scanned ID cards / passports stored as personal-doc backups).
  File write = ransomware-style overwrite, malicious-document
  injection. Sharing scope = create unauthorized shared links
  exfiltrating the entire team's stored content via the public
  internet. Team admin scope = exfiltrate the team's complete
  member directory, revoke other admins' access, modify retention
  policies to enable persistence. The short-lived TTL (typically
  4 hours) bounds the blast window but the issuer's refresh
  token can re-mint short-lived tokens indefinitely — a leaked
  short-lived token strongly implies the refresh token is also
  exposed somewhere in the same artefact (CI env, repo config,
  developer workstation).

* **Pulumi access token (HIGHEST blast radius — IaC control plane)**:
  full Pulumi Cloud API access for the issuing user across every
  accessible org / project / stack. Read access = exfiltrate
  EVERY secret persisted in stack state (cloud provider
  credentials are the canonical IaC-stored credential class —
  AWS keys, Azure service principals, GCP service-account JSONs,
  database passwords, third-party API keys, TLS private keys
  for issued certificates), reconstruct the org's complete
  infrastructure topology for reconnaissance. Write access =
  trigger arbitrary ``pulumi up`` operations modifying production
  infrastructure (provision attacker-controlled VMs in the
  victim's cloud account, modify IAM bindings to grant
  attacker access, add backdoored DNS records to redirect
  customer traffic). The IaC control-plane breach is the canonical
  "pivot to every downstream environment via a single credential"
  amplifier — structurally analogous to a leaked Terraform Cloud
  workspace token or a leaked AWS root account credential.

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])sl\\.[A-Za-z0-9_\\-]{40,}(?![A-Za-z0-9])`` →
  "Dropbox Short-Lived Access Token gefunden" / mask preserving
  ``sl.***`` for IR triage (revocation flow at
  dropbox.com/developers/apps — App console > app settings >
  "Revoke tokens").
* ``(?<![A-Za-z0-9])pul-[a-f0-9]{40}(?![A-Za-z0-9])`` →
  "Pulumi Access Token gefunden" / mask preserving ``pul-***`` for
  IR triage (revocation flow at app.pulumi.com/account/tokens >
  "Revoke").

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``mysl.<body>``, ``Xpul-<body>`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``sl.***``, ``pul-***``) do NOT re-match
the regex (the ``*`` char is OUTSIDE every body alphabet AND the
masked body length 3 chars is below every per-family floor of 40 / 40).

Marker: SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content

SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT = (
    "SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT: neither _KNOWN_TOKENS nor "
    "sanitize_log_message detected/masked the Dropbox Short-Lived Access "
    "Token (sl.<base64url body 40+>) or Pulumi Access Token "
    "(pul-<40 lowercase hex>) shapes. Bare tokens in committed source "
    "AND in operator log streams (plain text, JSON values with "
    "non-sensitive keys, URL paths, URL query params with non-sensitive "
    "names, exception messages) bypassed every existing detection / "
    "masking branch — or were attributed generically "
    "(Hochentropischer Token-String for both) losing issuer-specific "
    "revocation flow anchoring."
)


def _body_b64url(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    base64url ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D2e3F-g4H"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    strict lowercase-hex ``[a-f0-9]`` alphabet (Pulumi's documented body
    alphabet)."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


# Dropbox Short-Lived Access Token fixture: ``sl.<140 base64url body>``
# mirrors the canonical Dropbox documented shape (real-world bodies
# range 130-160 chars; 140 is mid-range for a realistic fixture).
_DROPBOX = "sl." + _body_b64url(140)

# Pulumi Access Token fixture: ``pul-<40 lowercase hex>`` mirrors the
# canonical Pulumi documented format (per trufflehog / gitleaks default
# rule ``pul-[a-f0-9]{40}``).
_PULUMI = "pul-" + _body_hex(40)


# Sanity-check the fixtures.
assert _DROPBOX.startswith("sl.")
assert len(_DROPBOX) == 3 + 140, f"Dropbox fixture wrong length: {len(_DROPBOX)}"
assert _PULUMI.startswith("pul-")
assert len(_PULUMI) == 4 + 40, f"Pulumi fixture wrong length: {len(_PULUMI)}"
assert all(c in "0123456789abcdef" for c in _PULUMI[4:]), "Pulumi body must be lowercase hex"


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family with
# vendor-specific attribution (NOT the generic Hochentropie fallback).
# ---------------------------------------------------------------------------


def test_drift_premise_scanner_detects_dropbox_token() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Dropbox-specific
    pattern that matches the canonical Short-Lived Access Token shape. If a
    future contributor drops the Dropbox entry this test FAILS first
    (loud) — preventing silent re-drift."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_DROPBOX)
    ]
    assert any("Dropbox" in r for r in matched_reasons), (
        f"Drift premise FAILED: Dropbox token {_DROPBOX[:15]!r}... is "
        f"not detected by _KNOWN_TOKENS with Dropbox attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT}"
    )


def test_drift_premise_scanner_detects_pulumi_token() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Pulumi-specific
    pattern matching the canonical ``pul-<40 hex>`` shape."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_PULUMI)
    ]
    assert any("Pulumi" in r for r in matched_reasons), (
        f"Drift premise FAILED: Pulumi token {_PULUMI!r} is "
        f"not detected by _KNOWN_TOKENS with Pulumi attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT}"
    )


def test_dropbox_attribution_wins_over_generic_entropy() -> None:
    """The Dropbox-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught only the body span after ``sl.``
    as ``Hochentropischer Token-String``, losing the issuer attribution."""
    findings = _scan_content(f"audit: {_DROPBOX}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Dropbox" in r for r in reasons), (
        f"Dropbox attribution lost: {reasons!r}"
    )


def test_pulumi_attribution_wins_over_generic_entropy() -> None:
    """The Pulumi-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the entire ``pul-<body>`` span
    as ``Hochentropischer Token-String``, losing the issuer attribution."""
    findings = _scan_content(f"audit: {_PULUMI}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Pulumi" in r for r in reasons), (
        f"Pulumi attribution lost: {reasons!r}"
    )
    # And the generic high-entropy attribution MUST NOT also fire (the
    # Pulumi span covers the entire token, so the entropy fallback should
    # be suppressed via covered_ranges).
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: Pulumi token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_dropbox_token_in_plain_log_line_is_masked() -> None:
    """Bare Dropbox token in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the ``sl.***``
    attribution for IR triage."""
    log_line = f"Dropbox API 401: invalid token {_DROPBOX}"
    result = sanitize_log_message(log_line)
    assert _DROPBOX not in result, (
        f"Dropbox token leaked through sanitize_log_message: "
        f"{SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT}"
    )
    assert "sl.***" in result, (
        "Dropbox mask MUST preserve 'sl.***' attribution for IR triage "
        "(revocation flow at dropbox.com/developers/apps)"
    )


def test_pulumi_token_in_plain_log_line_is_masked() -> None:
    """Bare Pulumi token in plain log text MUST be masked. The mask MUST
    preserve the ``pul-***`` attribution for IR triage (revocation flow
    at app.pulumi.com/account/tokens)."""
    log_line = f"Pulumi 403 for token {_PULUMI}"
    result = sanitize_log_message(log_line)
    assert _PULUMI not in result, (
        "Pulumi token leaked through sanitize_log_message"
    )
    assert "pul-***" in result, (
        "Pulumi mask MUST preserve 'pul-***' attribution for IR triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_dropbox_token_in_json_value_is_masked(key_name: str) -> None:
    """Dropbox token in JSON value with a NON-sensitive key name MUST be
    masked. Pre-fix the JSON-key sensitive-name regex missed keys like
    ``data`` / ``payload`` / ``response_body`` / ``message`` and the
    token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_DROPBOX}"}}'
    result = sanitize_log_message(log_line)
    assert _DROPBOX not in result
    assert "sl.***" in result


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_pulumi_token_in_json_value_is_masked(key_name: str) -> None:
    """Pulumi token in JSON value with a NON-sensitive key name MUST be
    masked. Same drift premise as the Dropbox JSON test."""
    log_line = f'{{"{key_name}": "{_PULUMI}"}}'
    result = sanitize_log_message(log_line)
    assert _PULUMI not in result
    assert "pul-***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_dropbox_token_in_url_path_is_masked() -> None:
    """Dropbox token embedded in URL path MUST be masked. Pre-fix the
    URL credential regex required the credential to appear before ``@``;
    path-embedded tokens slipped past."""
    log_line = f"GET /2/files/list_folder/continue?cursor={_DROPBOX} HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert _DROPBOX not in result
    assert "sl.***" in result


def test_pulumi_token_in_url_path_is_masked() -> None:
    """Pulumi token embedded in URL path MUST be masked."""
    log_line = f"GET /api/stacks/{_PULUMI}/state HTTP/1.1 403"
    result = sanitize_log_message(log_line)
    assert _PULUMI not in result
    assert "pul-***" in result


def test_dropbox_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Dropbox token in URL query string with a NON-sensitive parameter
    name (``ref`` / ``commit_sha`` / ``q``) MUST be masked."""
    log_line = f"GET /foo/bar?ref={_DROPBOX} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _DROPBOX not in result
    assert "sl.***" in result


def test_pulumi_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Pulumi token in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={_PULUMI} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _PULUMI not in result
    assert "pul-***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_dropbox_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` (the
    canonical exception-text sanitisation path in ``src/utils/http.py``)
    MUST mask Dropbox tokens."""
    exc_msg = f"HTTPError: 401 — token {_DROPBOX} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _DROPBOX not in result
    assert "sl.***" in result


def test_pulumi_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask Pulumi tokens with full issuer attribution."""
    exc_msg = f"HTTPError: 403 Forbidden — Pulumi {_PULUMI} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert _PULUMI not in result
    assert "pul-***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_dropbox_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_DROPBOX}"
    result = sanitize_log_arg(arg)
    assert _DROPBOX not in result
    assert "sl.***" in result


def test_sanitize_log_arg_masks_pulumi_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a Pulumi token MUST
    have the value masked. Uses a NON-sensitive attribute name
    (``audit``) so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_PULUMI})"

    result = sanitize_log_arg(_Wrapper())
    assert _PULUMI not in result
    assert "pul-***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Too short Dropbox body (< 40)
        "sl." + _body_b64url(39),
        # Wrong Dropbox prefix
        "xl." + _body_b64url(60),
        # Mid-identifier collision — lookbehind prevents it
        "X" + "sl." + _body_b64url(60),
        "0" + "sl." + _body_b64url(60),
        # ISO 639 Slovenian language code in URL path (e.g. ``/sl/about``)
        # without a token-shaped body MUST NOT match.
        "https://example.com/sl/about",
    ],
)
def test_benign_dropbox_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / mid-identifier
    collisions / benign URL paths MUST NOT trigger the Dropbox mask.
    The ``(?<![A-Za-z0-9])`` lookbehind + 40-char body floor +
    ``(?![A-Za-z0-9])`` lookahead are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "sl.***" not in result, (
        f"False-positive Dropbox mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Too short Pulumi body (< 40)
        "pul-" + _body_hex(39),
        # Wrong Pulumi prefix
        "pun-" + _body_hex(40),
        # Body contains non-hex char (uppercase) — strict lowercase-hex
        # alphabet rejects it.
        "pul-" + "G" + _body_hex(39),
        # Mid-identifier collision
        "X" + "pul-" + _body_hex(40),
        "0" + "pul-" + _body_hex(40),
    ],
)
def test_benign_pulumi_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / non-hex body chars /
    mid-identifier collisions MUST NOT trigger the Pulumi mask."""
    result = sanitize_log_message(benign)
    assert "pul-***" not in result, (
        f"False-positive Pulumi mask on benign input: {benign!r} → "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "sl.***",
        "pul-***",
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


def test_dropbox_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_DROPBOX}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _DROPBOX not in first


def test_pulumi_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_PULUMI}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _PULUMI not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_dropbox_family() -> None:
    """**Sibling-alignment invariant.** Every Dropbox token shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``.
    Any future Dropbox-family pattern adjustment to the scanner without
    a companion log-mask adjustment fails this test on the first pytest
    run after the new scanner entry is committed — surfacing the next
    drift family programmatically."""
    dropbox_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Dropbox" in reason
    ]
    assert len(dropbox_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Dropbox' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Dropbox shape.
    log_line = f"audit: {_DROPBOX}"
    result = sanitize_log_message(log_line)
    assert _DROPBOX not in result
    assert "sl.***" in result


def test_scanner_and_log_sanitiser_share_pulumi_family() -> None:
    """**Sibling-alignment invariant.** Every Pulumi token shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``."""
    pulumi_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Pulumi" in reason
    ]
    assert len(pulumi_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Pulumi' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    log_line = f"audit: {_PULUMI}"
    result = sanitize_log_message(log_line)
    assert _PULUMI not in result
    assert "pul-***" in result


# ---------------------------------------------------------------------------
# (9) Cross-family disambiguation — Dropbox must not match Pulumi and v.v.
# ---------------------------------------------------------------------------


def test_dropbox_token_not_misattributed_as_pulumi() -> None:
    """A Dropbox token is structurally disjoint from a Pulumi token —
    the ``sl.`` prefix vs. ``pul-`` prefix are mutually exclusive at the
    leading-char level (``s`` vs. ``p``)."""
    result = sanitize_log_message(f"audit: {_DROPBOX}")
    assert "pul-***" not in result, (
        "Dropbox token misattributed as Pulumi mask — cross-mutex broken"
    )


def test_pulumi_token_not_misattributed_as_dropbox() -> None:
    """A Pulumi token is structurally disjoint from a Dropbox token."""
    result = sanitize_log_message(f"audit: {_PULUMI}")
    assert "sl.***" not in result, (
        "Pulumi token misattributed as Dropbox mask — cross-mutex broken"
    )
