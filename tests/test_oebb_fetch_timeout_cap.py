"""Verify that ``oebb.fetch_events(timeout=...)`` cannot exceed ``MAX_OEBB_FETCH_TIMEOUT``.

``src/providers/oebb.py:fetch_events`` consumes ``timeout`` as the per-request
budget for ``fetch_content_safe`` (via ``_fetch_xml``) — both the connect and
read timeout. The default callers in ``build_feed.py`` use ``effective_timeout``
(already capped at ``feed_config.MAX_PROVIDER_TIMEOUT``) and
``scripts/update_oebb_cache.py`` uses the 25-second default, but ``fetch_events``
is exported as a public API and a future caller passing an env-controlled or
user-controlled value (e.g. a hypothetical ``OEBB_FETCH_TIMEOUT`` env var) would
otherwise inherit the unbounded shape — at very large values
(``timeout=99999``) a sluggish or attacker-controlled upstream peer could hold
the worker for ~28 hours per fetch, stalling the cron pipeline (Slowloris
vector). Capping inside the function (defense-in-depth) means every caller —
current and future — inherits the ceiling. TIGHTEN-only contract mirrors
``MAX_PRUNE_CACHE_MAX_AGE_HOURS`` (``src/utils/cache.py``) and
``MAX_LOG_PRUNE_KEEP_DAYS`` (``src/feed/logging.py``) — same parameter-boundary
defense-in-depth pattern, applied to the Slowloris-cap drift family.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import pytest

import src.providers.oebb as oebb
from src.providers.oebb import MAX_OEBB_FETCH_TIMEOUT, fetch_events


def test_max_oebb_fetch_timeout_matches_provider_timeout_ceiling() -> None:
    # The cap matches ``feed_config.MAX_PROVIDER_TIMEOUT`` (25 seconds), the
    # orchestrator-level Slowloris ceiling, so no legitimate orchestrator-
    # capped value is ever rejected. The default ``fetch_events`` parameter
    # (25) sits exactly at the cap.
    assert MAX_OEBB_FETCH_TIMEOUT == 25
    assert MAX_OEBB_FETCH_TIMEOUT >= 1


def test_fetch_events_clamps_huge_timeout_to_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller passing ``timeout=99999`` would otherwise let a sluggish or
    attacker-controlled upstream peer stall a worker for ~28 hours. Verify
    the cap collapses the value to ``MAX_OEBB_FETCH_TIMEOUT``."""
    recorded: dict[str, Any] = {}

    def fake_fetch_xml(url: str, timeout: Any) -> ET.Element:
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    result = fetch_events(timeout=99999)
    assert result == []
    assert recorded["timeout"] == MAX_OEBB_FETCH_TIMEOUT


def test_fetch_events_at_cap_passes_cap_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the cap exactly the value passes through unchanged — verifies the
    cap clamps to its documented value, not silently to a tighter bound."""
    recorded: dict[str, Any] = {}

    def fake_fetch_xml(url: str, timeout: Any) -> ET.Element:
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    fetch_events(timeout=MAX_OEBB_FETCH_TIMEOUT)
    assert recorded["timeout"] == MAX_OEBB_FETCH_TIMEOUT


def test_fetch_events_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small ``timeout`` (e.g. 5) must pass through unchanged — the cap
    must not change the unclamped behaviour for legitimate values (tests
    and tighter operator overrides)."""
    recorded: dict[str, Any] = {}

    def fake_fetch_xml(url: str, timeout: Any) -> ET.Element:
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    fetch_events(timeout=5)
    assert recorded["timeout"] == 5


def test_fetch_events_default_timeout_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hardcoded default (25) sits exactly at the cap and must pass
    through unchanged so the production call sites are unaffected."""
    recorded: dict[str, Any] = {}

    def fake_fetch_xml(url: str, timeout: Any) -> ET.Element:
        recorded["timeout"] = timeout
        root = ET.Element("rss")
        ET.SubElement(root, "channel")
        return root

    monkeypatch.setattr(oebb, "_fetch_xml", fake_fetch_xml)

    fetch_events()
    assert recorded["timeout"] == 25
