"""Placeholder debris in the EN cache is re-translated (fund D, 2026-10-02).

``_PLACEHOLDER_DEBRIS_RE`` repairs fresh translations since 2026-10-01, but a
value cached before kept its debris and was served from the cache: the EN
feed of 2026-10-02 still read "between Wien Franz-Josefs-Bahnhof0X and
St.Andrä-Wördern".
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.build_feed import _cached_translation, _cached_translation_defect

_SOURCE = (
    "Wegen Bauarbeiten können von 02.11.2026 bis 03.11.2026 zwischen Wien "
    "Franz-Josefs-Bahnhof und St.Andrä-Wördern Bahnhof keine R 40-Züge fahren."
)
_CACHED = (
    "Due to construction works, from 02.11.2026 to 03.11.2026 between Wien "
    "Franz-Josefs-Bahnhof0X and St.Andrä-Wördern train station no R 40 train."
)


@pytest.fixture()
def echo_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A model that returns its masked input with an ``EN `` prefix."""
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": "EN " + text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


@pytest.mark.parametrize(
    ("source", "cached"),
    [
        (_SOURCE, _CACHED),
        ("Fahrtbehinderung wegen Verkehrsunfall", "service obstruction because of traffic accidentX"),
        ("Linie 18: Fahrtbehinderung", "Line 18X: service obstruction"),
        ("12A/13A/14A: Verkehrsüberlastung", "12A/13A/14A2X: traffic congestion0X"),
        ("Voraussichtliche Dauer: 13:40 Uhr", "expected duration: 13:40X"),
        ("10: Schadhafter Zug Maroltingergasse", "10: Damaged train XGLABc73c97a91c2673X0X Maroltingergasse"),
    ],
)
def test_debris_is_a_defect(source: str, cached: str) -> None:
    assert _cached_translation_defect(source, cached) == "residual placeholder"


@pytest.mark.parametrize(
    ("source", "cached"),
    [
        ("REX 41: Wien Franz-Josefs-Bahnhof ↔ Tulln", "REX 41: Wien Franz-Josefs-Bahnhof ↔ Tulln"),
        ("Linie 13A: Umleitung", "Line 13A: diversion"),
        ("Bus 400X fährt", "Bus 400X runs"),  # the source carries the token itself
        ("Ab 02.11.2026 Ersatzverkehr", "From 02.11.2026 replacement service"),
    ],
)
def test_sound_text_is_no_defect(source: str, cached: str) -> None:
    assert _cached_translation_defect(source, cached) is None


def test_cached_debris_is_re_translated(echo_pipeline: list[str]) -> None:
    state: dict[str, dict[str, Any]] = {"oebb|r40": {"translations": {"en": {"summary": _CACHED}}}}
    out, ok = _cached_translation(_SOURCE, "summary", "oebb|r40", state)
    assert ok
    assert len(echo_pipeline) == 1, "the debris value must be a cache miss"
    assert "Bahnhof0X" not in out
    assert state["oebb|r40"]["translations"]["en"]["summary"] == out

