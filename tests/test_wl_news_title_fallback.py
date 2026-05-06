"""Regression tests for Bug 13C (WL News POI title fallback).

The TrafficInfo branch of ``fetch_events`` falls back through three
layers: ``ti.get("title") or ti.get("name") or "Meldung"``. The News
POI branch only had two: ``poi.get("title") or "Hinweis"``. When the
WL API returned a POI with empty ``title`` but a populated ``name``,
the resulting feed item collapsed to the literal placeholder
``"Hinweis"`` plus the auto-generated context suffix, losing the
user-meaningful headline that was sitting in the ``name`` field.

The fix mirrors the TrafficInfo fallback: ``poi.get("title") or
poi.get("name") or "Hinweis"``.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_news_poi_title_fallback_uses_name() -> None:
    # Static source-code check: the patched line must include the
    # three-step fallback through ``name``.
    path = Path(__file__).resolve().parent.parent / "src" / "providers" / "wl_fetch.py"
    source = path.read_text(encoding="utf-8")

    # The fallback should exist for the News-POI branch.
    pattern = re.compile(
        r'poi\.get\("title"\)\s*or\s*poi\.get\("name"\)\s*or\s*"Hinweis"',
        re.DOTALL,
    )
    assert pattern.search(source), (
        "News-POI title fallback must include poi.get('name') as middle step"
    )


def test_traffic_info_title_fallback_unchanged() -> None:
    # The TrafficInfo branch already had the right fallback — this
    # guards against accidental regression.
    path = Path(__file__).resolve().parent.parent / "src" / "providers" / "wl_fetch.py"
    source = path.read_text(encoding="utf-8")

    pattern = re.compile(
        r'ti\.get\("title"\)\s*or\s*ti\.get\("name"\)\s*or\s*"Meldung"',
    )
    assert pattern.search(source), (
        "TrafficInfo title fallback must preserve ti.get('name') middle step"
    )
