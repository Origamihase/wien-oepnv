"""Sentinel drift coverage for the DevOps / CI/CD Pipeline + DigitalOcean
Cloud token tier value-shape log-sanitisation across
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain.

The 2026-05-17 Slack + AI/ML Inference Platform Log-Sanitisation Drift
Closure round (see ``.jules/sentinel.md``) named the **DevOps & CI/CD
Pipeline credential tier** as the next high-impact backlog cluster. The
secret-scanner ``_KNOWN_TOKENS`` already detects committed tokens for the
following 13 issuer-prefixes across 4 vendors, but the companion log-
sanitisation codepath (``src/utils/logging.py:sanitize_log_message``) was
NOT extended in any prior round — bare tokens in plain log text leaked
verbatim into operator log streams plus the public
``docs/feed_health.json`` artefact.

Families covered (each is a sibling-drift closure for one or more
``_KNOWN_TOKENS`` entries):

GitLab CI/CD Pipeline tier (8 prefixes)
---------------------------------------

* **GitLab Runner Authentication Token** — ``glrt-<20 chars from
  [A-Za-z0-9_-]>``. Issued via project / group / instance Runner
  registration in GitLab 15.6+ (post-16.0 default replacing the legacy
  unprefixed registration-token shape). A leak grants the holder the
  ability to register a rogue GitLab Runner against the issuing scope:
  the rogue runner drains the CI job queue, and every CI job (with
  whatever build-secret env vars the pipeline exposes) is delivered to
  attacker-controlled hardware.

* **GitLab Deploy Token** — ``gldt-<20 chars from [A-Za-z0-9_-]>``.
  Issued via project / group Settings > Repository > Deploy tokens for
  registry-pull / repository-read / package-read access. A leak grants
  Deploy-Token-scope pull access (CI image / package registry contents,
  source-code read) — sufficient to exfiltrate every committed secret
  and every package the project produces.

* **GitLab CI Agent Token** — ``glagent-<50+ chars from [A-Za-z0-9_-]>``.
  Issued for the GitLab Agent for Kubernetes (KAS). A leak grants the
  agent's full cluster scope: apply arbitrary manifests to the connected
  Kubernetes cluster, exfiltrate cluster secrets, run privileged pods.

* **GitLab Feature Flag Token** — ``glft-<20 chars from [A-Za-z0-9_-]>``.
  Issued for Unleash-protocol feature-flag clients. Leak grants the
  ability to flip feature flags arbitrarily (enable hidden / abandoned
  features, disable security gates).

* **GitLab Incoming Mail Token** — ``glimt-<25+ chars from
  [A-Za-z0-9_-]>``. Issued for the Service-Desk and incoming-mail
  integration. Leak grants impersonation of arbitrary users when
  injecting issues / merge-request comments via the mail gateway.

* **GitLab CI Build Token** — ``glcbt-<alphanumeric_segment>_<20+ chars
  from [A-Za-z0-9_-]>``. Issued per CI job by the GitLab Runner. Leak
  inside the job duration grants pull/push access to the project's
  Container Registry, Package Registry, and protected CI/CD variables.

* **GitLab SCIM OAuth Access Token** — ``glsoat-<20+ chars from
  [A-Za-z0-9_-]>``. Issued for SCIM-based user provisioning at the
  group / instance level. Leak grants the ability to create / modify /
  delete user accounts across the SCIM-managed scope.

* **GitLab Pipeline Trigger Token** — ``glptt-<40 chars from
  [A-Za-z0-9_-]>``. Issued per project for the Pipeline-Trigger API.
  Leak lets a network adversary trigger arbitrary pipelines with
  attacker-controlled variables — code-execution primitive on the
  project's runners.

CircleCI tier (1 prefix)
------------------------

* **CircleCI Personal API Token** — ``CCIPAT_<32+ chars from
  [A-Za-z0-9_-]>``. Issued via app.circleci.com/settings/user/tokens
  for REST API v2 access. Leak grants the issuing user's full
  CircleCI scope: read every accessible pipeline's build logs (which
  often include masked-but-echoed env vars), trigger pipelines with
  arbitrary parameters, modify project settings.

Buildkite tier (2 prefixes)
---------------------------

* **Buildkite Agent Token** — ``bkat_<40+ alphanumeric body>``. Issued
  via buildkite.com/organizations/<org>/agents for Buildkite agent
  registration. Highest leak surface in the modern CI stack: rogue
  agents can drain the job queue with whatever build-secret env vars
  the pipeline exposes.

* **Buildkite User Access Token** — ``bkua_<40+ alphanumeric body>``.
  Issued via buildkite.com/user/api-access-tokens for user-scoped REST
  API access. Leak grants the issuing user's full Buildkite scope
  across every accessible organisation: read pipeline definitions,
  retry historical builds with attacker-controlled env overrides,
  manage agents.

DigitalOcean cloud tier (2 prefixes)
------------------------------------

* **DigitalOcean Personal Access Token** — ``dop_v1_<64 lowercase hex>``.
  Issued via cloud.digitalocean.com/account/api/tokens for full account
  API access. Leak grants the issuer's full Droplet/Spaces/Database/
  Kubernetes cluster scope across every project in the account.

* **DigitalOcean OAuth Refresh Token** — ``doo_v1_<64 lowercase hex>``.
  Issued during the OAuth-app authorisation flow. Mints fresh
  ``dop_v1_`` access tokens until the refresh token is revoked.

Pre-fix detection gaps (mirror the 2026-05-17 Multi-Vendor / Vault /
GitHub / Slack-AIML rounds' structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare
   token in plain log text bypasses every existing pattern. Four
   leak surfaces:

   * **Plain application f-string logs** — ``log.error(f"GitLab API
     401 using {token}: {exc}")``. The bare token shape lands in
     operator log streams verbatim.
   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     echoes the supplied token back in its error payload.
   * **JSON values without sensitive key names** — ``{"data":
     "glrt-..."}`` or ``{"payload": "bkat_..."}``. The JSON-key
     sensitive-name regex misses keys like ``data`` / ``payload`` /
     ``response_body`` / ``message`` so the token value leaks
     unredacted into the JSON value span.
   * **URL paths / query strings with non-sensitive parameter
     names** — ``GET /api/v1/audit?ref=glrt-...`` or
     ``/internal/audit/CCIPAT_XXX/details``.

2. End-to-end via ``_sanitize_exception_msg``: this is the canonical
   exception-text sanitisation path in ``src/utils/http.py``. It
   extracts HTTP URLs via a pre-regex and falls back to
   ``sanitize_log_message`` for the non-HTTP-URL remainder. Fixing
   the latter closes the exception-text leak sink for every leaked
   token in the families covered here.

**Fix:** append thirteen value-shape mask patterns to
``sanitize_log_message``'s pattern list mirroring the scanner regex
structural anchors exactly. Each pattern preserves the issuer-
specific prefix (``glrt-***``, ``CCIPAT_***``, ``bkat_***``,
``dop_v1_***`` etc.) for incident-response triage because each tier
has a distinct revocation flow:

* GitLab CI/CD — gitlab.com/-/user_settings/applications &
  project / group settings > Access Tokens > Revoke (different sub-
  page per token type).
* CircleCI — app.circleci.com/settings/user/tokens > Revoke; audit
  build logs for unauthorised pipeline triggers.
* Buildkite Agent — buildkite.com/organizations/<org>/agents >
  Revoke; audit agent-connection history for rogue registrations.
* Buildkite User — buildkite.com/user/api-access-tokens > Delete.
* DigitalOcean PAT — cloud.digitalocean.com/account/api/tokens >
  Regenerate; audit billing for unauthorised resource creation.
* DigitalOcean OAuth Refresh — revoke at the OAuth app's authorisation
  list on the user's account page.

Structural anchors mirror the scanner regexes exactly:

* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myglrt-``, ``fooCCIPAT_`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict body lengths / alphabets per vendor canonical format
  reject accidental fragments while accepting every real-shape
  token.

Idempotence: masked forms (``glrt-***``, ``CCIPAT_***``,
``bkat_***``, ``dop_v1_***`` etc.) do NOT match any of the new
regexes because ``*`` is not in any body alphabet AND the masked
body length (3 chars) is below every per-family floor
(20/25/27/32/40/50/64).

Marker: SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the GitLab "
    "CI/CD family (glrt-/gldt-/glagent-/glft-/glimt-/glcbt-/glsoat-/"
    "glptt-), CircleCI (CCIPAT_), Buildkite (bkat_/bkua_), and "
    "DigitalOcean (dop_v1_/doo_v1_) token families that the scanner's "
    "_KNOWN_TOKENS already detects in committed source files. Bare "
    "tokens in plain log text, JSON values with non-sensitive keys, "
    "URL paths / query strings, and exception messages slipped past "
    "all key/header/URL-credential masking patterns and leaked "
    "verbatim into operator log streams and the public "
    "docs/feed_health.json artefact."
)


# ---------------------------------------------------------------------------
# Canonical real-shape token fixtures, one per scanner-detected prefix.
# Each body uses a mixed alphabet that exercises the regex's full character
# class so partial-class bypasses (uniform-class bodies) cannot mask a
# regex bug as a passing test.
# ---------------------------------------------------------------------------


def _body_extended(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    extended ``[A-Za-z0-9_-]`` alphabet — exercises the full character
    class so a partial-class regex bug cannot pass."""
    chunk = "Aa1B-c_D"  # 8-char cycle covering upper/lower/digit/dash/underscore
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_alnum(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    pure alphanumeric alphabet (no `_` or `-`)."""
    chunk = "Aa1Bb2Cc3"  # 9-char cycle, no underscores or dashes
    return (chunk * (length // len(chunk) + 1))[:length]


def _body_hex(length: int) -> str:
    """Generate a deterministic body of exactly ``length`` chars using the
    lowercase hex alphabet."""
    chunk = "0123456789abcdef"
    return (chunk * (length // len(chunk) + 1))[:length]


# GitLab CI/CD family
# 20-char body exact (``[A-Za-z0-9_-]`` alphabet)
_GLRT_RUNNER = "glrt-" + _body_extended(20)
_GLDT_DEPLOY = "gldt-" + _body_extended(20)
_GLFT_FEATURE_FLAG = "glft-" + _body_extended(20)
# 25+ char body
_GLIMT_INCOMING_MAIL = "glimt-" + _body_extended(28)
# 50+ char body
_GLAGENT_AGENT = "glagent-" + _body_extended(56)
# Special two-part body: <alphanumeric_project>_<20+ body>
_GLCBT_CI_BUILD = "glcbt-" + "ProjAlpha2024" + "_" + _body_extended(26)
# 20+ char body
_GLSOAT_SCIM_OAUTH = "glsoat-" + _body_extended(24)
# 40-char body exact
_GLPTT_PIPELINE_TRIGGER = "glptt-" + _body_extended(40)

# CircleCI: 32+ char body, ``[A-Za-z0-9_-]`` alphabet
_CCIPAT = "CCIPAT_" + _body_extended(38)

# Buildkite: 40+ char pure alphanumeric body (no _ or -)
_BKAT_AGENT = "bkat_" + _body_alnum(42)
_BKUA_USER = "bkua_" + _body_alnum(45)

# DigitalOcean: 64-char lowercase hex body
_DOP_V1_PAT = "dop_v1_" + _body_hex(64)
_DOO_V1_OAUTH_REFRESH = "doo_v1_" + _body_hex(64)


# Sanity checks: ensure fixture body lengths satisfy the scanner regex
# anchor floors exactly. Failure here means the test fixture itself is
# malformed and would mask a real regex bug.
assert len(_GLRT_RUNNER) - len("glrt-") == 20
assert len(_GLDT_DEPLOY) - len("gldt-") == 20
assert len(_GLFT_FEATURE_FLAG) - len("glft-") == 20
assert len(_GLIMT_INCOMING_MAIL) - len("glimt-") >= 25
assert len(_GLAGENT_AGENT) - len("glagent-") >= 50
assert "_" in _GLCBT_CI_BUILD[len("glcbt-"):]
assert len(_GLSOAT_SCIM_OAUTH) - len("glsoat-") >= 20
assert len(_GLPTT_PIPELINE_TRIGGER) - len("glptt-") == 40
assert len(_CCIPAT) - len("CCIPAT_") >= 32
assert len(_BKAT_AGENT) - len("bkat_") >= 40
assert len(_BKUA_USER) - len("bkua_") >= 40
assert len(_DOP_V1_PAT) - len("dop_v1_") == 64
assert len(_DOO_V1_OAUTH_REFRESH) - len("doo_v1_") == 64


# Group fixtures for parametrisation
_GITLAB_CICD_TOKENS = [
    (_GLRT_RUNNER, "glrt-"),
    (_GLDT_DEPLOY, "gldt-"),
    (_GLFT_FEATURE_FLAG, "glft-"),
    (_GLIMT_INCOMING_MAIL, "glimt-"),
    (_GLAGENT_AGENT, "glagent-"),
    (_GLCBT_CI_BUILD, "glcbt-"),
    (_GLSOAT_SCIM_OAUTH, "glsoat-"),
    (_GLPTT_PIPELINE_TRIGGER, "glptt-"),
]

_CIRCLECI_TOKENS = [
    (_CCIPAT, "CCIPAT_"),
]

_BUILDKITE_TOKENS = [
    (_BKAT_AGENT, "bkat_"),
    (_BKUA_USER, "bkua_"),
]

_DIGITALOCEAN_TOKENS = [
    (_DOP_V1_PAT, "dop_v1_"),
    (_DOO_V1_OAUTH_REFRESH, "doo_v1_"),
]

_ALL_TOKENS = (
    _GITLAB_CICD_TOKENS
    + _CIRCLECI_TOKENS
    + _BUILDKITE_TOKENS
    + _DIGITALOCEAN_TOKENS
)


# ---------------------------------------------------------------------------
# (1) Plain log line — bare token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_cicd_token_in_plain_log_line_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Bare CI/CD vendor token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through
    the operator-log sink and the public ``docs/feed_health.json``
    artefact."""
    log_line = f"Provider API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' leaked through "
        f"sanitize_log_message: "
        f"{SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{expected_prefix}***" in result, (
        f"Mask MUST preserve issuer-attribution prefix "
        f"'{expected_prefix}***' for incident-response triage"
    )


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_cicd_token_in_json_value_is_masked(
    token: str, expected_prefix: str, key_name: str
) -> None:
    """Token in JSON value with a NON-sensitive key name MUST be
    masked — pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` / ``message`` and
    the token value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"Token with prefix '{expected_prefix}' in JSON value with "
        f"non-sensitive key '{key_name}' leaked through "
        f"sanitize_log_message"
    )
    assert f"{expected_prefix}***" in result


# ---------------------------------------------------------------------------
# (3) URL path / query string with non-sensitive parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_cicd_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, expected_prefix: str
) -> None:
    """Token in URL query string with a NON-sensitive parameter name
    (``ref`` / ``commit_sha`` / ``q``) MUST be masked — pre-fix the
    URL credential regex required the credential to appear before
    ``@``; query-string and path-embedded tokens slipped past."""
    log_line = f"GET /api/foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{expected_prefix}***" in result


def test_gitlab_runner_token_in_url_path_segment_is_masked() -> None:
    """Token embedded in URL path segment (NOT ``user:pass@`` form)
    MUST be masked — covers the path-embedded leak surface."""
    log_line = f"GET /api/internal/audit/{_GLRT_RUNNER}/details 200"
    result = sanitize_log_message(log_line)
    assert _GLRT_RUNNER not in result
    assert "glrt-***" in result


def test_digitalocean_token_in_url_path_segment_is_masked() -> None:
    """DigitalOcean PAT embedded in a URL path segment MUST be masked.
    Uses a NON-sensitive parameter context (path segment, not
    ``?token=`` which would hit the existing sensitive-query-key
    regex first) so the value-shape mask is the primary defence."""
    log_line = f"GET /api/internal/audit/{_DOP_V1_PAT}/details 200"
    result = sanitize_log_message(log_line)
    assert _DOP_V1_PAT not in result
    assert "dop_v1_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected_prefix", _ALL_TOKENS)
def test_cicd_token_through_sanitize_exception_msg(
    token: str, expected_prefix: str
) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask vendor tokens."""
    exc_msg = f"HTTPError: 401 Unauthorized — credential {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{expected_prefix}***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_gitlab_runner_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args."""
    arg = f"audit: {_GLRT_RUNNER}"
    result = sanitize_log_arg(arg)
    assert _GLRT_RUNNER not in result
    assert "glrt-***" in result


def test_sanitize_log_arg_masks_buildkite_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    Uses a NON-sensitive attribute name (``audit``) so the value-shape
    mask is the primary defence (the ``key=value`` regex would catch
    sensitive names like ``token`` first)."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_BKAT_AGENT})"

    result = sanitize_log_arg(_Wrapper())
    assert _BKAT_AGENT not in result, (
        "Buildkite Agent Token leaked through sanitize_log_arg"
    )
    assert "bkat_***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below each per-family floor
        "glrt-short",
        "gldt-short",
        "glagent-short",
        "glft-short",
        "glimt-short",
        "glcbt-short_short",  # 5+5 = doesn't reach 20+ in body half
        "glsoat-short",
        "glptt-short",
        "CCIPAT_short",
        "bkat_short",
        "bkua_short",
        "dop_v1_short",
        "doo_v1_short",
        # Mid-identifier collisions (lookbehind prevents these)
        "Xglrt-" + "A" * 20,
        "0gldt-" + "A" * 20,
        "9glagent-" + "A" * 50,
        "AbkAT_" + "A" * 40,  # case-sensitive: bkAT != bkat
        "1CCIPAT_" + "A" * 32,
        "0bkat_" + "A" * 40,
        "9dop_v1_" + "a" * 64,
    ],
)
def test_benign_cicd_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / mid-identifier collisions MUST
    NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind plus the body
    floor are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive vendor token mask on benign input: "
        f"{benign!r} → {result!r}"
    )


def test_short_buildkite_body_is_not_masked() -> None:
    """Buildkite body shorter than 40 chars is below the structural floor."""
    short = "bkat_" + "a" * 39  # 39 chars body, below 40-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_circleci_body_is_not_masked() -> None:
    """CircleCI body shorter than 32 chars is below the structural floor."""
    short = "CCIPAT_" + "a" * 31  # 31 chars body, below 32-char floor
    result = sanitize_log_message(short)
    assert result == short


def test_digitalocean_short_body_is_not_masked() -> None:
    """DigitalOcean body shorter than 64 chars OR using non-hex chars is
    below the structural floor."""
    short = "dop_v1_" + "a" * 63  # 63 chars, below 64-char anchor
    result = sanitize_log_message(short)
    assert result == short


def test_digitalocean_non_hex_body_is_not_masked() -> None:
    """DigitalOcean regex requires LOWERCASE hex body. Uppercase letters
    must NOT match — mirrors the scanner's strict alphabet."""
    non_hex = "dop_v1_" + "G" * 64  # 64 chars but uppercase G not in [a-f0-9]
    result = sanitize_log_message(non_hex)
    assert result == non_hex


def test_glcbt_without_underscore_separator_is_not_masked() -> None:
    """GitLab CI Build Token regex requires ``glcbt-<alnum>_<20+ body>``
    with the embedded underscore. Bodies without it must NOT match."""
    no_underscore = "glcbt-" + "A" * 25  # No internal underscore
    result = sanitize_log_message(no_underscore)
    assert result == no_underscore


def test_glptt_body_not_exactly_40_is_not_masked() -> None:
    """GitLab Pipeline Trigger requires EXACTLY 40-char body (no `+`).
    A 41-char body in `[A-Za-z0-9_-]` would extend the match span, but
    the lookahead `(?![A-Za-z0-9])` requires a non-alphanumeric
    immediately after 40 chars. A 41-char alphanumeric tail therefore
    fails the lookahead."""
    long_alnum = "glptt-" + "A" * 41
    result = sanitize_log_message(long_alnum)
    assert long_alnum in result, (
        "glptt- body with 41 contiguous alnum chars must NOT be masked"
    )


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,_prefix", _ALL_TOKENS)
def test_cicd_token_mask_is_idempotent(token: str, _prefix: str) -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output. The mask token (e.g. ``glrt-***``) MUST
    NOT be re-matched as a credential."""
    log_line = f"Failed: {token}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert token not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — log mask covers every scanner prefix
# ---------------------------------------------------------------------------


def test_gitlab_cicd_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant.** Every GitLab CI/CD issuer prefix
    that appears in ``_KNOWN_TOKENS`` MUST have a matching mask in
    ``sanitize_log_message``. A future addition to the scanner without
    a companion log-mask addition fails this test programmatically."""
    pairs = [
        ("glrt-", _GLRT_RUNNER),
        ("gldt-", _GLDT_DEPLOY),
        ("glagent-", _GLAGENT_AGENT),
        ("glft-", _GLFT_FEATURE_FLAG),
        ("glimt-", _GLIMT_INCOMING_MAIL),
        ("glcbt-", _GLCBT_CI_BUILD),
        ("glsoat-", _GLSOAT_SCIM_OAUTH),
        ("glptt-", _GLPTT_PIPELINE_TRIGGER),
    ]
    for prefix, token in pairs:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"GitLab CI/CD prefix '{prefix}' missing from "
            f"sanitize_log_message — log-sanitisation drift vs. "
            f"scanner _KNOWN_TOKENS"
        )
        assert f"{prefix}***" in result, (
            f"GitLab CI/CD prefix '{prefix}' mask MUST preserve issuer "
            f"attribution as '{prefix}***' for incident-response triage"
        )


def test_buildkite_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant** for the Buildkite token family
    (bkat_ Agent + bkua_ User Access)."""
    pairs = [
        ("bkat_", _BKAT_AGENT),
        ("bkua_", _BKUA_USER),
    ]
    for prefix, token in pairs:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"Buildkite prefix '{prefix}' missing from sanitize_log_message"
        )
        assert f"{prefix}***" in result


def test_digitalocean_family_log_mask_covers_every_scanner_prefix() -> None:
    """**Sibling-alignment invariant** for the DigitalOcean token family
    (dop_v1_ PAT + doo_v1_ OAuth Refresh)."""
    pairs = [
        ("dop_v1_", _DOP_V1_PAT),
        ("doo_v1_", _DOO_V1_OAUTH_REFRESH),
    ]
    for prefix, token in pairs:
        log_line = f"diagnostic: {token}"
        result = sanitize_log_message(log_line)
        assert token not in result, (
            f"DigitalOcean prefix '{prefix}' missing from sanitize_log_message"
        )
        assert f"{prefix}***" in result


def test_scanner_known_tokens_contain_every_log_masked_prefix() -> None:
    """**Inverse sibling-alignment invariant.** Every prefix we log-
    mask in this round MUST also exist in the scanner's
    ``_KNOWN_TOKENS``. This ensures the log mask doesn't drift ahead
    of the scanner — if the scanner ever drops a prefix, this test
    fires."""
    scanner_pattern_text = " ".join(
        regex.pattern for regex, _attr in _KNOWN_TOKENS
    )

    expected_substrings = [
        ("glrt-", "GitLab Runner Authentication Token"),
        ("gldt-", "GitLab Deploy Token"),
        ("glagent-", "GitLab CI Agent Token"),
        ("glft-", "GitLab Feature Flag Token"),
        ("glimt-", "GitLab Incoming Mail Token"),
        ("glcbt-", "GitLab CI Build Token"),
        ("glsoat-", "GitLab SCIM OAuth Access Token"),
        ("glptt-", "GitLab Pipeline Trigger Token"),
        ("CCIPAT_", "CircleCI Personal API Token"),
        ("bkat_", "Buildkite Agent Token"),
        ("bkua_", "Buildkite User Access Token"),
        ("dop_v1_", "DigitalOcean Personal Access Token"),
        ("doo_v1_", "DigitalOcean OAuth Refresh Token"),
    ]
    for substring, label in expected_substrings:
        assert substring in scanner_pattern_text, (
            f"Scanner _KNOWN_TOKENS is missing the '{substring}' "
            f"({label}) entry that the log-sanitiser mask family "
            f"depends on. If this test fires, either restore the "
            f"scanner entry or remove the corresponding mask from "
            f"sanitize_log_message."
        )


# ---------------------------------------------------------------------------
# (9) Cross-family no-collision — each pattern matches ONLY its own family
# ---------------------------------------------------------------------------


def test_glpat_token_still_masked_with_glpat_prefix() -> None:
    """The existing ``glpat-`` mask (GitLab Personal Access Token) must
    NOT be eaten by any of the new GitLab CI/CD masks. The two are
    structurally distinct (``glpat-`` vs. ``glrt-`` / ``gldt-`` etc.)
    but explicit cross-family no-collision testing documents intent."""
    glpat_token = "glpat-" + _body_extended(20)
    log_line = f"key: {glpat_token}"
    result = sanitize_log_message(log_line)
    assert glpat_token not in result
    assert "glpat-***" in result


def test_glcbt_does_not_get_eaten_by_glsoat_pattern() -> None:
    """``glcbt-`` body has a special two-part shape (``<alnum>_<20+>``).
    The mask MUST be the ``glcbt-`` regex specifically — preserving the
    ``glcbt-`` prefix attribution rather than the generic ``glsoat-``
    family rule."""
    log_line = f"build: {_GLCBT_CI_BUILD}"
    result = sanitize_log_message(log_line)
    assert _GLCBT_CI_BUILD not in result
    assert "glcbt-***" in result


def test_dop_v1_does_not_get_eaten_by_glpat_or_other_dash_pattern() -> None:
    """DigitalOcean ``dop_v1_<64 hex>`` has a body alphabet (hex) that
    is a subset of the GitLab PAT alphabet, BUT the prefix is distinct
    (``dop_v1_`` vs ``glpat-``) so the masks are mutually exclusive."""
    log_line = f"pat: {_DOP_V1_PAT}"
    result = sanitize_log_message(log_line)
    assert _DOP_V1_PAT not in result
    assert "dop_v1_***" in result


# ---------------------------------------------------------------------------
# (10) Real-world emission pattern — upstream error echoing token back
# ---------------------------------------------------------------------------


def test_upstream_error_echoing_glptt_token_back_is_masked() -> None:
    """Real-world leak vector: GitLab Pipeline Trigger error responses
    sometimes include the supplied token suffix in their error message.
    The application log line ``log.warning(f"GitLab error:
    {response.text}")`` must NOT leak the token."""
    fake_response_text = (
        '{"error": "Forbidden", '
        f'"message": "trigger token {_GLPTT_PIPELINE_TRIGGER} '
        f'is not authorised for project 42"}}'
    )
    log_line = f"GitLab API error: {fake_response_text}"
    result = sanitize_log_message(log_line)
    assert _GLPTT_PIPELINE_TRIGGER not in result
    assert "glptt-***" in result


def test_upstream_error_echoing_buildkite_agent_token_back_is_masked() -> None:
    """Mirror test for Buildkite: agent-registration error responses
    occasionally include the supplied token in the error message body."""
    fake_error = (
        '{"errors": [{"detail": '
        f'"Agent token {_BKAT_AGENT} has been revoked"}}]}}'
    )
    log_line = f"Provider error: {fake_error}"
    result = sanitize_log_message(log_line)
    assert _BKAT_AGENT not in result
    assert "bkat_***" in result


def test_upstream_error_echoing_circleci_token_back_is_masked() -> None:
    """CircleCI 401 responses occasionally echo the bearer-token value
    in the response body during certain misconfiguration paths."""
    fake_error = (
        f'{{"message": "Authentication failed for token {_CCIPAT}"}}'
    )
    log_line = f"CircleCI API error: {fake_error}"
    result = sanitize_log_message(log_line)
    assert _CCIPAT not in result
    assert "CCIPAT_***" in result


def test_upstream_error_echoing_digitalocean_token_back_is_masked() -> None:
    """DigitalOcean 401 responses with token-debug echoing must be
    masked — DigitalOcean PAT is full-account-scope so this is highest
    blast radius in the round."""
    fake_error = (
        f'{{"id": "unauthorized", '
        f'"message": "Token {_DOP_V1_PAT} is invalid or expired"}}'
    )
    log_line = f"DigitalOcean API error: {fake_error}"
    result = sanitize_log_message(log_line)
    assert _DOP_V1_PAT not in result
    assert "dop_v1_***" in result
