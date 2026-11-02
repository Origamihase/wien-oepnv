"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_secret_scanner_token_scheme_drift.py.
"""

from __future__ import annotations


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


__all__ = [
    "SENTINEL_TOKEN_SCHEME_DRIFT",
    "TOKEN_SCHEME_REASON",
    "_ALL_LETTERS_BODY",
    "_LEGACY_HEX_BODY",
    "_OPAQUE_MIXED_BODY",
]
