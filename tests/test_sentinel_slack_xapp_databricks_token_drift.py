"""Sentinel drift coverage for the Slack App-Level Token (``xapp-``) +
Databricks Personal Access Token (``dapi<32 hex>``) value-shape detection
(``_KNOWN_TOKENS``) AND log-sanitisation (``sanitize_log_message`` plus the
downstream ``_sanitize_exception_msg`` chain).

After the 2026-05-18 Dropbox + Pulumi round closed the file-storage SaaS /
IaC-control-plane tier, two more high-blast-radius vendor families remain
SILENTLY UNCOVERED across BOTH detection codepaths — neither the secret
scanner nor the log sanitiser attribute tokens for these issuers:

* **Slack App-Level Token (``xapp-<version>-<app_id>-<seq>-<hex>``)** —
  the canonical Slack App-Level Token format used for Socket Mode and
  app-level Events API access. Issued via api.slack.com/apps/<id>/general
  ("App-Level Tokens" section) with the ``connections:write`` and/or
  ``authorizations:read`` scopes. Pre-fix the multi-dash multi-segment
  format split at every ``-`` boundary in the entropy fallback (the
  inter-segment ``-`` IS in the entropy alphabet but the per-segment
  bodies fall BELOW the 24-char floor — the 1-digit version, 11-char
  app id, and 13-digit sequence are all too short to trip the entropy
  detector independently), so the FULL ``xapp-1-<app_id>-<seq>-<hex>``
  span was SILENTLY UNDETECTED entirely — not even a generic
  ``Hochentropischer Token-String`` finding fired. This is a strict
  sibling-drift gap relative to the existing ``xoxb-``/``xoxp-``/
  ``xoxa-``/``xoxc-``/``xoxd-``/``xoxe-``/``xoxr-`` Slack family
  entries that DO have dedicated ``_KNOWN_TOKENS`` rows.

* **Databricks Personal Access Token (``dapi<32 hex>(?:-<digit>)?``)**
  — the canonical Databricks PAT format. Issued via the Databricks
  workspace UI (User Settings → Developer → Access tokens) for full
  workspace-scoped API access (Databricks REST API ``/api/2.0/...``).
  Pre-fix the body (32 lowercase hex chars) lies ENTIRELY inside the
  entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet — the entropy
  regex matched the full ``dapi<body>`` span as one generic
  ``Hochentropischer Token-String`` finding, losing the
  Databricks-specific issuer attribution that anchors the per-workspace
  revocation flow (User Settings → Developer → Access tokens > "Revoke").

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"Slack 401: {token}")``)
   leaks verbatim to operator log streams and the public
   ``docs/feed_health.json`` artefact.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "xapp-1-A012345ABCD-..."}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /api/2.0/clusters/list?token=dapi<body>``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /api/foo?ref=<token>``) bypasses the URL-query-param
   sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a Slack App-Level
   Token or Databricks PAT as a JSON value, README example, or
   .env-like fixture sails past the scanner's existing
   ``_KNOWN_TOKENS`` table:
   - Slack ``xapp-<v>-<app>-<seq>-<hex>``: the multi-dash format
     splits the entropy match at every per-segment boundary into
     fragments below the 24-char floor — the FULL token escapes
     detection entirely (NO finding at all, not even generic).
   - Databricks ``dapi<32 hex>``: the body lies inside the entropy
     alphabet so ``_HIGH_ENTROPY_RE`` matches the full ``dapi<body>``
     span as generic high-entropy — the Databricks-specific issuer
     attribution that anchors the workspace-scoped revocation flow
     is LOST.

Blast radius per leaked credential:

* **Slack App-Level Token (HIGH blast radius — workspace event firehose
  + app management)**: a leaked ``xapp-`` grants the holder the
  app's Socket Mode connection (full firehose of every app-subscribed
  workspace event — DM contents, channel messages the app can see,
  interactive component payloads, slash command invocations,
  modal submissions). Combined with ``authorizations:read`` it
  enumerates every workspace install of the app (cross-tenant
  pivot — one leaked App-Level Token compromises every workspace
  the app is installed in). Real-world emission patterns: ``.env``
  files (``SLACK_APP_TOKEN=xapp-...``), GitHub Actions secrets dumped
  to logs by a misconfigured action, notebook outputs hardcoding the
  token, Slack SDK error responses echoing the token back in
  diagnostic messages. Revocation flow lives at api.slack.com/apps/
  <app_id>/general ("App-Level Tokens" section > regenerate) and is
  distinct from every other Slack token family's revocation flow
  (xoxb/xoxp = ``oauth.v2.revoke`` API; xoxc/xoxd = slack.com/account/
  sessions; xapp = api.slack.com/apps/<id>/general).

* **Databricks PAT (HIGH blast radius — full workspace data plane +
  job-execution plane)**: a leaked ``dapi`` grants the issuing user's
  full Databricks workspace-scoped API access. Read access = exfiltrate
  EVERY table the user can SELECT (Unity Catalog tables, S3-backed
  Delta tables, Snowflake federation tables — the canonical data-
  warehouse credential class), export entire datasets via
  ``/api/2.0/jobs/runs/export``, exfiltrate notebook source code
  (which routinely embeds further credentials — cloud provider keys,
  database connection strings, third-party API keys). Write access
  = submit arbitrary Spark jobs / SQL queries on the user's
  attached clusters (compute-resource theft, USD 100s-1000s/hour on
  GPU clusters), modify Unity Catalog permissions (with appropriate
  privileges), upload backdoored notebooks to user folders for
  persistence. The cluster-execution capability is the canonical
  "arbitrary code execution within the cloud account" amplifier —
  Databricks clusters run on the customer's AWS/Azure/GCP account,
  giving the cluster the IAM role attached to the cluster (often a
  broad ``DatabricksDataAccess`` role with S3 / Glue / Athena read).
  Real-world emission patterns: ``.env`` files (``DATABRICKS_TOKEN=
  dapi...``), CI/CD pipeline debug logs (``terraform-provider-
  databricks`` echoing the token in plan output), notebook output
  cells displaying ``os.environ`` for debugging, ``databricks-cli``
  ``--profile`` config files committed by mistake. Revocation flow
  lives at Databricks workspace UI > User Settings > Developer >
  Access tokens > "Revoke" — distinct per workspace, distinct from
  every other Databricks credential class (service principals, OAuth
  apps, basic auth).

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])xapp-[0-9]+-[A-Z][A-Z0-9]{8,}-[0-9]+-[a-zA-Z0-9]{32,}(?![A-Za-z0-9])``
  → "Slack App-Level Token gefunden" / mask preserving ``xapp-***`` for
  IR triage (revocation flow at api.slack.com/apps/<app_id>/general
  > "App-Level Tokens" > regenerate).
* ``(?<![A-Za-z0-9])dapi[a-f0-9]{32}(?:-[0-9]+)?(?![A-Za-z0-9])`` →
  "Databricks Personal Access Token gefunden" / mask preserving
  ``dapi***`` for IR triage (revocation flow at Databricks workspace
  UI > User Settings > Developer > Access tokens > "Revoke").

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``Xxapp-...``, ``mydapi...`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``xapp-***``, ``dapi***``) do NOT re-match
the regex (the ``*`` char is OUTSIDE every body alphabet AND the
masked body length 3 chars is below every per-family floor).

Marker: SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content

SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT = (
    "SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT: neither _KNOWN_TOKENS "
    "nor sanitize_log_message detected/masked the Slack App-Level Token "
    "(xapp-<version>-<app_id>-<seq>-<hex>) or Databricks PAT "
    "(dapi<32 hex>) shapes. Bare tokens in committed source AND in "
    "operator log streams (plain text, JSON values with non-sensitive "
    "keys, URL paths, URL query params with non-sensitive names, "
    "exception messages) bypassed every existing detection / masking "
    "branch — or were attributed generically (Hochentropischer "
    "Token-String for Databricks; NO finding at all for Slack xapp- "
    "due to per-segment splitting below the 24-char entropy floor) "
    "losing issuer-specific revocation flow anchoring."
)


def _body_hex(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    strict lowercase-hex ``[a-f0-9]`` alphabet (Databricks' documented body
    alphabet)."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_alnum(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    ``[a-zA-Z0-9]`` alphabet (Slack xapp- body alphabet, mixed-case
    accepted per Slack docs)."""
    chunk = "Aa1Bb2Cc3Dd4Ee5Ff"
    return (chunk * (length // len(chunk) + 1))[:length]


# Slack App-Level Token fixture: ``xapp-1-A0123456789-1234567890123-<64 alnum>``
# mirrors the canonical Slack documented shape (real-world tokens have
# version=1, 11-char app ID starting with A, 13-digit sequence, 64-char
# hex body — total ~92 chars).
_SLACK_XAPP = "xapp-1-A0123456789-1234567890123-" + _body_alnum(64)

# Databricks PAT fixture: ``dapi<32 hex>`` mirrors the canonical
# Databricks documented format. Also test the versioned variant
# ``dapi<32 hex>-2``.
_DATABRICKS = "dapi" + _body_hex(32)
_DATABRICKS_VERSIONED = "dapi" + _body_hex(32) + "-2"


# Sanity-check the fixtures.
assert _SLACK_XAPP.startswith("xapp-1-A0123456789-1234567890123-")
assert len(_SLACK_XAPP) == 33 + 64, (
    f"Slack xapp fixture wrong length: {len(_SLACK_XAPP)}"
)
assert _DATABRICKS.startswith("dapi")
assert len(_DATABRICKS) == 4 + 32, f"Databricks fixture wrong length: {len(_DATABRICKS)}"
assert all(c in "0123456789abcdef" for c in _DATABRICKS[4:]), (
    "Databricks body must be lowercase hex"
)
assert _DATABRICKS_VERSIONED.endswith("-2")


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family with
# vendor-specific attribution (NOT the generic Hochentropie fallback or
# nothing-at-all for the Slack xapp- case).
# ---------------------------------------------------------------------------


def test_drift_premise_scanner_detects_slack_xapp_token() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Slack App-Level
    Token-specific pattern that matches the canonical
    ``xapp-<v>-<app>-<seq>-<hex>`` shape. If a future contributor drops the
    Slack entry this test FAILS first (loud) — preventing silent
    re-drift."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_SLACK_XAPP)
    ]
    assert any("Slack" in r and ("App-Level" in r or "App Level" in r) for r in matched_reasons), (
        f"Drift premise FAILED: Slack App-Level Token {_SLACK_XAPP[:20]!r}... "
        f"is not detected by _KNOWN_TOKENS with Slack-App-Level attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT}"
    )


def test_drift_premise_scanner_detects_databricks_token() -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Databricks-specific
    pattern matching the canonical ``dapi<32 hex>`` shape."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_DATABRICKS)
    ]
    assert any("Databricks" in r for r in matched_reasons), (
        f"Drift premise FAILED: Databricks token {_DATABRICKS!r} is "
        f"not detected by _KNOWN_TOKENS with Databricks attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT}"
    )


def test_drift_premise_scanner_detects_databricks_versioned_token() -> None:
    """Databricks ALSO issues versioned PATs of form ``dapi<hex>-N`` —
    the version suffix MUST be recognised by the same pattern."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(_DATABRICKS_VERSIONED)
    ]
    assert any("Databricks" in r for r in matched_reasons), (
        f"Drift premise FAILED: versioned Databricks token "
        f"{_DATABRICKS_VERSIONED!r} is not detected. "
        f"matched_reasons={matched_reasons}"
    )


def test_slack_xapp_attribution_wins_over_generic_entropy() -> None:
    """The Slack-App-Level-specific attribution MUST win in the arbitration —
    pre-fix the multi-dash format split at every segment boundary into
    fragments below the 24-char floor, so the FULL token was SILENTLY
    UNDETECTED (not even a generic Hochentropie finding fired)."""
    findings = _scan_content(f"audit: {_SLACK_XAPP}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Slack" in r and ("App-Level" in r or "App Level" in r) for r in reasons), (
        f"Slack App-Level attribution lost: {reasons!r}"
    )


def test_databricks_attribution_wins_over_generic_entropy() -> None:
    """The Databricks-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the full ``dapi<body>`` span as
    ``Hochentropischer Token-String``, losing the issuer attribution."""
    findings = _scan_content(f"audit: {_DATABRICKS}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Databricks" in r for r in reasons), (
        f"Databricks attribution lost: {reasons!r}"
    )
    # And the generic high-entropy attribution MUST NOT also fire (the
    # Databricks span covers the entire token, so the entropy fallback
    # should be suppressed via covered_ranges).
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: Databricks token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_slack_xapp_token_in_plain_log_line_is_masked() -> None:
    """Bare Slack App-Level Token in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the ``xapp-***``
    attribution for IR triage."""
    log_line = f"Slack Socket Mode 401: invalid token {_SLACK_XAPP}"
    result = sanitize_log_message(log_line)
    assert _SLACK_XAPP not in result, (
        f"Slack App-Level Token leaked through sanitize_log_message: "
        f"{SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT}"
    )
    assert "xapp-***" in result, (
        "Slack App-Level mask MUST preserve 'xapp-***' attribution for IR "
        "triage (revocation flow at api.slack.com/apps/<app_id>/general)"
    )


def test_databricks_token_in_plain_log_line_is_masked() -> None:
    """Bare Databricks PAT in plain log text MUST be masked. The mask MUST
    preserve the ``dapi***`` attribution for IR triage (revocation flow
    at Databricks workspace UI > User Settings > Developer > Access tokens)."""
    log_line = f"Databricks 403 for token {_DATABRICKS}"
    result = sanitize_log_message(log_line)
    assert _DATABRICKS not in result, (
        "Databricks PAT leaked through sanitize_log_message"
    )
    assert "dapi***" in result, (
        "Databricks mask MUST preserve 'dapi***' attribution for IR triage"
    )


def test_databricks_versioned_token_in_plain_log_line_is_masked() -> None:
    """Versioned Databricks PAT (``dapi<hex>-N``) MUST also be masked."""
    log_line = f"Databricks token rotation: old={_DATABRICKS_VERSIONED}"
    result = sanitize_log_message(log_line)
    assert _DATABRICKS_VERSIONED not in result, (
        "Versioned Databricks PAT leaked through sanitize_log_message"
    )
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_slack_xapp_token_in_json_value_is_masked(key_name: str) -> None:
    """Slack App-Level Token in JSON value with a NON-sensitive key name
    MUST be masked. Pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` / ``message`` and the
    token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_SLACK_XAPP}"}}'
    result = sanitize_log_message(log_line)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_databricks_token_in_json_value_is_masked(key_name: str) -> None:
    """Databricks PAT in JSON value with a NON-sensitive key name MUST be
    masked. Same drift premise as the Slack xapp- JSON test."""
    log_line = f'{{"{key_name}": "{_DATABRICKS}"}}'
    result = sanitize_log_message(log_line)
    assert _DATABRICKS not in result
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_slack_xapp_token_in_url_path_is_masked() -> None:
    """Slack App-Level Token embedded in URL path MUST be masked. Pre-fix
    the URL credential regex required the credential to appear before
    ``@``; path-embedded tokens slipped past."""
    log_line = f"GET /apps/connections/open/{_SLACK_XAPP}/socket HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


def test_databricks_token_in_url_path_is_masked() -> None:
    """Databricks PAT embedded in URL path MUST be masked."""
    log_line = f"GET /api/2.0/clusters/list/{_DATABRICKS}/info HTTP/1.1 403"
    result = sanitize_log_message(log_line)
    assert _DATABRICKS not in result
    assert "dapi***" in result


def test_slack_xapp_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Slack App-Level Token in URL query string with a NON-sensitive
    parameter name (``ref`` / ``commit_sha`` / ``q``) MUST be masked."""
    log_line = f"GET /foo/bar?ref={_SLACK_XAPP} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


def test_databricks_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Databricks PAT in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={_DATABRICKS} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _DATABRICKS not in result
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_slack_xapp_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` (the
    canonical exception-text sanitisation path in ``src/utils/http.py``)
    MUST mask Slack App-Level Tokens."""
    exc_msg = f"HTTPError: 401 — token {_SLACK_XAPP} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


def test_databricks_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask Databricks PATs with full issuer attribution."""
    exc_msg = f"HTTPError: 403 Forbidden — Databricks {_DATABRICKS} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert _DATABRICKS not in result
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_slack_xapp_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_SLACK_XAPP}"
    result = sanitize_log_arg(arg)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


def test_sanitize_log_arg_masks_databricks_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a Databricks PAT MUST
    have the value masked. Uses a NON-sensitive attribute name
    (``audit``) so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_DATABRICKS})"

    result = sanitize_log_arg(_Wrapper())
    assert _DATABRICKS not in result
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong Slack xapp prefix
        "yapp-1-A0123456789-1234567890123-" + _body_alnum(64),
        # Missing app_id segment leading capital A
        "xapp-1-0123456789-1234567890123-" + _body_alnum(64),
        # Body too short
        "xapp-1-A0123456789-1234567890123-" + _body_alnum(20),
        # Mid-identifier collision — lookbehind prevents it
        "X" + "xapp-1-A0123456789-1234567890123-" + _body_alnum(64),
        "0" + "xapp-1-A0123456789-1234567890123-" + _body_alnum(64),
        # ISO 639 / abbreviation collisions: "xapp" inside other context
        # (no full structural format) MUST NOT match.
        "configure xapp settings",
    ],
)
def test_benign_slack_xapp_shape_is_not_masked(benign: str) -> None:
    """Negative case: wrong prefix / missing segments / short bodies /
    mid-identifier collisions / benign English text MUST NOT trigger the
    Slack App-Level mask. The ``(?<![A-Za-z0-9])`` lookbehind + strict
    multi-segment structural format + ``(?![A-Za-z0-9])`` lookahead are
    the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "xapp-***" not in result, (
        f"False-positive Slack App-Level mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Body too short (< 32 hex)
        "dapi" + _body_hex(31),
        # Wrong Databricks prefix
        "dapj" + _body_hex(32),
        # Body contains non-hex char (uppercase) — strict lowercase-hex
        # alphabet rejects it.
        "dapi" + "G" + _body_hex(31),
        # Body contains non-hex char (g-z)
        "dapi" + "g" + _body_hex(31),
        # Mid-identifier collision
        "X" + "dapi" + _body_hex(32),
        "0" + "dapi" + _body_hex(32),
        # English word starting "dapi..." (no body shape)
        "dapibus malesuada",
    ],
)
def test_benign_databricks_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / non-hex body chars /
    mid-identifier collisions / English collisions MUST NOT trigger the
    Databricks mask."""
    result = sanitize_log_message(benign)
    assert "dapi***" not in result, (
        f"False-positive Databricks mask on benign input: {benign!r} → "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "xapp-***",
        "dapi***",
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


def test_slack_xapp_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_SLACK_XAPP}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _SLACK_XAPP not in first


def test_databricks_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_DATABRICKS}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _DATABRICKS not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_slack_xapp_family() -> None:
    """**Sibling-alignment invariant.** Every Slack App-Level Token shape
    that appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``. Any future Slack-App-Level-family pattern
    adjustment to the scanner without a companion log-mask adjustment
    fails this test on the first pytest run after the new scanner entry
    is committed — surfacing the next drift family programmatically."""
    slack_xapp_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Slack" in reason and ("App-Level" in reason or "App Level" in reason)
    ]
    assert len(slack_xapp_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Slack App-Level' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Slack xapp- shape.
    log_line = f"audit: {_SLACK_XAPP}"
    result = sanitize_log_message(log_line)
    assert _SLACK_XAPP not in result
    assert "xapp-***" in result


def test_scanner_and_log_sanitiser_share_databricks_family() -> None:
    """**Sibling-alignment invariant.** Every Databricks token shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by ``sanitize_log_message``."""
    databricks_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Databricks" in reason
    ]
    assert len(databricks_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Databricks' entry in "
        "_KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    log_line = f"audit: {_DATABRICKS}"
    result = sanitize_log_message(log_line)
    assert _DATABRICKS not in result
    assert "dapi***" in result


# ---------------------------------------------------------------------------
# (9) Cross-family disambiguation — Slack xapp- must not match Databricks
# and vice versa.
# ---------------------------------------------------------------------------


def test_slack_xapp_token_not_misattributed_as_databricks() -> None:
    """A Slack App-Level Token is structurally disjoint from a Databricks
    PAT — the ``xapp-`` prefix vs. ``dapi`` prefix are mutually exclusive
    at the leading-char level (``x`` vs. ``d``)."""
    result = sanitize_log_message(f"audit: {_SLACK_XAPP}")
    assert "dapi***" not in result, (
        "Slack App-Level Token misattributed as Databricks mask — "
        "cross-mutex broken"
    )


def test_databricks_token_not_misattributed_as_slack_xapp() -> None:
    """A Databricks PAT is structurally disjoint from a Slack App-Level
    Token."""
    result = sanitize_log_message(f"audit: {_DATABRICKS}")
    assert "xapp-***" not in result, (
        "Databricks PAT misattributed as Slack App-Level mask — "
        "cross-mutex broken"
    )
