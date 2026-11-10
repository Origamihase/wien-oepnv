"""WL's "über die Strecke der Linien 43 und 9" has fixed English.

Both German nouns are "line" to the model, so the EN feed read "over the
line line 43 and 9" (KI-Wächter, issue #2188) and "(line lines O and 18)".
The noun phrase is rendered from its parts before the model sees the text,
the preposition in front stays with the model
(``src/feed_build/line_routes.py``).

Mutations checked against this file (each one caught, by the test named):

* ``_mask_line_routes`` is not called in ``_translate_text_attempt`` →
  ``test_route_phrase_rendered_from_its_parts``.
* the number of a noun is ignored ("line" always) →
  ``test_route_phrase_rendered_from_its_parts`` (plural cases).
* ``_line_route_defect`` is not asked in ``_cached_translation`` →
  ``test_cached_doubled_line_is_translated_again``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.feed_build.line_routes import _LINE_ROUTE_RE, _line_route_defect
from tests._conftest.build_feed_bindings import setattr_all

_WL = "Wiener Linien"


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """An echo model, recording its inputs."""
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        return [{"translation_text": text}]

    setattr_all(monkeypatch, build_feed, "_get_translation_pipeline", lambda: pipe)
    return seen


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("über die Strecke der Linien 43 und 9", "über the route of lines 43 and 9"),
        ("über die Strecke der Linien 2 …", "über the route of lines 2 …"),
        ("über die Strecke der Linie 49", "über the route of line 49"),
        ("über die Strecken der Linien 49 und 52", "über the routes of lines 49 and 52"),
        ("(Strecke Linien O und 18)", "(route of lines O and 18)"),
        ("(Strecke Linie D)", "(route of line D)"),
        ("auf der Strecke der Linien 5, 33 bzw. U6", "auf the route of lines 5, 33 or U6"),
        ("Die Strecke der Linie 43A ist gesperrt", "The route of line 43A ist closed"),
    ],
)
def test_route_phrase_rendered_from_its_parts(
    model: list[str], german: str, english: str
) -> None:
    assert build_feed._translate_text_attempt(german, source=_WL) == english
    # The echo model shows what the real one gets: the preposition stays
    # German for it, the noun phrase is one placeholder.
    assert not any("Linie" in shown for shown in model)


@pytest.mark.parametrize(
    "german",
    [
        # No line code after the noun: the model keeps the text.
        "über die Strecke der Linie hinaus",
        # ÖBB's "auf der Strecke" names no line.
        "auf der Strecke zwischen Wien Meidling und Mödling",
        "Streckensperre der Linie 49",
    ],
)
def test_text_without_a_line_list_is_left_alone(german: str) -> None:
    assert _LINE_ROUTE_RE.search(german) is None


def test_cached_doubled_line_is_translated_again(model: list[str]) -> None:
    german = (
        "Umleitung zwischen Dornbach und Mayssengasse über die Strecke der Linien 43 und 9."
    )
    state: dict[str, dict[str, Any]] = {
        "id": {
            "translations": {
                "en": {
                    "desc": "diversion between Dornbach and Mayssengasse over the line line 43 and 9."
                },
            }
        }
    }
    english, ok = build_feed._cached_translation(
        german, "desc", "id", state, source=_WL, category="Störung"
    )
    assert ok
    assert "the route of lines 43 and 9" in english
    assert "line line" not in english
    # The new value is stable: the next build serves it from the cache.
    model.clear()
    again, ok = build_feed._cached_translation(
        german, "desc", "id", state, source=_WL, category="Störung"
    )
    assert ok
    assert again == english
    assert model == []


def test_cached_value_with_the_fixed_english_is_kept() -> None:
    german = "über die Strecke der Linien 9 und 44"
    assert _line_route_defect(german, "Diversion via the route of lines 9 and 44.") is None
    assert _line_route_defect(german, "over the line line 9 and 44") == "line route"
    assert _line_route_defect("Kein Betrieb.", "No service.") is None
