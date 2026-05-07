"""Verify ``vor.fetch_events`` / ``fetch_vor_disruptions`` cap timeout at ``MAX_VOR_FETCH_TIMEOUT``.

``src/providers/vor.py:fetch_vor_disruptions`` (and its delegating wrapper
``fetch_events``) consume ``timeout`` as the per-request budget for
``fetch_content_safe`` (via ``_fetch_departure_board_for_station``: ``timeout
or HTTP_TIMEOUT``). The env-source clamp on ``HTTP_TIMEOUT`` —
``min(VOR_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT)`` — bounds operator-controlled
config, but a caller-provided ``timeout=99999`` was previously truthy and
bypassed the ``or HTTP_TIMEOUT`` fallback entirely, letting a sluggish or
attacker-controlled VAO peer hold a worker for ~28 hours per fetch (Slowloris
vector). Capping at the public API entry point (defense-in-depth) means every
caller — current and future — inherits the ceiling. TIGHTEN-only contract
mirrors ``MAX_OEBB_FETCH_TIMEOUT`` (``src/providers/oebb.py``) and
``MAX_WL_FETCH_TIMEOUT`` (``src/providers/wl_fetch.py``) — same parameter-
boundary defense-in-depth pattern, applied to the VOR sibling that the
2026-05-07 Round 4 journal entry explicitly named as still-open.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.providers.vor as vor
from src.providers.vor import MAX_VOR_FETCH_TIMEOUT, fetch_events, fetch_vor_disruptions


def test_max_vor_fetch_timeout_matches_default_http_timeout() -> None:
    # The cap matches ``DEFAULT_HTTP_TIMEOUT`` (15 seconds), the VOR-specific
    # Slowloris ceiling — chosen rather than ``feed_config.MAX_PROVIDER_TIMEOUT``
    # (25 seconds) because VOR has documented a tighter local contract.
    assert MAX_VOR_FETCH_TIMEOUT == vor.DEFAULT_HTTP_TIMEOUT
    assert MAX_VOR_FETCH_TIMEOUT == 15
    assert MAX_VOR_FETCH_TIMEOUT >= 1


def _install_recorder(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, Any]
) -> None:
    """Replace VOR's network surface with a stub that records the timeout
    propagated to ``_fetch_departure_board_for_station`` and returns no data
    (so the rest of ``fetch_vor_disruptions`` short-circuits cleanly without a
    network)."""

    def fake_refresh_access_credentials() -> str:
        return "stub-token"

    def fake_load_request_count() -> tuple[str, int]:
        return ("2099-01-01", 0)

    def fake_get_configured_stations() -> list[str]:
        return ["12345"]

    def fake_select_stations_for_run(station_ids: list[str]) -> list[str]:
        return list(station_ids)

    def fake_fetch_departure_board_for_station(
        station_id: str,
        now_local: Any,
        counter: Any = None,
        session: Any = None,
        timeout: Any = None,
    ) -> None:
        recorded["timeout"] = timeout
        return None

    monkeypatch.setattr(vor, "refresh_access_credentials", fake_refresh_access_credentials)
    monkeypatch.setattr(vor, "load_request_count", fake_load_request_count)
    monkeypatch.setattr(vor, "get_configured_stations", fake_get_configured_stations)
    monkeypatch.setattr(vor, "select_stations_for_run", fake_select_stations_for_run)
    monkeypatch.setattr(
        vor,
        "_fetch_departure_board_for_station",
        fake_fetch_departure_board_for_station,
    )


def test_fetch_vor_disruptions_clamps_huge_timeout_to_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller passing ``timeout=99999`` would otherwise let a sluggish or
    attacker-controlled VAO peer stall a worker for ~28 hours. Verify the cap
    collapses the value to ``MAX_VOR_FETCH_TIMEOUT``."""
    recorded: dict[str, Any] = {}
    _install_recorder(monkeypatch, recorded)

    with pytest.raises(vor.RequestException):
        fetch_vor_disruptions(timeout=99999)
    assert recorded["timeout"] == MAX_VOR_FETCH_TIMEOUT


def test_fetch_vor_disruptions_at_cap_passes_cap_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the cap exactly the value passes through unchanged — verifies the
    cap clamps to its documented value, not silently to a tighter bound."""
    recorded: dict[str, Any] = {}
    _install_recorder(monkeypatch, recorded)

    with pytest.raises(vor.RequestException):
        fetch_vor_disruptions(timeout=MAX_VOR_FETCH_TIMEOUT)
    assert recorded["timeout"] == MAX_VOR_FETCH_TIMEOUT


def test_fetch_vor_disruptions_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small ``timeout`` (e.g. 5) must pass through unchanged — the cap
    must not change the unclamped behaviour for legitimate values (tests
    and tighter operator overrides)."""
    recorded: dict[str, Any] = {}
    _install_recorder(monkeypatch, recorded)

    with pytest.raises(vor.RequestException):
        fetch_vor_disruptions(timeout=5)
    assert recorded["timeout"] == 5


def test_fetch_vor_disruptions_none_timeout_uses_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``timeout=None`` (the default) must resolve to ``MAX_VOR_FETCH_TIMEOUT``
    — the existing ``timeout or HTTP_TIMEOUT`` fallback semantics are preserved
    by the ``timeout or MAX_VOR_FETCH_TIMEOUT`` form."""
    recorded: dict[str, Any] = {}
    _install_recorder(monkeypatch, recorded)

    with pytest.raises(vor.RequestException):
        fetch_vor_disruptions()
    assert recorded["timeout"] == MAX_VOR_FETCH_TIMEOUT


def test_fetch_events_delegates_clamp_to_fetch_vor_disruptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``fetch_events`` public wrapper delegates to
    ``fetch_vor_disruptions(station_ids, timeout=timeout)`` — verify the
    clamp applies through the wrapper too so both ``__all__`` entry points
    inherit the ceiling without per-wrapper duplicate clamps."""
    recorded: dict[str, Any] = {}
    _install_recorder(monkeypatch, recorded)

    with pytest.raises(vor.RequestException):
        fetch_events(timeout=99999)
    assert recorded["timeout"] == MAX_VOR_FETCH_TIMEOUT
