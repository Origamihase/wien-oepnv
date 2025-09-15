#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Helpers for reading environment variables in a safe way."""

from __future__ import annotations

import logging
import os

__all__ = ["get_int_env"]


def get_int_env(name: str, default: int) -> int:
    """Read integer environment variables safely.

    Returns the provided default if the variable is unset or cannot be
    converted to ``int``. On invalid values, a warning is logged using the
    ``build_feed`` logger.
    """

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError) as e:
        logging.getLogger("build_feed").warning(
            "Ungültiger Wert für %s=%r – verwende Default %d (%s: %s)",
            name,
            raw,
            default,
            type(e).__name__,
            e,
        )
        return default

