"""bug b1: the route regexes (_ZWISCHEN_/_VON_NACH_/_STRECKE_PLAIN_RE)
over-extended the second endpoint into the predicate because their boundary
alternation lacked ``zu``/``zur`` (zwischen), ``über``/``via`` (von-nach +
strecke) and the predicate verbs (strecke). The standard ÖBB phrasing
"kommt es zwischen X und Y zu …" then produced a frankenstring endpoint, the
real Wien route was mis-classified as "unknown" and the message dropped.

bug b12: in the single-station path a Pendler-only mention was counted as
relevant even under OEBB_ONLY_VIENNA, where the route rule requires BOTH
endpoints inside Vienna — so a lone Pendler message leaked in strict mode.
"""
from __future__ import annotations

import pytest

import src.providers.oebb as oebb
from src.providers.oebb import _is_relevant


class TestRouteBoundaryTokens:
    def test_zwischen_zu_keeps_wien_route(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Aufgrund von Bauarbeiten kommt es zwischen Wien Mitte und "
                "Flughafen Wien zu Einschränkungen.",
            )
            is True
        )

    def test_von_nach_ueber_keeps_wien_route(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Von Wien Meidling nach Mödling über Wiener Neudorf ist die "
                "Strecke gesperrt.",
            )
            is True
        )

    def test_strecke_kommt_keeps_wien_route(self) -> None:
        assert (
            _is_relevant(
                "Verspätungen",
                "Auf der Strecke Wien Meidling - Wien Hauptbahnhof kommt es "
                "zu Verspätungen.",
            )
            is True
        )


def test_lone_pendler_dropped_only_under_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = ("Störung Mödling", "Wegen einer Störung kommt es zu Verspätungen.")
    monkeypatch.setattr(oebb, "OEBB_ONLY_VIENNA", True)
    assert oebb._is_relevant(*msg) is False
    monkeypatch.setattr(oebb, "OEBB_ONLY_VIENNA", False)
    assert oebb._is_relevant(*msg) is True
