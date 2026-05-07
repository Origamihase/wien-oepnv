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

from src.providers.wl_fetch import _best_ts, _coerce_dict, _extract_wl_items


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


@pytest.mark.parametrize(
    "non_dict",
    [None, [1, 2], "abc", 42, 3.14, True, False, 0, "", [{"a": 1}]],
)
def test_coerce_dict_collapses_non_dict_shapes(non_dict: Any) -> None:
    """Truthy *and* falsy non-dicts both collapse to ``{}`` (Round 4 drift)."""
    assert _coerce_dict(non_dict) == {}


def test_coerce_dict_passes_through_dict() -> None:
    """Real dict payloads pass through unchanged."""
    payload = {"start": "2026-01-01T00:00:00Z", "nested": {"a": 1}}
    assert _coerce_dict(payload) is payload


@pytest.mark.parametrize(
    "non_dict_field",
    ["evil_string", [1, 2, 3], 42, True, 3.14, [{"start": "2026-01-01"}]],
)
def test_best_ts_survives_truthy_non_dict_time(non_dict_field: Any) -> None:
    """A compromised upstream shipping a truthy non-dict ``time`` field must
    not raise out of ``_best_ts`` — that would propagate out of ``fetch_events``
    and disable the WL cache refresh entirely."""
    obj = {"time": non_dict_field, "updated": "2026-01-01T00:00:00Z"}
    # Falls back to ``updated`` because ``time.start`` / ``time.end`` are
    # unreachable when ``time`` collapses to ``{}``.
    result = _best_ts(obj)
    assert result is not None
    assert result.year == 2026


@pytest.mark.parametrize(
    "non_dict_field",
    ["evil_string", [1, 2, 3], 42, True, 3.14],
)
def test_best_ts_survives_truthy_non_dict_attributes(non_dict_field: Any) -> None:
    """A compromised upstream shipping a truthy non-dict ``attributes`` field
    must not raise out of ``_best_ts``."""
    obj = {"time": {}, "attributes": non_dict_field}
    # No usable timestamp anywhere -> returns ``None`` cleanly, never raises.
    assert _best_ts(obj) is None


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
