# ruff: noqa: S603, S607
"""Der Prompt des KI-Wächters (scripts/ki_waechter_prompt.md): Platzhalter der Eingabe,
Klassen, keine Fälle der Replay-Stände.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import pytest

from scripts import ki_waechter as kw

from ._support import (
    REPO,
)


def test_the_prompt_ends_with_the_input() -> None:
    template = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    prompt = kw.build_prompt("## Meldung 1\nINHALT", template)
    assert prompt.rstrip().endswith("## Meldung 1\nINHALT") and "{EINGABE}" not in prompt
    with pytest.raises(ValueError):
        kw.build_prompt("x", "ohne Platzhalter")


def test_prompt_names_its_input_and_not_the_backtest_cases() -> None:
    prompt = (REPO / "scripts/ki_waechter_prompt.md").read_text(encoding="utf-8")
    assert prompt.count("{EINGABE}") == 1
    for klasse in kw.CLASSES:
        assert f"- {klasse} " in prompt
    # The pull-request run replays 27.10. and 30.10.2026; the prompt must
    # not hand the model those answers.
    for case in ("Algenweg", "algae", "All Saints", "Allerheiligen", "Liechtensteinstraße"):
        assert case not in prompt
