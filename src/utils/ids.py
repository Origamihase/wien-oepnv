#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ID utilities."""

import hashlib

__all__ = ["make_guid"]


def make_guid(*parts: str) -> str:
    """
    Return a stable SHA256-based GUID for the given parts.

    Escapes pipe characters and backslashes to prevent collisions.
    """
    return hashlib.sha256(
        "|".join(
            (p or "").replace("\\", "\\\\").replace("|", "\\|")
            for p in parts
        ).encode("utf-8")
    ).hexdigest()
