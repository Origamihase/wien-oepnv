"""Shared fixtures of the Heroku / Docker Hub token drift sentinels.

Failure marker, deterministic body generator and the token fixtures used by
``test_sentinel_heroku_docker_token_drift.py`` and
``test_sentinel_heroku_docker_token_drift_invariants.py``.
"""

from __future__ import annotations

__all__ = [
    "SENTINEL_HEROKU_DOCKER_TOKEN_DRIFT",
    "_DOCKER_PAT_LONG",
    "_DOCKER_PAT_SHORT",
    "_HEROKU_B64",
    "_HEROKU_UUID",
    "_body_b64url",
]

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
