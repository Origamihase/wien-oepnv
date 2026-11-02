"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_cicd_devops_token_log_sanitization_drift.py.
"""

from __future__ import annotations


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


__all__ = [
    "SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT",
    "_ALL_TOKENS",
    "_BKAT_AGENT",
    "_BKUA_USER",
    "_BUILDKITE_TOKENS",
    "_CCIPAT",
    "_CIRCLECI_TOKENS",
    "_DIGITALOCEAN_TOKENS",
    "_DOO_V1_OAUTH_REFRESH",
    "_DOP_V1_PAT",
    "_GITLAB_CICD_TOKENS",
    "_GLAGENT_AGENT",
    "_GLCBT_CI_BUILD",
    "_GLDT_DEPLOY",
    "_GLFT_FEATURE_FLAG",
    "_GLIMT_INCOMING_MAIL",
    "_GLPTT_PIPELINE_TRIGGER",
    "_GLRT_RUNNER",
    "_GLSOAT_SCIM_OAUTH",
    "_body_alnum",
    "_body_extended",
    "_body_hex",
]
