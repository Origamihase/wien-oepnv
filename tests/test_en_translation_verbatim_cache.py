"""A translation whose correct English IS the German must stop re-running.

Live regression, 2026-09-12 — in every build log, every time::

    Cached EN translation for …TRACKINFO&910806/title equals source; retrying.

``_cached_translation`` reads "stored translation equals the source" as
evidence of an earlier BROKEN build that persisted the German text as the
translation, and re-translates. For a title made only of station names —
``Wien Hauptbahnhof ↔ Felixdorf`` — the correct English *is* the German, so
the condition holds forever. The model re-ran on every build and never once
produced a different answer. In the persisted state 227 entries carry such a
title.

A stored string cannot tell "a broken build wrote the source here" apart from
"the model ran and its output equals the source". ``_VERBATIM_FIELDS_KEY``
can, because it is written only where the two differ: after
``_translate_text_attempt`` returned a non-``None`` result. That is the whole
idea, and the tests below pin both halves of it — the loop closes, AND the
drift guard keeps its teeth for entries that carry no mark.

Costs only, no feed content: a looping item renders the same EN output either
way. Builds run every 30 minutes.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from src import build_feed

_STATION_ONLY = "Wien Hauptbahnhof ↔ Felixdorf"


class _Model:
    """Stand-in for the NMT pass; records every call."""

    def __init__(self, returns: str | None) -> None:
        self.returns = returns
        self.calls: list[str] = []

    def __call__(
        self,
        text: str,
        ident: str = "",
        *,
        source: str | None = None,
        category: str | None = None,
    ) -> str | None:
        self.calls.append(text)
        return text if self.returns is _ECHO else self.returns


_ECHO = "<echo the input>"


def _run_builds(
    model: _Model, state: dict[str, dict[str, Any]], text: str, count: int
) -> None:
    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        for _ in range(count):
            build_feed._cached_translation(text, "title", "oebb-835981", state)


# ---------------- the loop ----------------


def test_a_station_only_title_is_translated_once_not_every_build() -> None:
    """The defect, measured: five builds used to mean five model runs."""
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {}
    _run_builds(model, state, _STATION_ONLY, 5)

    assert len(model.calls) == 1, (
        f"das Modell lief {len(model.calls)}x für 5 Builds — die Schleife ist offen"
    )


def test_the_mark_records_the_field_that_was_verified() -> None:
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {}
    _run_builds(model, state, _STATION_ONLY, 1)

    translations = state["oebb-835981"]["translations"]
    assert translations[build_feed._VERBATIM_FIELDS_KEY] == ["title"]
    assert translations["en"]["title"] == _STATION_ONLY


def test_the_cached_value_is_still_served_unchanged() -> None:
    """Closing the loop must not change what the EN subscriber receives."""
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {}
    _run_builds(model, state, _STATION_ONLY, 1)

    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        out, ok = build_feed._cached_translation(
            _STATION_ONLY, "title", "oebb-835981", state
        )
    assert (out, ok) == (_STATION_ONLY, True)


def test_the_mark_survives_the_json_round_trip() -> None:
    """State is persisted as JSON; a set would not survive, a list does."""
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {}
    _run_builds(model, state, _STATION_ONLY, 1)

    revived = json.loads(json.dumps(state))
    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        build_feed._cached_translation(_STATION_ONLY, "title", "oebb-835981", revived)
    assert len(model.calls) == 1, "nach dem Neuladen lief das Modell erneut"


# ---------------- the drift guard must keep its teeth ----------------


def test_a_failed_translation_never_earns_the_mark() -> None:
    """The distinction the whole fix rests on.

    A build that could not translate returns ``None`` and persists nothing —
    so it can never claim "identical is correct" about a text it never
    successfully processed.
    """
    model = _Model(None)
    state: dict[str, dict[str, Any]] = {}
    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        out, ok = build_feed._cached_translation(_STATION_ONLY, "title", "x", state)

    assert (out, ok) == (_STATION_ONLY, False)
    translations = state["x"]["translations"]
    assert build_feed._VERBATIM_FIELDS_KEY not in translations
    assert "title" not in translations["en"]


def test_an_unmarked_legacy_entry_is_still_retried() -> None:
    """Pre-fix state carries the source as the "translation" and no mark.

    That is exactly the broken-build case the guard exists for, and it must
    keep re-translating — the fix narrows the retry, it does not remove it.
    """
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {
        "legacy": {"translations": {"en": {"title": _STATION_ONLY}}}
    }
    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        build_feed._cached_translation(_STATION_ONLY, "title", "legacy", state)

    assert len(model.calls) == 1, "der Drift-Guard darf Altbestand nicht durchwinken"
    # …and this run is what converts it: one retry, then marked.
    assert state["legacy"]["translations"][build_feed._VERBATIM_FIELDS_KEY] == ["title"]


def test_the_mark_is_withdrawn_when_the_model_translates_after_all() -> None:
    """A stale mark must not outlive the output it describes."""
    state: dict[str, dict[str, Any]] = {
        "z": {"translations": {"en": {}, build_feed._VERBATIM_FIELDS_KEY: ["title"]}}
    }
    with patch.object(
        build_feed, "_translate_text_attempt", return_value="Vienna Hbf ↔ Felixdorf"
    ):
        out, ok = build_feed._cached_translation(_STATION_ONLY, "title", "z", state)

    assert (out, ok) == ("Vienna Hbf ↔ Felixdorf", True)
    assert build_feed._VERBATIM_FIELDS_KEY not in state["z"]["translations"]


def test_a_residual_placeholder_beats_the_mark() -> None:
    """A raw sentinel is never served from cache — mark or no mark.

    The self-heal guard exists because a mangled placeholder once reached
    subscribers (audit 2026-09-05 §5). The new early return must not open a
    path around it.
    """
    model = _Model("Wien Hauptbahnhof ↔ Felixdorf")
    corrupted = "Wien X4X Felixdorf"
    state: dict[str, dict[str, Any]] = {
        "w": {
            "translations": {
                "en": {"title": corrupted},
                build_feed._VERBATIM_FIELDS_KEY: ["title"],
            }
        }
    }
    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        out, _ok = build_feed._cached_translation(corrupted, "title", "w", state)

    assert len(model.calls) == 1, "der Self-Heal muss den Marker schlagen"
    assert "X4X" not in out


# ---------------- lifecycle ----------------


def test_an_epoch_eviction_drops_the_mark_with_the_translations() -> None:
    """The mark describes what THIS epoch's pipeline produced.

    A newer epoch with better masking or glossary may translate the same text
    after all, so the mark must not outlive the strings it belongs to.
    """
    state: dict[str, dict[str, Any]] = {
        "v": {
            "translations": {
                "epoch": 1,
                "en": {"title": _STATION_ONLY},
                build_feed._VERBATIM_FIELDS_KEY: ["title"],
            }
        }
    }
    build_feed._evict_stale_translations("v", state)

    translations = state["v"]["translations"]
    assert "en" not in translations
    assert build_feed._VERBATIM_FIELDS_KEY not in translations


def test_a_translatable_text_leaves_no_mark_behind() -> None:
    """No state bloat: the key is absent unless a field actually earned it.

    The persisted state holds 2500+ entries; an empty list on each would be
    pure weight.
    """
    state: dict[str, dict[str, Any]] = {}
    with patch.object(
        build_feed, "_translate_text_attempt", return_value="track construction works"
    ):
        build_feed._cached_translation("Gleisbauarbeiten", "title", "n", state)

    assert build_feed._VERBATIM_FIELDS_KEY not in state["n"]["translations"]


def test_two_fields_are_tracked_independently() -> None:
    """``title`` and ``summary`` are cached separately and can differ."""
    state: dict[str, dict[str, Any]] = {}
    with patch.object(build_feed, "_translate_text_attempt", side_effect=lambda t, *a, **k: t):
        build_feed._cached_translation(_STATION_ONLY, "title", "m", state)
    with patch.object(
        build_feed, "_translate_text_attempt", return_value="Due to construction works …"
    ):
        build_feed._cached_translation("Wegen Bauarbeiten …", "summary", "m", state)

    assert state["m"]["translations"][build_feed._VERBATIM_FIELDS_KEY] == ["title"]


# ---------------- through the real overlay ----------------


def _content(title: str, summary: str) -> build_feed.FormattedContent:
    return build_feed.FormattedContent(
        guid="g",
        link="https://example.com",
        title_cdata=f"<![CDATA[{title}]]>",
        desc_text_truncated=summary,
        desc_cdata=summary,
        raw_desc=summary,
        title_out=title,
        desc_html=summary,
    )


@pytest.mark.parametrize("builds", [2, 5])
def test_the_overlay_stops_re_translating_across_builds(builds: int) -> None:
    """End to end: ``_apply_lang_overlay`` is the real caller."""
    model = _Model(_ECHO)
    state: dict[str, dict[str, Any]] = {}
    base = _content(_STATION_ONLY, _STATION_ONLY)

    with patch.object(build_feed, "_translate_text_attempt", side_effect=model):
        for _ in range(builds):
            build_feed._apply_lang_overlay(
                base, _STATION_ONLY, "[Seit 01.09.2026]", "oebb-1", "en", state
            )

    # title + summary, once each — not once per build.
    assert len(model.calls) == 2, model.calls
    translations = state["oebb-1"]["translations"]
    assert translations["epoch"] == build_feed._TRANSLATION_CACHE_EPOCH
    assert translations[build_feed._VERBATIM_FIELDS_KEY] == ["summary", "title"]
