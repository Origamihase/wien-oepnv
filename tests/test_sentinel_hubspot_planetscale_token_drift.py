"""Sentinel drift coverage for the HubSpot Private App Token
(``pat-<region>-<UUID>``) + PlanetScale Database Token
(``pscale_(?:oauth|tkn|pw)_<43 base64url chars>``) value-shape detection
(``_KNOWN_TOKENS``) AND log-sanitisation (``sanitize_log_message`` plus the
downstream ``_sanitize_exception_msg`` chain).

After the 2026-05-18 Slack App-Level + Databricks round closed the
workspace event firehose + data-warehouse PAT tier, two more
high-blast-radius vendor families remain SILENTLY UNCOVERED across BOTH
detection codepaths — the secret scanner attributes them generically and
the log sanitiser leaks them verbatim:

* **HubSpot Private App Token (``pat-(?:na1|na2|na3|eu1)-<UUID>``)** —
  the canonical HubSpot Private App access token format used for the
  HubSpot CRM REST API (``/crm/v3/...``, ``/marketing/v3/...``,
  ``/contacts/v1/...``). Issued via the HubSpot UI at Settings → Account
  Setup → Integrations → Private Apps → <App> → Auth tab. Pre-fix the
  body (UUID format with internal ``-`` separators) lies inside the
  entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet — the entropy regex
  matched the full ``pat-<region>-<UUID>`` span as one generic
  ``Hochentropischer Token-String`` finding, losing the HubSpot-specific
  issuer attribution that anchors the per-portal revocation flow (Settings
  → Integrations → Private Apps → <App> → "Rotate" / "Delete app").

* **PlanetScale Database Token (``pscale_(?:oauth|tkn|pw)_<43 chars>``)**
  — the canonical PlanetScale credential format spanning OAuth client
  secrets (``pscale_oauth_``), service tokens / personal access tokens
  (``pscale_tkn_``), and database branch passwords (``pscale_pw_``).
  Issued via the PlanetScale UI at app.planetscale.com/<org>/<db>/<branch>/
  passwords (for ``pw_``) and app.planetscale.com/<org>/settings/service-tokens
  (for ``tkn_``). Pre-fix the body (43-char ``[A-Za-z0-9_-]`` base64url-ish
  alphabet) lies ENTIRELY inside the entropy fallback's alphabet — the
  entropy regex matched the full ``pscale_<type>_<body>`` span as one
  generic ``Hochentropischer Token-String`` finding, losing the
  PlanetScale-specific issuer attribution that anchors the per-database
  revocation flow plus the credential-tier disambiguation
  (OAuth-client-secret vs. service-token vs. DB-password — three
  distinct revocation panels).

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"HubSpot 401: {token}")``)
   leaks verbatim to operator log streams and the public
   ``docs/feed_health.json`` artefact.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "pat-na1-...uuid..."}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /contacts/v1/...?hapikey=pat-na1-<uuid>``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /api/foo?ref=pscale_tkn_<body>``) bypasses the URL-query-param
   sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a HubSpot Private App
   Token or PlanetScale token as a JSON value, README example, or
   .env-like fixture lands in the scanner output as a generic
   ``Hochentropischer Token-String`` finding, with NO per-issuer
   attribution to anchor the operator's revocation playbook.

Blast radius per leaked credential:

* **HubSpot Private App Token (HIGH blast radius — full CRM data plane
  with PII):** a leaked ``pat-`` grants the issuing private app's
  configured OAuth-equivalent scopes against the HubSpot portal. The
  canonical scope set
  (``crm.objects.contacts.read``/``write``, ``crm.objects.companies.``,
  ``crm.objects.deals.``, ``marketing.``, ``automation.``, ``forms.``,
  ``files.``) provides FULL access to: the portal's complete contact
  database (names + emails + phone + addresses + custom properties —
  GDPR-protected PII at scale), every company and deal record
  (B2B revenue data + pipeline forecasts), every marketing email
  campaign (recipient lists + open / click tracking — competitive
  intelligence goldmine), every automation workflow (modify or
  disable triggers — sabotage primitive), every form submission
  (incoming lead capture — exfiltrate or redirect to attacker
  endpoint). Real-world emission patterns: ``.env`` files
  (``HUBSPOT_PRIVATE_APP_TOKEN=pat-na1-...``), CI/CD pipeline debug
  logs, GitHub Actions secrets dumped to logs by a misconfigured
  action, notebook outputs hardcoding ``HubSpot(access_token="pat-na1-...")``,
  curl examples in README files. Revocation flow lives at the HubSpot
  portal UI > Settings > Account Setup > Integrations > Private Apps >
  <App> > Auth tab > "Rotate" (immediate) or "Delete app" (permanent)
  and is distinct per portal — distinct from every other CRM-vendor
  rotation flow (Salesforce, Microsoft Dynamics 365, Zoho CRM).

* **PlanetScale Database Token (HIGH blast radius — DB control plane +
  data plane per-tier):** a leaked ``pscale_<type>_`` grants different
  privileges per tier:
  - ``pscale_oauth_<body>`` — OAuth client secret. Mint user-delegated
    OAuth access tokens against the PlanetScale API. Multi-user pivot
    (the OAuth flow can grant tokens for ANY PlanetScale user who has
    authorized the app — cross-account amplifier).
  - ``pscale_tkn_<body>`` — Service token / Personal Access Token.
    Full PlanetScale API access scoped per the token's configured
    permissions (typically ``connect_production_branch``,
    ``manage_branches``, ``manage_deploy_requests``, ``read_organization``).
    Modify production schemas, exfiltrate every branch's DB password,
    trigger arbitrary deploy requests, delete branches.
  - ``pscale_pw_<body>`` — Database branch password. **HIGHEST data-
    plane blast radius:** direct MySQL-wire-protocol access to the
    database branch (read every table, write every table — full
    customer data exfiltration / ransomware-style overwrite primitive).
    The username is the leaked token's matching service-token name
    OR the branch's auto-generated DB user, both of which are
    typically present in the same ``.env`` file (``DATABASE_URL=
    mysql://pscale_<user>:pscale_pw_<body>@<host>/<db>``).
  Real-world emission patterns: ``.env`` files
  (``PLANETSCALE_TOKEN=pscale_tkn_...``), CI/CD pipeline debug logs
  (Terraform ``planetscale_database`` resource echoing the token in
  plan output), notebook outputs hardcoding the PlanetScale Python
  client constructor, ``pscale`` CLI ``--service-token`` flag in
  CI YAML files. Revocation flow lives at app.planetscale.com >
  organization settings > Service Tokens > "Delete" (for ``tkn_``),
  database branch > Passwords > "Delete" (for ``pw_``), and
  organization settings > OAuth Apps > "Reset secret" (for
  ``oauth_``) — three DISTINCT revocation panels, requiring the
  operator to identify the tier from the prefix.

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])pat-(?:na1|na2|na3|eu1)-[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}(?![A-Za-z0-9])``
  → "HubSpot Private App Token gefunden" / mask preserving ``pat-***``
  for IR triage (revocation flow at HubSpot portal > Settings >
  Integrations > Private Apps > <App> > Auth tab > Rotate).
* ``(?<![A-Za-z0-9])pscale_(?:oauth|tkn|pw)_[A-Za-z0-9_-]{43}(?![A-Za-z0-9])``
  → "PlanetScale Database Token gefunden" / mask preserving
  ``pscale_<tier>_***`` for IR triage (revocation flow at
  app.planetscale.com — distinct per tier).

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``Xpat-na1-...``, ``mypscale_tkn_...`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format reject
  accidental fragments while accepting every real-shape token.

Idempotence: masked forms (``pat-***``, ``pscale_tkn_***``) do NOT
re-match the regex (``*`` is OUTSIDE every body alphabet AND the
masked body length 3 chars is below every per-family floor).

Marker: SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content

SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT = (
    "SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT: neither _KNOWN_TOKENS "
    "nor sanitize_log_message detected/masked the HubSpot Private App "
    "Token (pat-<region>-<UUID>) or PlanetScale Database Token "
    "(pscale_(?:oauth|tkn|pw)_<43>) shapes. Bare tokens in committed "
    "source AND in operator log streams (plain text, JSON values with "
    "non-sensitive keys, URL paths, URL query params with non-sensitive "
    "names, exception messages) bypassed every existing detection / "
    "masking branch — or were attributed generically as "
    "Hochentropischer Token-String, losing issuer-specific revocation "
    "flow anchoring."
)


def _body_hex(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    strict lowercase-hex ``[a-f0-9]`` alphabet (HubSpot UUID body alphabet)."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_b64url(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    ``[A-Za-z0-9_-]`` alphabet (PlanetScale body alphabet)."""
    chunk = "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk-Ll_Mm"
    return (chunk * (length // len(chunk) + 1))[:length]


# HubSpot Private App Token fixture: ``pat-na1-XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX``
# mirrors the canonical HubSpot UUID-shaped body.
def _hubspot_token(region: str = "na1") -> str:
    return (
        f"pat-{region}-"
        f"{_body_hex(8)}-{_body_hex(4)}-{_body_hex(4)}-{_body_hex(4)}-{_body_hex(12)}"
    )


_HUBSPOT_NA1 = _hubspot_token("na1")
_HUBSPOT_NA2 = _hubspot_token("na2")
_HUBSPOT_NA3 = _hubspot_token("na3")
_HUBSPOT_EU1 = _hubspot_token("eu1")

# PlanetScale Database Token fixtures per tier.
_PSCALE_OAUTH = "pscale_oauth_" + _body_b64url(43)
_PSCALE_TKN = "pscale_tkn_" + _body_b64url(43)
_PSCALE_PW = "pscale_pw_" + _body_b64url(43)


# Sanity-check the fixtures.
assert _HUBSPOT_NA1.startswith("pat-na1-")
assert len(_HUBSPOT_NA1) == len("pat-na1-") + 8 + 1 + 4 + 1 + 4 + 1 + 4 + 1 + 12, (
    f"HubSpot fixture wrong length: {len(_HUBSPOT_NA1)}"
)
assert _PSCALE_OAUTH.startswith("pscale_oauth_")
assert len(_PSCALE_OAUTH) == len("pscale_oauth_") + 43, (
    f"PlanetScale OAuth fixture wrong length: {len(_PSCALE_OAUTH)}"
)
assert _PSCALE_TKN.startswith("pscale_tkn_")
assert _PSCALE_PW.startswith("pscale_pw_")


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family with
# vendor-specific attribution (NOT the generic Hochentropie fallback).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [_HUBSPOT_NA1, _HUBSPOT_NA2, _HUBSPOT_NA3, _HUBSPOT_EU1],
)
def test_drift_premise_scanner_detects_hubspot_token(token: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a HubSpot-specific
    pattern that matches the canonical ``pat-<region>-<UUID>`` shape across
    every documented region (``na1``/``na2``/``na3``/``eu1``)."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("HubSpot" in r for r in matched_reasons), (
        f"Drift premise FAILED: HubSpot token {token!r} is not detected "
        f"by _KNOWN_TOKENS with HubSpot attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT}"
    )


@pytest.mark.parametrize("token", [_PSCALE_OAUTH, _PSCALE_TKN, _PSCALE_PW])
def test_drift_premise_scanner_detects_planetscale_token(token: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a PlanetScale-specific
    pattern matching the canonical ``pscale_(?:oauth|tkn|pw)_<43>`` shape
    across every documented tier."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("PlanetScale" in r for r in matched_reasons), (
        f"Drift premise FAILED: PlanetScale token {token!r} is "
        f"not detected by _KNOWN_TOKENS with PlanetScale attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT}"
    )


def test_hubspot_attribution_wins_over_generic_entropy() -> None:
    """The HubSpot-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the full ``pat-<region>-<UUID>``
    span as ``Hochentropischer Token-String``, losing the HubSpot-specific
    issuer attribution."""
    findings = _scan_content(f"audit: {_HUBSPOT_NA1}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("HubSpot" in r for r in reasons), (
        f"HubSpot attribution lost: {reasons!r}"
    )
    # And the generic high-entropy attribution MUST NOT also fire (the
    # HubSpot span covers the entire token, so the entropy fallback
    # should be suppressed via covered_ranges).
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: HubSpot token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


def test_planetscale_attribution_wins_over_generic_entropy() -> None:
    """The PlanetScale-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the full ``pscale_<tier>_<body>``
    span as ``Hochentropischer Token-String``, losing the issuer
    attribution."""
    findings = _scan_content(f"audit: {_PSCALE_TKN}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("PlanetScale" in r for r in reasons), (
        f"PlanetScale attribution lost: {reasons!r}"
    )
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: PlanetScale token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_hubspot_token_in_plain_log_line_is_masked() -> None:
    """Bare HubSpot Private App Token in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the ``pat-***``
    attribution for IR triage."""
    log_line = f"HubSpot 401: invalid token {_HUBSPOT_NA1}"
    result = sanitize_log_message(log_line)
    assert _HUBSPOT_NA1 not in result, (
        f"HubSpot Private App Token leaked through sanitize_log_message: "
        f"{SENTINEL_HUBSPOT_PLANETSCALE_TOKEN_DRIFT}"
    )
    assert "pat-***" in result, (
        "HubSpot mask MUST preserve 'pat-***' attribution for IR triage "
        "(revocation flow at HubSpot portal > Settings > Integrations > "
        "Private Apps > <App> > Auth tab > Rotate)"
    )


def test_planetscale_token_in_plain_log_line_is_masked() -> None:
    """Bare PlanetScale token in plain log text MUST be masked. The mask
    MUST preserve the ``pscale_<tier>_***`` attribution for IR triage."""
    log_line = f"PlanetScale 403 for token {_PSCALE_TKN}"
    result = sanitize_log_message(log_line)
    assert _PSCALE_TKN not in result, (
        "PlanetScale token leaked through sanitize_log_message"
    )
    assert "pscale_tkn_***" in result, (
        "PlanetScale mask MUST preserve 'pscale_tkn_***' tier-specific "
        "attribution for IR triage (revocation flow at app.planetscale.com "
        "> organization settings > Service Tokens > Delete)"
    )


@pytest.mark.parametrize(
    ("token", "expected_mask"),
    [
        (_PSCALE_OAUTH, "pscale_oauth_***"),
        (_PSCALE_TKN, "pscale_tkn_***"),
        (_PSCALE_PW, "pscale_pw_***"),
    ],
)
def test_planetscale_tier_specific_mask(token: str, expected_mask: str) -> None:
    """Per-tier PlanetScale mask MUST preserve the tier-specific prefix
    so IR triage can identify which revocation panel to use:
    ``pscale_oauth_*`` (OAuth Apps panel), ``pscale_tkn_*`` (Service
    Tokens panel), ``pscale_pw_*`` (Database Branch Passwords panel)."""
    log_line = f"audit: {token}"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert expected_mask in result, (
        f"Tier-specific mask {expected_mask!r} missing — IR triage "
        f"cannot identify which PlanetScale revocation panel to use. "
        f"result={result!r}"
    )


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_hubspot_token_in_json_value_is_masked(key_name: str) -> None:
    """HubSpot Private App Token in JSON value with a NON-sensitive key
    name MUST be masked. Pre-fix the JSON-key sensitive-name regex missed
    keys like ``data`` / ``payload`` / ``response_body`` / ``message`` and
    the token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_HUBSPOT_NA1}"}}'
    result = sanitize_log_message(log_line)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_planetscale_token_in_json_value_is_masked(key_name: str) -> None:
    """PlanetScale token in JSON value with a NON-sensitive key name MUST
    be masked. Same drift premise as the HubSpot JSON test."""
    log_line = f'{{"{key_name}": "{_PSCALE_TKN}"}}'
    result = sanitize_log_message(log_line)
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_hubspot_token_in_url_path_is_masked() -> None:
    """HubSpot Private App Token embedded in URL path MUST be masked.
    Pre-fix the URL credential regex required the credential to appear
    before ``@``; path-embedded tokens slipped past."""
    log_line = f"GET /crm/v3/objects/contacts/{_HUBSPOT_NA1}/profile HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


def test_planetscale_token_in_url_path_is_masked() -> None:
    """PlanetScale token embedded in URL path MUST be masked."""
    log_line = f"GET /v1/organizations/foo/databases/bar/branches/{_PSCALE_TKN}/info HTTP/1.1 403"
    result = sanitize_log_message(log_line)
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


def test_hubspot_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """HubSpot Private App Token in URL query string with a NON-sensitive
    parameter name (``ref`` / ``commit_sha`` / ``q``) MUST be masked."""
    log_line = f"GET /foo/bar?ref={_HUBSPOT_NA1} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


def test_planetscale_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """PlanetScale token in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={_PSCALE_TKN} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_hubspot_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` (the
    canonical exception-text sanitisation path in ``src/utils/http.py``)
    MUST mask HubSpot Private App Tokens."""
    exc_msg = f"HTTPError: 401 — token {_HUBSPOT_NA1} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


def test_planetscale_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask PlanetScale tokens with full issuer + tier attribution."""
    exc_msg = f"HTTPError: 403 Forbidden — PlanetScale {_PSCALE_TKN} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_hubspot_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_HUBSPOT_NA1}"
    result = sanitize_log_arg(arg)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


def test_sanitize_log_arg_masks_planetscale_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a PlanetScale token MUST
    have the value masked. Uses a NON-sensitive attribute name
    (``audit``) so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_PSCALE_TKN})"

    result = sanitize_log_arg(_Wrapper())
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong HubSpot prefix
        "qat-na1-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(12),
        # Wrong region
        "pat-xy9-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(12),
        # Missing UUID segments
        "pat-na1-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4),
        # Body too short in last segment
        "pat-na1-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(11),
        # Non-hex body (uppercase G)
        "pat-na1-" + "G" + _body_hex(7) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(12),
        # Mid-identifier collision
        "X" + "pat-na1-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(12),
        "0" + "pat-na1-" + _body_hex(8) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(4) + "-" + _body_hex(12),
        # English collisions
        "configure pat-na1 settings",
        "the pat-na1 token format is documented at",
    ],
)
def test_benign_hubspot_shape_is_not_masked(benign: str) -> None:
    """Negative case: wrong prefix / wrong region / missing segments /
    short bodies / non-hex chars / mid-identifier collisions / benign
    English text MUST NOT trigger the HubSpot mask. The
    ``(?<![A-Za-z0-9])`` lookbehind + strict region alternation +
    strict UUID format + ``(?![A-Za-z0-9])`` lookahead are the
    structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "pat-***" not in result, (
        f"False-positive HubSpot mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Body too short (< 43 chars)
        "pscale_tkn_" + _body_b64url(42),
        # Wrong PlanetScale prefix
        "pscalex_tkn_" + _body_b64url(43),
        # Wrong tier
        "pscale_foo_" + _body_b64url(43),
        # Mid-identifier collision
        "X" + "pscale_tkn_" + _body_b64url(43),
        "0" + "pscale_tkn_" + _body_b64url(43),
        # English collisions
        "configure pscale_tkn settings",
        "the pscale CLI accepts a service token argument",
    ],
)
def test_benign_planetscale_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / wrong tier /
    mid-identifier collisions / English collisions MUST NOT trigger
    the PlanetScale mask."""
    result = sanitize_log_message(benign)
    assert "pscale_oauth_***" not in result
    assert "pscale_tkn_***" not in result
    assert "pscale_pw_***" not in result, (
        f"False-positive PlanetScale mask on benign input: {benign!r} → "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "pat-***",
        "pscale_oauth_***",
        "pscale_tkn_***",
        "pscale_pw_***",
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


def test_hubspot_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_HUBSPOT_NA1}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _HUBSPOT_NA1 not in first


def test_planetscale_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_PSCALE_TKN}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _PSCALE_TKN not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_hubspot_family() -> None:
    """**Sibling-alignment invariant.** Every HubSpot Private App Token
    shape that appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``. Any future HubSpot-family pattern adjustment
    to the scanner without a companion log-mask adjustment fails this test
    on the first pytest run after the new scanner entry is committed —
    surfacing the next drift family programmatically."""
    hubspot_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "HubSpot" in reason
    ]
    assert len(hubspot_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'HubSpot' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical HubSpot shape.
    log_line = f"audit: {_HUBSPOT_NA1}"
    result = sanitize_log_message(log_line)
    assert _HUBSPOT_NA1 not in result
    assert "pat-***" in result


def test_scanner_and_log_sanitiser_share_planetscale_family() -> None:
    """**Sibling-alignment invariant.** Every PlanetScale token shape
    that appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``."""
    planetscale_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "PlanetScale" in reason
    ]
    assert len(planetscale_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'PlanetScale' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    log_line = f"audit: {_PSCALE_TKN}"
    result = sanitize_log_message(log_line)
    assert _PSCALE_TKN not in result
    assert "pscale_tkn_***" in result


# ---------------------------------------------------------------------------
# (9) Cross-family disambiguation — HubSpot tokens must not match
# PlanetScale and vice versa.
# ---------------------------------------------------------------------------


def test_hubspot_token_not_misattributed_as_planetscale() -> None:
    """A HubSpot Private App Token is structurally disjoint from a
    PlanetScale token — the ``pat-`` prefix vs. ``pscale_`` prefix are
    mutually exclusive at the leading-char level (``pa`` vs. ``ps``)."""
    result = sanitize_log_message(f"audit: {_HUBSPOT_NA1}")
    assert "pscale_oauth_***" not in result
    assert "pscale_tkn_***" not in result
    assert "pscale_pw_***" not in result, (
        "HubSpot token misattributed as PlanetScale mask — cross-mutex "
        "broken"
    )


def test_planetscale_token_not_misattributed_as_hubspot() -> None:
    """A PlanetScale token is structurally disjoint from a HubSpot
    Private App Token."""
    result = sanitize_log_message(f"audit: {_PSCALE_TKN}")
    assert "pat-***" not in result, (
        "PlanetScale token misattributed as HubSpot mask — cross-mutex "
        "broken"
    )
