"""Zero-Trust regression tests for ``wl_fetch._extract_wl_items``.

``_get_json`` validates that the WL response top-level is a dict, but the
inner ``data["data"]`` and ``data["data"][key]`` values are still ``Any``.
The previous one-liner ``(data.get("data", {}) or {}).get(key, []) or []``
collapses *falsy* JSON shapes (``None``, ``0``, ``""``, ``[]``, ``{}``) to
a safe empty list, but lets *truthy non-Mapping* / *truthy non-list*
shapes through (``[1, 2]``, ``"abc"``, ``True``, ``42``). A misbehaving /
compromised upstream peer (or a tampered proxy response) could ship one
of those shapes and the resulting ``.get(...)`` call (or the iteration in
``fetch_events``) would raise ``AttributeError`` / ``TypeError`` —
propagating out of ``fetch_events`` and silently disabling the WL cache
refresh.

These tests pin the shape-validation branch so future refactors cannot
drop it.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.providers.wl_fetch import _extract_wl_items


@pytest.mark.parametrize(
    "non_dict_outer",
    [
        None,
        [],
        [{"trafficInfos": []}],
        "unexpected string body",
        42,
        3.14,
        True,
        False,
        0,
        "",
    ],
)
def test_extract_wl_items_rejects_non_dict_outer(non_dict_outer: Any) -> None:
    """A non-dict ``data["data"]`` must collapse to []."""
    assert _extract_wl_items({"data": non_dict_outer}, "trafficInfos") == []
    assert _extract_wl_items({"data": non_dict_outer}, "pois") == []


@pytest.mark.parametrize(
    "non_list_inner",
    [
        None,
        {},
        {"x": 1},
        "unexpected string body",
        42,
        3.14,
        True,
        False,
        0,
        "",
    ],
)
def test_extract_wl_items_rejects_non_list_inner(non_list_inner: Any) -> None:
    """A non-list ``data["data"][key]`` must collapse to []."""
    payload = {"data": {"trafficInfos": non_list_inner}}
    assert _extract_wl_items(payload, "trafficInfos") == []


def test_extract_wl_items_drops_non_dict_elements() -> None:
    """Per-element guards: non-dict items in the list are filtered out."""
    payload = {
        "data": {
            "trafficInfos": [
                {"title": "ok"},
                None,
                "abc",
                42,
                True,
                [1, 2, 3],
                {"title": "also ok"},
            ]
        }
    }
    result = _extract_wl_items(payload, "trafficInfos")
    assert result == [{"title": "ok"}, {"title": "also ok"}]


def test_extract_wl_items_returns_empty_when_key_missing() -> None:
    """Missing key collapses to []."""
    assert _extract_wl_items({"data": {}}, "trafficInfos") == []
    assert _extract_wl_items({}, "trafficInfos") == []


def test_extract_wl_items_happy_path() -> None:
    """Well-formed payload: the list of dicts is returned unchanged."""
    payload = {
        "data": {
            "trafficInfos": [
                {"title": "Störung 1"},
                {"title": "Störung 2"},
            ],
            "pois": [{"title": "News"}],
        }
    }
    assert _extract_wl_items(payload, "trafficInfos") == [
        {"title": "Störung 1"},
        {"title": "Störung 2"},
    ]
    assert _extract_wl_items(payload, "pois") == [{"title": "News"}]
