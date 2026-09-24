"""A calendar date is one verbatim token through the model.

Published 2026-09-24 19:31 in ``docs/feed.en.xml`` (item 5), and cached the
same minute for ``27A/28A/29A`` in ``data/first_seen.json``::

    DE  U2: Klapprampensperre am 27.09.2026
    EN  U2: Folding ramps out of service on 2709.2026

``_LINE_ENTITY_RE`` masks the day ``27`` alone (a bare number is
line-shaped), so the model saw ``XENT…X3X.09.2026`` — a placeholder glued
to a period — and dropped the period. Of the 8 dated titles translated
since mid-August, 3 shipped that way (the ÖBB ``Update 1 (1609.2026
07:22)`` of 2026-09-16 is the third). The 16:15 build of the same day had
rendered the U2 date intact: the defect is a per-run lottery, and what the
lottery draws on is the glued shape.

Two changes, both pinned here: the date is masked as ONE entity ahead of
the line pass, and a cached English value that lost a date of its German
source is treated as a cache miss (``_cached_translation_defect``), so the
two poisoned values re-translate without an epoch bump.

Mutations checked against this file (each one caught, by the test named):

* the date pass is removed from ``_mask_entities`` →
  ``test_a_date_is_one_placeholder``.
* the date pass runs after the line pass (the day is already a placeholder,
  the date regex no longer matches) → ``test_a_date_is_one_placeholder``.
* the date check is dropped from ``_cached_translation_defect`` →
  ``test_a_cached_mangled_date_is_re_translated``.
* the set difference is made symmetric (an extra date in the English also
  evicts) → ``test_an_extra_english_date_does_not_evict``.
* the residual-placeholder check is dropped from the helper →
  ``test_a_residual_placeholder_still_evicts``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src import build_feed
from src.build_feed import (
    _DATE_ENTITY_RE,
    _ENTITY_PLACEHOLDER_RE,
    _cached_translation,
    _cached_translation_defect,
    _entities_dropped_by_translation,
    _mask_entities,
    _unmask_entities,
)

GLUED_TO_A_DIGIT = re.compile(_ENTITY_PLACEHOLDER_RE.pattern + r"\.\d")


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("U2: Klapprampensperre am 27.09.2026", "27.09.2026"),
        ("27A/28A/29A: Veranstaltung am 27.09.2026", "27.09.2026"),
        ("Update 1 (16.09.2026 07:22) Verkehrseinschränkung: Wien Floridsdorf", "16.09.2026"),
        ("Bis 30.9.2026 Betriebsschluss.", "30.9.2026"),
        ("Bauarbeiten ab 1.10.2026", "1.10.2026"),
    ],
)
def test_a_date_is_one_placeholder(text: str, date: str) -> None:
    masked, mapping = _mask_entities(text)
    assert date in mapping.values(), mapping
    assert date not in masked
    # The shape the model tore apart: a placeholder, a period, a digit.
    assert not GLUED_TO_A_DIGIT.search(masked), masked
    assert _unmask_entities(masked, mapping) == text


def test_the_day_is_no_longer_masked_as_a_line() -> None:
    _, mapping = _mask_entities("Veranstaltung am 27.09.2026")
    assert "27" not in mapping.values()
    assert list(mapping.values()) == ["27.09.2026"]


def test_a_dropped_date_fails_the_translation() -> None:
    masked, mapping = _mask_entities("Veranstaltung am 27.09.2026 am Rathausplatz")
    date_placeholder = next(ph for ph, surface in mapping.items() if surface == "27.09.2026")
    without_date = masked.replace(date_placeholder, "")
    assert _entities_dropped_by_translation(masked, without_date, mapping) == ["27.09.2026"]


def test_the_date_regex_matches_what_wl_and_oebb_write() -> None:
    assert _DATE_ENTITY_RE.findall("am 27.09.2026, bis 30.9.2026, ab 1.10.2026") == [
        "27.09.2026",
        "30.9.2026",
        "1.10.2026",
    ]
    # Not a date: a clock time, a two-digit year, a version number.
    assert _DATE_ENTITY_RE.findall("13:00 Uhr, 27.09.26, 1.2.3") == []


# ---------------- cache self-heal ----------------


@pytest.mark.parametrize(
    ("source", "cached", "defect"),
    [
        ("U2: Klapprampensperre am 27.09.2026", "U2: folding ramps out of service on 2709.2026", "missing or mangled date"),
        ("27A/28A/29A: Veranstaltung am 27.09.2026", "27A/28A/29A: event on 2709.2026", "missing or mangled date"),
        ("U2: Klapprampensperre am 27.09.2026", "U2: folding ramps out of service on 27.09.2026", None),
        ("Veranstaltung am Rathausplatz", "event at Rathausplatz", None),
    ],
)
def test_the_defect_of_a_cached_value(source: str, cached: str, defect: str | None) -> None:
    assert _cached_translation_defect(source, cached) == defect


def test_an_extra_english_date_does_not_evict() -> None:
    # Only a date the SOURCE carries can be missing; English may add one.
    assert _cached_translation_defect(
        "Veranstaltung am 27.09.2026", "Event on 27.09.2026 (until 28.09.2026)"
    ) is None


def test_a_residual_placeholder_still_evicts() -> None:
    assert _cached_translation_defect(
        "Veranstaltung am Rathausplatz", "event at XENTdeadbeefX0X"
    ) == "residual placeholder"


@pytest.fixture()
def echo_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A model that returns its masked input with an ``EN `` prefix."""
    calls: list[str] = []

    def _pipe(text: str, **_kw: Any) -> list[dict[str, str]]:
        calls.append(text)
        return [{"translation_text": "EN " + text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: _pipe)
    return calls


def test_a_cached_mangled_date_is_re_translated(echo_pipeline: list[str]) -> None:
    source = "Veranstaltung am 27.09.2026 am Rathausplatz"
    state: dict[str, dict[str, Any]] = {
        "wl|27A|x": {"translations": {"en": {"summary": "event on 2709.2026 at Rathausplatz"}}}
    }
    out, ok = _cached_translation(source, "summary", "wl|27A|x", state)
    assert ok
    assert len(echo_pipeline) == 1, "the poisoned value must be a cache miss"
    assert out.startswith("EN ")
    assert "27.09.2026" in out, out
    assert state["wl|27A|x"]["translations"]["en"]["summary"] == out


def test_a_sound_cached_value_is_served_without_the_model(echo_pipeline: list[str]) -> None:
    source = "Veranstaltung am 27.09.2026 am Rathausplatz"
    state: dict[str, dict[str, Any]] = {
        "wl|27A|x": {"translations": {"en": {"summary": "Event on 27.09.2026 at Rathausplatz"}}}
    }
    out, ok = _cached_translation(source, "summary", "wl|27A|x", state)
    assert (out, ok) == ("Event on 27.09.2026 at Rathausplatz", True)
    assert echo_pipeline == []
