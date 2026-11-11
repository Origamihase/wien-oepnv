"""A capital ``X`` of the source glued to a masked entity must survive.

The masker shields arrows, dashes and other glyphs with ``XENT…X`` placeholders.
The two repairs for the model's ``X`` debris (``_PLACEHOLDER_LEADING_X_RE``
in front of a placeholder, ``_PLACEHOLDER_DEBRIS_RE`` behind it) cannot tell
an ``X`` the model added from one the source wrote, and run on every unmask.
So "REX→Wien" came back as "RE →Wien" and "→X" as "→", and the property
``test_mask_unmask_round_trip_identity`` failed whenever Hypothesis drew such
a text (found 10.11.2026 as "X⬀" → "⬀").

``_absorb_glued_x`` hands that ``X`` to the placeholder on the mask side, so
the repairs never see it and the model's own debris is still repaired.

Mutations checked against this file (each one caught):

* ``_absorb_glued_x`` call removed from ``_mask_entities`` → every test here.
* the ``lead`` group dropped from ``_GLUED_X_RE`` → ``REX→Wien``, ``X⬀``.
* the ``trail`` group dropped → ``→X``, ``→0X``.
* a shared placeholder rewritten in place → ``→ Wien →X``.
"""

from __future__ import annotations

from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from src import build_feed
from tests._conftest.build_feed_bindings import setattr_all

_GLUED = (
    "X⬀",
    "REX→Wien",
    "Linie REX–Wien Meidling",
    "MAX–Winter-Platz",
    "→X",
    "X→X",
    "→0X",
    "A–X.",
    "WLB X→ Baden",
    "X0XX→",
    "→X→",
    "→→X",
    "XX→XX→X",
)


@pytest.mark.parametrize("text", _GLUED)
def test_glued_x_round_trips(text: str) -> None:
    masked, mapping = build_feed._mask_entities(text)
    assert build_feed._unmask_entities(masked, mapping) == text


@pytest.mark.parametrize("text", _GLUED)
def test_masked_text_gives_the_debris_repair_nothing_to_do(text: str) -> None:
    masked, _ = build_feed._mask_entities(text)
    assert build_feed._normalise_placeholder_debris(masked) == masked


def test_a_shared_placeholder_keeps_its_plain_surface() -> None:
    # The arrow stands twice; only the second carries the X, so it gets a
    # placeholder of its own and the first keeps the bare arrow.
    masked, mapping = build_feed._mask_entities("→ Wien →X")
    assert sorted(mapping.values()) == ["→", "→X"]
    assert build_feed._unmask_entities(masked, mapping) == "→ Wien →X"


def test_text_without_glued_x_masks_as_before() -> None:
    masked, mapping = build_feed._mask_entities("REX 7 → Wien")
    assert "→" in mapping.values()
    assert not any("X" in surface for surface in mapping.values())


def test_model_debris_is_still_repaired() -> None:
    masked, mapping = build_feed._mask_entities("Betrieb ab Augasse → Börse")
    placeholder = next(p for p, s in mapping.items() if s == "→")
    debris = masked.replace(placeholder, "X" + placeholder + "X")
    assert build_feed._unmask_entities(debris, mapping) == "Betrieb ab Augasse → Börse"


def test_the_x_reaches_the_english_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def model(text: str, **kwargs: Any) -> list[dict[str, str]]:
        return [{"translation_text": text.replace("Zug", "Train")}]

    setattr_all(monkeypatch, build_feed, "_get_translation_pipeline", lambda: model)
    out = build_feed._translate_text_attempt("Zug REX→Wien")
    assert out is not None and "REX→Wien" in out, out


_X_HEAVY = st.lists(
    st.sampled_from(["X", "XX", "0", "1", "→", "–", "↔", " ", "REX", "Wien Meidling", "U1", "A", "XENT"]),
    max_size=12,
).map("".join)


@given(text=_X_HEAVY)
@settings(max_examples=500, deadline=None, derandomize=True)
def test_x_heavy_texts_round_trip(text: str) -> None:
    masked, mapping = build_feed._mask_entities(text)
    assert build_feed._unmask_entities(masked, mapping) == text
