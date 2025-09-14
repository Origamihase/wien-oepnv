"""Wiener Linien provider wrapper.

This module keeps the original public API intact by exposing the
``fetch_events`` function from :mod:`wl_fetch`.
"""

from .wl_fetch import fetch_events

__all__ = ["fetch_events"]

