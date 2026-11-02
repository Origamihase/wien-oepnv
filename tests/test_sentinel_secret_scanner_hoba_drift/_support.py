"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_sentinel_secret_scanner_hoba_drift.py.
"""

from __future__ import annotations


SENTINEL_HOBA_DRIFT = "rfc 7486 HOBA attribution + silent-undetection drift"

HOBA_REASON = "HOBA Authentication Credential gefunden"


# Realistic HOBA result bodies of varying shapes:
#
#  * ``_REALISTIC_HOBA_BODY`` — Realistic HOBA result: 43-char base64url
#    KID (= SHA-256 hash) + 16-char challenge + 16-char nonce + 86-char
#    ECDSA P-256 signature. Total ~161 chars + 3 dots = 164 chars.
#  * ``_ALL_LETTERS_BODY`` — 4 fields × 8 chars each, all letters
#    (no digits, no `+`/`/`/`=`). Trips the ``candidate.isalpha()``
#    skip in the entropy fallback so SILENTLY UNDETECTED pre-fix.
#  * ``_MIXED_BODY`` — Smaller mixed-class body for attribution-drift
#    PoC (each field at the 8+ floor, with mixed letters / digits /
#    underscores / hyphens).

_REALISTIC_HOBA_BODY = (
    "Mz4UeCBJD8MwLD9TwbDjUyU9rgcc9CwIIB44pVfH4Pc"  # 43-char base64url KID
    "."
    "kJ7Z9TmDhX9MQpA8"  # 16-char base64url challenge
    "."
    "rVqMzbN3xLpDtKsW"  # 16-char base64url nonce
    "."
    "MEUCIQDqxKqr8MhCdiBjQq3M4mUL_oXcjEdRGn4r9Z"
    "TlPLAg7AIgVrlHvAj8N6tBEAPb-i_9TF"  # 86-char base64url ECDSA P-256 sig
)

assert _REALISTIC_HOBA_BODY.count(".") == 3

assert len(_REALISTIC_HOBA_BODY) > 100

_ALL_LETTERS_BODY = (
    "abcdefghijklmnop"
    "."
    "qrstuvwxyzABCDEF"
    "."
    "GHIJKLMNOPQRSTUV"
    "."
    "WXYZabcdefghijkl"
)  # 4 × 16-char fields, all letters

assert _ALL_LETTERS_BODY.count(".") == 3

# Ensure each field is all-letter (the silent-undetection trigger).
for field in _ALL_LETTERS_BODY.split("."):
    assert field.isalpha(), f"field {field!r} is not all-letter"

_MIXED_BODY = (
    "Abc12345_X-Y9z"  # 14-char field, mixed
    "."
    "QwErTy12_3-4Lp"  # 14-char field, mixed
    "."
    "ZxCvBn78_9-0Mn"  # 14-char field, mixed
    "."
    "PqRsTu56_7-8Vw"  # 14-char field, mixed
)

assert _MIXED_BODY.count(".") == 3

for field in _MIXED_BODY.split("."):
    assert len(field) >= 8


__all__ = [
    "HOBA_REASON",
    "SENTINEL_HOBA_DRIFT",
    "_ALL_LETTERS_BODY",
    "_MIXED_BODY",
    "_REALISTIC_HOBA_BODY",
    "field",
]
