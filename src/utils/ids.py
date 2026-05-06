#!/usr/bin/env python3
"""ID utilities."""

import hashlib

__all__ = ["make_guid"]


def make_guid(*parts: str | None) -> str:
    """
    Return a stable SHA256-based GUID for the given parts.

    Falsy values (None, empty string) are coerced to empty strings before
    hashing. Pipe characters and backslashes in values are escaped to
    prevent collisions.
    """
    return hashlib.sha256(
        "|".join(
            (p or "").replace("\\", "\\\\").replace("|", "\\|")
            for p in parts
        ).encode("utf-8")
    ).hexdigest()
