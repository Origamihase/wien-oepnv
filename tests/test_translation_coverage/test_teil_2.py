"""Teil 2 der Tests aus tests/test_translation_coverage.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed
from src.feed_types import FeedItem
from tests._conftest.build_feed_bindings import setattr_all


def test_suffixed_station_name_preserved_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end regression for ``Schloss Hetzendorf`` → ``lock
    Hetzendorf``. The station name (a ``(WL)``-suffixed Vienna stop)
    must survive verbatim through the full ``_format_item_content``
    EN path — title AND description."""
    def fake(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Translate the surrounding German connectors; leave the
        # XENT/XGLO placeholders the masker injected untouched (the
        # canonical Marian behaviour the production pipeline relies on).
        out = (
            text.replace("Haltestellenverlegung der Linien", "Stop relocation of lines")
            .replace(" und ", " and ")
            .replace(" in Richtung ", " towards ")
            .replace("Haltestelle:", "Stop:")
            .replace("Von:", "From:")
            .replace("Nach:", "To:")
        )
        return [{"translation_text": out}]

    setattr_all(monkeypatch, build_feed, "_get_translation_pipeline", lambda: fake)
    item = cast(
        FeedItem,
        {
            "title": "16A/62: Schloss Hetzendorf",
            "description": (
                "Haltestellenverlegung der Linien 62 und 16A in Richtung "
                "Oper Haltestelle: Schloss Hetzendorf Von: Hetzendorfer "
                "Straße 79"
            ),
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "wl-schloss-1",
            "link": "",
        },
    )
    state: dict[str, dict[str, Any]] = {}
    out = build_feed._format_item_content(
        item,
        ident="wl-schloss-1",
        starts_at=datetime(2026, 5, 4, 16, 30, tzinfo=UTC),
        ends_at=None,
        lang="en",
        state=state,
    )
    # The mistranslation must be gone from BOTH fields.
    assert "lock" not in out.title_out.lower()
    assert "castle" not in out.title_out.lower()
    assert "Schloss Hetzendorf" in out.title_out
    assert "lock" not in out.desc_text_truncated.lower()
    assert "castle" not in out.desc_text_truncated.lower()
    assert "Schloss Hetzendorf" in out.desc_text_truncated
