"""Sentinel drift coverage for the Heroku Platform API Token
(``HRKU-<base64url body>``) + Docker Hub Personal Access Token
(``dckr_pat_<base64url body>``) value-shape detection (``_KNOWN_TOKENS``)
AND log-sanitisation (``sanitize_log_message`` plus the downstream
``_sanitize_exception_msg`` chain).

After the 2026-05-18 HubSpot Private App + PlanetScale Database Token round
closed the CRM-data-plane + DB-control/data-plane tier, two more
high-blast-radius vendor families remain SILENTLY UNCOVERED across BOTH
detection codepaths — the secret scanner attributes them generically as
``Hochentropischer Token-String`` and the log sanitiser leaks them
verbatim:

* **Heroku Platform API Token (``HRKU-<base64url 36+ body>``)** — the
  canonical Heroku Platform API authorization token format issued
  post-March 2023 in response to the heroku.com OAuth incident. Issued via
  the Heroku CLI (``heroku authorizations:create``) and the Heroku
  Dashboard at dashboard.heroku.com/account/applications. Used for the
  Heroku Platform API (``api.heroku.com/apps/...``, ``api.heroku.com/
  account/...``, ``api.heroku.com/teams/...``) for full app / dyno /
  config-var / Heroku Postgres / Heroku Redis control-plane access.
  Pre-fix the body alphabet (``[A-Za-z0-9_-]`` for the modern HRKU-
  prefixed format) lies ENTIRELY inside the entropy fallback's
  ``[A-Za-z0-9+/=_-]`` alphabet — the entropy regex matched the full
  ``HRKU-<body>`` span as one generic ``Hochentropischer Token-String``
  finding, losing the Heroku-specific issuer attribution that anchors
  the per-account revocation flow (dashboard.heroku.com/account/
  applications > "Revoke" or ``heroku authorizations:revoke <id>``).

* **Docker Hub Personal Access Token (``dckr_pat_<base64url 27+ body>``)**
  — the canonical Docker Hub PAT format used for Docker registry
  authentication (``docker login`` with the PAT as the password). Issued
  via the Docker Hub UI at hub.docker.com/settings/security for full
  user-scoped registry access (push / pull / delete repositories the
  user owns, list every private repository under the user's namespace).
  Pre-fix the body alphabet (``[A-Za-z0-9_-]``) lies ENTIRELY inside the
  entropy fallback's alphabet — the entropy regex matched the full
  ``dckr_pat_<body>`` span as one generic ``Hochentropischer
  Token-String`` finding, losing the Docker-Hub-specific issuer
  attribution that anchors the per-user revocation flow (hub.docker.com/
  settings/security > "Delete" — distinct from every other Docker /
  container-registry vendor's revocation flow including
  ``ghcr.io`` GitHub Container Registry, AWS ECR public, GitLab
  Container Registry).

Drift threat model
==================

For BOTH vendors, pre-fix every bare token shape in:

1. **Plain application f-string logs** (``log.warning(f"Heroku 401: {token}")``)
   leaks verbatim to operator log streams and the public
   ``docs/feed_health.json`` artefact.
2. **JSON values with non-sensitive key names**
   (``{"response_body": "HRKU-AABBCCDD-..."}``) bypasses the
   ``sanitize_log_message`` JSON-key sensitive-name regex because the
   key is ``response_body`` / ``data`` / ``payload``.
3. **URL paths embedding the token**
   (``GET /apps/HRKU-AABBCCDD-.../config``) bypasses the
   ``Basic-Auth-in-URL`` regex (which requires ``user:pass@``).
4. **URL query strings with NON-sensitive parameter names**
   (``GET /v2/library/dckr_pat_.../manifests/latest``) bypasses the
   URL-query-param sensitive-key set.
5. **Exception messages** routed through ``_sanitize_exception_msg``
   leak the token in the non-HTTP-URL fallback span that
   ``sanitize_log_message`` processes.
6. **The committed-source detection codepath** — a hostile PR / mass
   data leak / compromised CI runner committing a Heroku auth token
   or Docker Hub PAT as a JSON value, README example, or .env-like
   fixture lands in the scanner output as a generic
   ``Hochentropischer Token-String`` finding, with NO per-issuer
   attribution to anchor the operator's revocation playbook.

Blast radius per leaked credential:

* **Heroku Platform API Token (HIGH blast radius — full PaaS control
  plane with adjacent data-plane access via add-ons):** a leaked
  ``HRKU-`` grants the issuing user/authorization's full Heroku
  Platform API scope. Read access = enumerate every app the user has
  access to (across personal account and every Heroku Team they
  collaborate on), dump every app's config vars (which routinely embed
  further credentials — ``DATABASE_URL`` for Heroku Postgres with the
  PostgreSQL connection string including the password, ``REDIS_URL``
  for Heroku Redis with the auth token, ``SENDGRID_API_KEY`` /
  ``STRIPE_SECRET_KEY`` / etc. for every third-party add-on the app
  uses — the canonical "one-credential-leak-cascades-to-many" amplifier).
  Write access = arbitrary code execution via ``heroku run`` against
  any dyno (canonical "shell on the production server" primitive),
  modify app config vars (overwrite ``DATABASE_URL`` to redirect every
  app instance to an attacker-controlled DB for credential interception),
  release new app code via ``heroku releases:rollback`` or new slug
  uploads (supply-chain compromise of the production app), scale up /
  down dynos (DoS or cost-amplification attack). The Heroku Postgres
  add-on access amplifier is the canonical "platform credential opens
  every customer DB" pattern — for production-tier Heroku apps the
  database typically contains the canonical customer record table.
  Real-world emission patterns: ``.env`` files (``HEROKU_API_KEY=HRKU-
  ...``), ``~/.netrc`` files committed by accident, CI/CD pipeline
  debug logs (``heroku-cli`` debug output echoing the token in plan
  output), GitHub Actions secrets dumped to logs by a misconfigured
  action, ``Procfile`` / ``app.json`` examples in README files
  hardcoding the token. Revocation flow lives at the Heroku Dashboard >
  Account Settings > Applications > "Revoke" (per-authorization) or
  via the CLI ``heroku authorizations:revoke <id>``.

* **Docker Hub Personal Access Token (HIGH blast radius — supply-chain
  compromise primitive):** a leaked ``dckr_pat_`` grants the issuing
  user's full Docker Hub scope per the token's configured permissions.
  Read access = pull every private image in the user's namespace
  (potentially containing baked-in credentials, proprietary source
  code in container layers, internal-only infrastructure topology
  encoded in image labels). Write access = push backdoored images to
  ANY repository under the user's namespace under any tag — the
  canonical "supply-chain compromise" primitive. Every downstream
  consumer pulling ``user/image:latest`` (CI/CD pipelines,
  Kubernetes deployments using ``imagePullPolicy: Always``,
  ``docker-compose`` setups with no pinned digest) pulls the
  backdoored image. The blast-radius amplifier is the cascade: Docker
  Hub is a top-3 public registry and base images frequently get
  reused across many projects, so a compromised base image with
  millions of weekly pulls cascades to every downstream consumer.
  Real-world emission patterns: ``.env`` files
  (``DOCKER_HUB_TOKEN=dckr_pat_...``), CI/CD pipeline YAML
  (``docker login -u $USER -p $DOCKER_HUB_TOKEN`` echoed in debug
  logs), ``~/.docker/config.json`` files committed by mistake (the
  ``auths`` block embeds the base64-encoded ``user:dckr_pat_<body>``
  string), GitHub Actions secrets dumped to logs by a misconfigured
  action, ``docker buildx`` debug output echoing the token in the
  registry-login phase, notebook outputs running ``docker push``
  with the token in plain text. Revocation flow lives at the Docker
  Hub UI > Account Settings > Security > Access Tokens > "Delete" —
  distinct per user, distinct from every other container-registry
  vendor's revocation flow (GitHub Container Registry uses GitHub
  PATs with ``write:packages`` scope; AWS ECR uses IAM credentials;
  GitLab Container Registry uses GitLab PATs).

Fix
===

Add two new regex entries to ``src/utils/secret_scanner.py:_KNOWN_TOKENS``
and ``src/utils/logging.py:sanitize_log_message`` patterns, mirroring the
structural anchors of every prior per-issuer round:

* ``(?<![A-Za-z0-9])HRKU-[A-Za-z0-9_\\-]{36,}(?![A-Za-z0-9])``
  → "Heroku Platform API Token gefunden" / mask preserving ``HRKU-***``
  for IR triage (revocation flow at dashboard.heroku.com/account/
  applications or via ``heroku authorizations:revoke <id>``).
* ``(?<![A-Za-z0-9])dckr_pat_[A-Za-z0-9_\\-]{27,}(?![A-Za-z0-9])``
  → "Docker Hub Personal Access Token gefunden" / mask preserving
  ``dckr_pat_***`` for IR triage (revocation flow at hub.docker.com/
  settings/security > "Delete").

Structural anchors mirror every previous per-issuer round exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``XHRKU-...``, ``mydckr_pat_...`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body length floors (36 / 27 chars) per vendor canonical
  format reject accidental fragments while accepting every real-shape
  token.

Idempotence: masked forms (``HRKU-***``, ``dckr_pat_***``) do NOT
re-match the regex (``*`` is OUTSIDE every body alphabet AND the
masked body length 3 chars is below every per-family floor).

Marker: SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS, _scan_content

SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT = (
    "SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT: neither _KNOWN_TOKENS nor "
    "sanitize_log_message detected/masked the Heroku Platform API Token "
    "(HRKU-<body>) or Docker Hub Personal Access Token (dckr_pat_<body>) "
    "shapes. Bare tokens in committed source AND in operator log streams "
    "(plain text, JSON values with non-sensitive keys, URL paths, URL "
    "query params with non-sensitive names, exception messages) bypassed "
    "every existing detection / masking branch — or were attributed "
    "generically as Hochentropischer Token-String, losing issuer-specific "
    "revocation flow anchoring."
)


def _body_b64url(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars from the
    ``[A-Za-z0-9_-]`` alphabet (Heroku + Docker Hub body alphabet)."""
    chunk = "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk-Ll_Mm"
    return (chunk * (length // len(chunk) + 1))[:length]


# Heroku Platform API Token fixtures. Two shapes representative of the
# documented format space: UUID-shape (``HRKU-`` + 32 hex + 4 dashes =
# 36-char body) and base64-shape (``HRKU-`` + 43 base64url chars). Both
# lie inside the modern Heroku canonical format envelope.
_HEROKU_UUID = "HRKU-AABBCCDD-1234-5678-9ABC-DEF012345678"
_HEROKU_B64 = "HRKU-" + _body_b64url(43)

# Docker Hub PAT fixtures.
_DOCKER_PAT_SHORT = "dckr_pat_" + _body_b64url(27)
_DOCKER_PAT_LONG = "dckr_pat_" + _body_b64url(36)


# Sanity-check the fixtures.
assert _HEROKU_UUID.startswith("HRKU-")
assert len(_HEROKU_UUID) == len("HRKU-") + 32 + 4, (
    f"Heroku UUID fixture wrong length: {len(_HEROKU_UUID)}"
)
assert _HEROKU_B64.startswith("HRKU-")
assert _DOCKER_PAT_SHORT.startswith("dckr_pat_")
assert _DOCKER_PAT_LONG.startswith("dckr_pat_")


# ---------------------------------------------------------------------------
# (0) Drift premise — the scanner MUST detect each new token family with
# vendor-specific attribution (NOT the generic Hochentropie fallback).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [_HEROKU_UUID, _HEROKU_B64])
def test_drift_premise_scanner_detects_heroku_token(token: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Heroku-specific
    pattern that matches the canonical ``HRKU-<body>`` shape across both
    UUID-shape and base64-shape body formats."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("Heroku" in r for r in matched_reasons), (
        f"Drift premise FAILED: Heroku token {token!r} is not detected "
        f"by _KNOWN_TOKENS with Heroku attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT}"
    )


@pytest.mark.parametrize("token", [_DOCKER_PAT_SHORT, _DOCKER_PAT_LONG])
def test_drift_premise_scanner_detects_docker_hub_token(token: str) -> None:
    """The scanner ``_KNOWN_TOKENS`` table MUST include a Docker-Hub-specific
    pattern matching the canonical ``dckr_pat_<body>`` shape."""
    matched_reasons = [
        reason
        for regex, reason in _KNOWN_TOKENS
        if regex.search(token)
    ]
    assert any("Docker Hub" in r for r in matched_reasons), (
        f"Drift premise FAILED: Docker Hub PAT {token!r} is not detected "
        f"by _KNOWN_TOKENS with Docker Hub attribution. "
        f"matched_reasons={matched_reasons}. "
        f"{SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT}"
    )


def test_heroku_attribution_wins_over_generic_entropy() -> None:
    """The Heroku-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the full ``HRKU-<body>`` span as
    ``Hochentropischer Token-String``, losing the Heroku-specific
    issuer attribution."""
    findings = _scan_content(f"audit: {_HEROKU_UUID}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Heroku" in r for r in reasons), (
        f"Heroku attribution lost: {reasons!r}"
    )
    # And the generic high-entropy attribution MUST NOT also fire (the
    # Heroku span covers the entire token, so the entropy fallback
    # should be suppressed via covered_ranges).
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: Heroku token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


def test_docker_hub_attribution_wins_over_generic_entropy() -> None:
    """The Docker-Hub-specific attribution MUST win in the arbitration —
    pre-fix the entropy fallback caught the full ``dckr_pat_<body>`` span
    as ``Hochentropischer Token-String``, losing the issuer attribution."""
    findings = _scan_content(f"audit: {_DOCKER_PAT_LONG}\n")
    reasons = [reason for _, _, reason in findings]
    assert any("Docker Hub" in r for r in reasons), (
        f"Docker Hub attribution lost: {reasons!r}"
    )
    assert not any("Hochentropischer" in r for r in reasons), (
        f"Cross-attribution drift: Docker Hub token was ALSO attributed "
        f"as generic high-entropy — _KNOWN_TOKENS ordering or arbitration "
        f"is wrong. reasons={reasons}"
    )


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


def test_heroku_token_in_plain_log_line_is_masked() -> None:
    """Bare Heroku Platform API Token in plain log text MUST be masked by
    ``sanitize_log_message`` and the mask MUST preserve the ``HRKU-***``
    attribution for IR triage."""
    log_line = f"Heroku 401: invalid token {_HEROKU_UUID}"
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result, (
        f"Heroku Platform API Token leaked through sanitize_log_message: "
        f"{SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT}"
    )
    assert "HRKU-***" in result, (
        "Heroku mask MUST preserve 'HRKU-***' attribution for IR triage "
        "(revocation flow at dashboard.heroku.com/account/applications or "
        "via heroku authorizations:revoke <id>)"
    )


def test_docker_hub_token_in_plain_log_line_is_masked() -> None:
    """Bare Docker Hub PAT in plain log text MUST be masked. The mask MUST
    preserve the ``dckr_pat_***`` attribution for IR triage."""
    log_line = f"docker push failed: {_DOCKER_PAT_LONG}"
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result, (
        "Docker Hub PAT leaked through sanitize_log_message"
    )
    assert "dckr_pat_***" in result, (
        "Docker Hub mask MUST preserve 'dckr_pat_***' attribution for IR "
        "triage (revocation flow at hub.docker.com/settings/security > "
        "Delete)"
    )


# ---------------------------------------------------------------------------
# (2) JSON values with non-sensitive key names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_heroku_token_in_json_value_is_masked(key_name: str) -> None:
    """Heroku Platform API Token in JSON value with a NON-sensitive key name
    MUST be masked. Pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` / ``message`` and the
    token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{_HEROKU_UUID}"}}'
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_docker_hub_token_in_json_value_is_masked(key_name: str) -> None:
    """Docker Hub PAT in JSON value with a NON-sensitive key name MUST be
    masked. Same drift premise as the Heroku JSON test."""
    log_line = f'{{"{key_name}": "{_DOCKER_PAT_LONG}"}}'
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (3) URL path embedding the token
# ---------------------------------------------------------------------------


def test_heroku_token_in_url_path_is_masked() -> None:
    """Heroku Platform API Token embedded in URL path MUST be masked.
    Pre-fix the URL credential regex required the credential to appear
    before ``@``; path-embedded tokens slipped past."""
    log_line = f"GET /apps/{_HEROKU_UUID}/config-vars HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_docker_hub_token_in_url_path_is_masked() -> None:
    """Docker Hub PAT embedded in URL path MUST be masked."""
    log_line = f"GET /v2/library/{_DOCKER_PAT_LONG}/manifests/latest HTTP/1.1 401"
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


def test_heroku_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Heroku Platform API Token in URL query string with a NON-sensitive
    parameter name (``ref`` / ``commit_sha`` / ``q``) MUST be masked."""
    log_line = f"GET /foo/bar?ref={_HEROKU_UUID} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_docker_hub_token_in_url_query_with_non_sensitive_param_is_masked() -> None:
    """Docker Hub PAT in URL query string with a NON-sensitive parameter
    name MUST be masked."""
    log_line = f"GET /foo/bar?ref={_DOCKER_PAT_LONG} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


def test_heroku_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` (the
    canonical exception-text sanitisation path in ``src/utils/http.py``)
    MUST mask Heroku Platform API Tokens."""
    exc_msg = f"HTTPError: 401 — token {_HEROKU_UUID} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_docker_hub_token_through_sanitize_exception_msg() -> None:
    """Exception messages routed through ``_sanitize_exception_msg`` MUST
    mask Docker Hub PATs with full issuer attribution."""
    exc_msg = f"HTTPError: 401 — Docker Hub {_DOCKER_PAT_LONG} rejected"
    result = _sanitize_exception_msg(exc_msg)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_heroku_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_HEROKU_UUID}"
    result = sanitize_log_arg(arg)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_sanitize_log_arg_masks_docker_hub_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation —
    a custom object whose ``__str__`` contains a Docker Hub PAT MUST have
    the value masked. Uses a NON-sensitive attribute name (``audit``)
    so the value-shape mask is the primary defence."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_DOCKER_PAT_LONG})"

    result = sanitize_log_arg(_Wrapper())
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Wrong Heroku prefix (case matters — lowercase variant should not
        # match because HRKU- is documented as uppercase by Heroku)
        "hrku-AABBCCDD-1234-5678-9ABC-DEF012345678",
        # Wrong prefix entirely
        "XRKU-AABBCCDD-1234-5678-9ABC-DEF012345678",
        # Body too short (< 36 chars)
        "HRKU-AABBCCDD",
        "HRKU-" + _body_b64url(35),
        # Mid-identifier collision
        "X" + "HRKU-" + _body_b64url(40),
        "0" + "HRKU-" + _body_b64url(40),
        # English collisions
        "configure HRKU- prefix tokens",
        "the HRKU- token format is documented at",
    ],
)
def test_benign_heroku_shape_is_not_masked(benign: str) -> None:
    """Negative case: wrong case / wrong prefix / short bodies /
    mid-identifier collisions / benign English text MUST NOT trigger
    the Heroku mask. The ``(?<![A-Za-z0-9])`` lookbehind + literal
    ``HRKU-`` prefix + length floor + ``(?![A-Za-z0-9])`` lookahead
    are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert "HRKU-***" not in result, (
        f"False-positive Heroku mask on benign input: {benign!r} → "
        f"{result!r}"
    )


@pytest.mark.parametrize(
    "benign",
    [
        # Body too short (< 27 chars)
        "dckr_pat_" + _body_b64url(26),
        # Wrong Docker prefix
        "dckr_xat_" + _body_b64url(36),
        "Xdckr_pat_" + _body_b64url(36),
        # Mid-identifier collision
        "X" + "dckr_pat_" + _body_b64url(36),
        "0" + "dckr_pat_" + _body_b64url(36),
        # English collisions
        "configure dckr_pat settings",
        "the dckr_pat token format is documented at",
    ],
)
def test_benign_docker_hub_shape_is_not_masked(benign: str) -> None:
    """Negative case: short bodies / wrong prefix / mid-identifier
    collisions / English collisions MUST NOT trigger the Docker Hub
    mask."""
    result = sanitize_log_message(benign)
    assert "dckr_pat_***" not in result, (
        f"False-positive Docker Hub mask on benign input: {benign!r} → "
        f"{result!r}"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "masked",
    [
        "HRKU-***",
        "dckr_pat_***",
    ],
)
def test_token_mask_is_idempotent(masked: str) -> None:
    """Running ``sanitize_log_message`` twice MUST be idempotent — the
    masked form MUST NOT itself match the corresponding regex (the ``*``
    char is outside every body alphabet AND the masked body length 3 chars
    is below every per-family floor)."""
    log_line = f"prior IR note: token redacted as {masked}"
    result = sanitize_log_message(log_line)
    assert masked in result, (
        f"Idempotence broken: masked form {masked!r} was further modified "
        f"by sanitize_log_message: {result!r}"
    )


def test_heroku_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_HEROKU_UUID}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _HEROKU_UUID not in first


def test_docker_hub_token_double_sanitize_is_stable() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output."""
    log_line = f"Failed: {_DOCKER_PAT_LONG}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _DOCKER_PAT_LONG not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — scanner & log mask agree per-family
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_heroku_family() -> None:
    """**Sibling-alignment invariant.** Every Heroku Platform API Token
    shape that appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``. Any future Heroku-family pattern adjustment
    to the scanner without a companion log-mask adjustment fails this test
    on the first pytest run after the new scanner entry is committed —
    surfacing the next drift family programmatically."""
    heroku_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Heroku" in reason
    ]
    assert len(heroku_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Heroku' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    # Confirm sanitize_log_message masks the canonical Heroku shape.
    log_line = f"audit: {_HEROKU_UUID}"
    result = sanitize_log_message(log_line)
    assert _HEROKU_UUID not in result
    assert "HRKU-***" in result


def test_scanner_and_log_sanitiser_share_docker_hub_family() -> None:
    """**Sibling-alignment invariant.** Every Docker Hub PAT shape that
    appears in ``_KNOWN_TOKENS`` MUST be masked by
    ``sanitize_log_message``."""
    docker_entries = [
        regex.pattern
        for regex, reason in _KNOWN_TOKENS
        if "Docker Hub" in reason
    ]
    assert len(docker_entries) >= 1, (
        "Sibling-alignment broken: scanner has no 'Docker Hub' entry "
        "in _KNOWN_TOKENS but the log mask depends on the scanner family"
    )
    log_line = f"audit: {_DOCKER_PAT_LONG}"
    result = sanitize_log_message(log_line)
    assert _DOCKER_PAT_LONG not in result
    assert "dckr_pat_***" in result


# ---------------------------------------------------------------------------
# (9) Cross-vendor boundary — no collision with other token families.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vendor_token,vendor_reason",
    [
        ("ghp_" + "A" * 36, "GitHub Personal Access Token gefunden"),
        ("glpat-" + "A" * 20, "GitLab Personal Access Token gefunden"),
        ("sk_live_" + "L" * 24, "Stripe Live Secret Key gefunden"),
        ("AIza" + "A" * 35, "Google API Key gefunden"),
    ],
)
def test_cross_vendor_no_collision_with_heroku_docker(
    vendor_token: str, vendor_reason: str
) -> None:
    """Cross-vendor regression: other vendors' tokens are not mis-attributed
    to the new Heroku / Docker Hub patterns. Both new detectors anchor on
    prefixes (``HRKU-`` / ``dckr_pat_``) which are unique to their
    respective vendors — no collision possible at the prefix level."""
    findings = _scan_content(f'OTHER_VENDOR = "{vendor_token}"\n')
    reasons = [r for _, _, r in findings]
    assert vendor_reason in reasons, (
        f"Cross-vendor token {vendor_token[:20]}... must keep its "
        f"canonical attribution; got: {reasons}"
    )
    # No false-positive Heroku / Docker Hub attribution.
    assert "Heroku Platform API Token gefunden" not in reasons
    assert "Docker Hub Personal Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# (10) Inventory invariants — source-grep enforces presence of the new
# patterns. A future regression that drops a pattern fails this test until
# the canonical detection is restored.
# ---------------------------------------------------------------------------


def test_secret_scanner_module_contains_heroku_known_token_entry() -> None:
    """Inventory pin: ``src/utils/secret_scanner.py`` must contain a
    ``_KNOWN_TOKENS`` entry that anchors the ``HRKU-`` prefix family."""
    from pathlib import Path

    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "HRKU-" in source, (
        "secret_scanner.py must contain an HRKU- detection pattern "
        "(Heroku Platform API Token)"
    )
    assert "Heroku Platform API Token" in source, (
        "secret_scanner.py must use canonical 'Heroku Platform API Token "
        "gefunden' attribution"
    )


def test_secret_scanner_module_contains_docker_hub_known_token_entry() -> None:
    """Inventory pin: ``dckr_pat_`` Docker Hub PAT detector present."""
    from pathlib import Path

    from src.utils import secret_scanner

    source = Path(secret_scanner.__file__).read_text(encoding="utf-8")
    assert "dckr_pat_" in source, (
        "secret_scanner.py must contain a dckr_pat_ detection pattern "
        "(Docker Hub Personal Access Token)"
    )
    assert "Docker Hub Personal Access Token" in source, (
        "secret_scanner.py must use canonical 'Docker Hub Personal Access "
        "Token gefunden' attribution"
    )
