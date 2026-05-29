"""Regression test for the ``KW_RESTRICTION`` word-boundary fix.

Pre-fix ``KW_RESTRICTION`` wrapped its keyword roots in ``\\b(...)\\b``. The
trailing ``\\b`` requires the root to be a COMPLETE word, but the list is
deliberately built from prefix-roots (``sperr``, ``baustell``, ``verspät``,
``einschränk``, ``unterbrech`` …). So every common German inflection /
compound — ``Baustelle``, ``Teilsperre``, ``Bauarbeiten``, ``Verspätungen``,
``Einschränkungen``, ``Signalstörung`` — FAILED the gate, and the item was
silently dropped at the mandatory WL-news inclusion gate in ``wl_fetch`` (and
not rescued past ``KW_EXCLUDE``). Only the handful of roots that happen to be
standalone words (``umleitung``, ``verkehr``, ``gesperrt``, standalone
``Störung`` / ``Ausfall``) ever matched.

The fix mirrors the sibling ``FACILITY_ONLY`` regex in the same module:
``\\b\\w*(...)\\w*\\b`` matches the root anywhere inside a compound noun.
"""
from __future__ import annotations

import pytest

from src.providers.wl_text import KW_RESTRICTION


@pytest.mark.parametrize(
    "text",
    [
        # Prefix-root compounds / inflections — ALL dropped pre-fix.
        "Baustelle Reumannplatz",
        "Bauarbeiten Linie 2",
        "Gleisbauarbeiten an der Trasse",
        "Teilsperre Praterstern",
        "Streckensperre Linie U4",
        "Sperre",
        "Sperrung der Station",
        "Verspätungen auf der Linie 13A",
        "Einschränkungen im Betrieb",
        "Betriebsunterbrechung",
        "Zugausfall",
        "Signalstörung",
        "Kurzführung der Linie U6",
        # Full-word roots that already matched pre-fix — regression guard that
        # the broadened pattern did not regress the previously-working cases.
        "Umleitung Linie 5",
        "Ersatzverkehr eingerichtet",
        "Strecke gesperrt",
        "Störung",
    ],
)
def test_kw_restriction_matches_compound_disruptions(text: str) -> None:
    assert KW_RESTRICTION.search(text) is not None, (
        f"{text!r} must pass the WL restriction gate — pre-fix the trailing "
        r"\b dropped this compound/inflection of a prefix-root."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sommerfest am Rathausplatz",
        "Eröffnung der neuen Station",
        "Gewinnspiel zum Jubiläum",
        "Information zum Ticketkauf",
        "Willkommen bei den Wiener Linien",
        "Linie 2 fährt wieder planmäßig",
    ],
)
def test_kw_restriction_rejects_non_disruptions(text: str) -> None:
    """The broadened pattern must NOT start matching obvious non-disruptions
    (promo / info / normal-service notices)."""
    assert KW_RESTRICTION.search(text) is None, (
        f"{text!r} is not a disruption and must not pass the restriction gate."
    )
