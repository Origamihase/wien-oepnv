"""Verify that ``PROVIDER_TIMEOUT`` env overrides cannot exceed ``MAX_PROVIDER_TIMEOUT``.

``feed_config.PROVIDER_TIMEOUT`` and the per-provider ``PROVIDER_TIMEOUT_<X>``
overrides resolved by ``build_feed._provider_timeout_override`` are consumed
by the orchestrator as both the per-fetch HTTP timeout and the future
deadline. Without an upper bound an env override such as
``PROVIDER_TIMEOUT=99999`` (intentional misconfig, leaked CI env, compromised
secret store) would let a sluggish or attacker-controlled upstream peer
hold a worker for ~28 hours per fetch — same Slowloris primitive as the
previously-capped ``VOR_HTTP_TIMEOUT`` and ``REQUEST_TIMEOUT_S``.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from src.feed import config as feed_config


def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda timeout=None: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda timeout=None: [])
    vor = types.ModuleType("providers.vor")
    setattr(vor, "fetch_events", lambda timeout=None: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module


def test_max_provider_timeout_matches_default() -> None:
    # The cap must equal the default so env overrides can only TIGHTEN the
    # value (never raise it above the documented Slowloris ceiling). Mirrors
    # ``HTTP_TIMEOUT = min(VOR_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT)`` in
    # ``src/providers/vor.py``.
    from src.config.defaults import DEFAULT_PROVIDER_TIMEOUT

    assert feed_config.MAX_PROVIDER_TIMEOUT == DEFAULT_PROVIDER_TIMEOUT


def test_provider_timeout_clamped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_TIMEOUT", "99999")
    feed_config.refresh_from_env()
    assert feed_config.PROVIDER_TIMEOUT == feed_config.MAX_PROVIDER_TIMEOUT


def test_provider_timeout_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_TIMEOUT", "5")
    feed_config.refresh_from_env()
    assert feed_config.PROVIDER_TIMEOUT == 5


def test_provider_timeout_zero_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero is a documented sentinel meaning "no time" — operators rely on it
    # to disable network providers in tests. The cap must not promote it.
    monkeypatch.setenv("PROVIDER_TIMEOUT", "0")
    feed_config.refresh_from_env()
    assert feed_config.PROVIDER_TIMEOUT == 0


def test_provider_timeout_negative_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``get_int_env`` followed by ``max(..., 0)`` already coerces negatives to
    # zero. The new cap must not change that lower-bound contract.
    monkeypatch.setenv("PROVIDER_TIMEOUT", "-5")
    feed_config.refresh_from_env()
    assert feed_config.PROVIDER_TIMEOUT == 0


def test_per_provider_timeout_override_clamped_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout: Any = None) -> list[dict[str, str]]:
        return []

    slow_fetch.__name__ = "slow"

    # Per-provider override mirrors the global ``PROVIDER_TIMEOUT`` shape; an
    # uncapped env value such as 99999 must be clamped at the same ceiling.
    monkeypatch.setenv("PROVIDER_TIMEOUT_SLOW", "99999")
    override = build_feed._provider_timeout_override(slow_fetch, "SLOW_ENABLE", "SLOW")
    assert override == feed_config.MAX_PROVIDER_TIMEOUT


def test_per_provider_timeout_override_below_cap_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout: Any = None) -> list[dict[str, str]]:
        return []

    slow_fetch.__name__ = "slow"

    monkeypatch.setenv("PROVIDER_TIMEOUT_SLOW", "3")
    override = build_feed._provider_timeout_override(slow_fetch, "SLOW_ENABLE", "SLOW")
    assert override == 3


def test_per_provider_timeout_override_unset_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    def slow_fetch(timeout: Any = None) -> list[dict[str, str]]:
        return []

    slow_fetch.__name__ = "slow"

    monkeypatch.delenv("PROVIDER_TIMEOUT_SLOW", raising=False)
    override = build_feed._provider_timeout_override(slow_fetch, "SLOW_ENABLE", "SLOW")
    assert override is None
