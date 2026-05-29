"""Regression tests for the round-11 build_feed bundle.

Pins three correctness fixes in ``src/build_feed.py``:

1. ``_call_fetch_with_timeout`` no longer double-calls the fetcher when
   an inner ``TypeError`` is raised. Pre-fix the ``except TypeError:
   return fetch()`` retry caught any TypeError raised from inside the
   fetch body (e.g. an ``int()`` conversion error) and re-invoked the
   fetcher with no kwargs — duplicating HTTP requests, side effects,
   and the ``report.provider_*`` event sequence.
2. ``_submit_network_fetches`` pre-computes the per-group worker limit
   across ALL fetchers before the main submit loop. Pre-fix a sibling
   provider in a shared ``concurrency_key`` group without its own env
   override ran unbounded — silently defeating the operator-intended
   shared cap.
3. ``_save_state`` failure surfaces as a structured warning on the
   ``report`` so dashboards see "degraded" instead of a clean
   ``build_successful=True``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import src.build_feed as build_feed
from src.feed.reporting import RunReport


# ---------------------------------------------------------------------------
# Fix #1 — _call_fetch_with_timeout no longer double-calls on inner TypeError
# ---------------------------------------------------------------------------


def test_call_fetch_with_timeout_does_not_swallow_inner_typeerror() -> None:
    """Pre-fix the ``except TypeError: return fetch()`` retry doubled
    every fetcher that raised a TypeError from INSIDE its body. The
    introspection result from ``_fetch_supports_timeout`` is now
    trusted — the retry is gone, the TypeError propagates and the
    caller's per-provider error handler attributes it correctly."""
    calls: list[tuple[Any, ...]] = []

    def buggy_fetch(*, timeout: int | None = None) -> None:
        calls.append(("call", timeout))
        # Simulate an internal type assertion failure — NOT a kwarg
        # rejection — that pre-fix was misattributed by the catch.
        raise TypeError("internal: timeout must be int")

    with pytest.raises(TypeError, match="internal"):
        build_feed._call_fetch_with_timeout(
            buggy_fetch, timeout=5, supports_timeout=True
        )

    # Single invocation — the swallow-and-retry path is gone.
    assert len(calls) == 1


def test_call_fetch_with_timeout_still_skips_kwarg_when_unsupported() -> None:
    """Regression guard: when ``supports_timeout=False`` the fetcher is
    called with no kwargs (legacy callable signature)."""
    received: list[tuple[Any, ...]] = []

    def legacy_fetch() -> str:
        received.append(("called",))
        return "ok"

    result = build_feed._call_fetch_with_timeout(
        legacy_fetch, timeout=5, supports_timeout=False
    )
    assert result == "ok"
    assert received == [("called",)]


# ---------------------------------------------------------------------------
# Fix #2 — _submit_network_fetches shares the semaphore across the group
# ---------------------------------------------------------------------------


def test_submit_network_fetches_shares_semaphore_across_concurrency_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When two providers share a ``concurrency_key`` and only one of
    them has an env-set worker limit, the OTHER must also pick up the
    shared semaphore. Pre-fix the single-pass loop registered a
    semaphore only when the CURRENT fetcher's own per-provider limit
    was set, so the sibling ran unbounded against the same upstream."""
    # Two distinct fetcher objects sharing one concurrency_key.
    fetch_a = MagicMock(__name__="fetch_a")
    fetch_b = MagicMock(__name__="fetch_b")

    semaphores_seen: dict[str, Any] = {}

    def fake_build_run_fetch(
        fetch: Any,
        timeout: int,
        supports_timeout: bool,
        semaphore: Any,
        provider_name: str,
    ) -> Any:
        # Record which semaphore each fetcher got.
        semaphores_seen[provider_name] = semaphore
        return lambda: None  # no-op runner

    # ``_provider_concurrency_key`` returns the SAME key for both
    # providers — they form one group.
    monkeypatch.setattr(
        build_feed, "_provider_concurrency_key",
        lambda fetch, name: "shared-pool",
    )
    # Only provider B has an env-set positive limit.
    monkeypatch.setattr(
        build_feed, "_provider_worker_limit",
        lambda fetch, env_name, name, key: 3 if name == "provider_b" else None,
    )
    monkeypatch.setattr(
        build_feed, "_provider_timeout_override",
        lambda *_a, **_kw: 5,
    )
    monkeypatch.setattr(
        build_feed, "_fetch_supports_timeout", lambda *_a, **_kw: False,
    )
    monkeypatch.setattr(build_feed, "_build_run_fetch", fake_build_run_fetch)

    executor = MagicMock()
    executor.submit = lambda func: MagicMock()
    report = RunReport(statuses=[("provider_a", True), ("provider_b", True)])

    build_feed._submit_network_fetches(
        executor,
        [fetch_a, fetch_b],
        provider_names={fetch_a: "provider_a", fetch_b: "provider_b"},
        provider_envs={fetch_a: "PROV_A_ENABLE", fetch_b: "PROV_B_ENABLE"},
        report=report,
    )

    # Both providers must share the SAME semaphore object — pre-fix
    # provider A had no semaphore while provider B was throttled.
    assert semaphores_seen["provider_a"] is not None
    assert semaphores_seen["provider_b"] is not None
    assert semaphores_seen["provider_a"] is semaphores_seen["provider_b"]


# ---------------------------------------------------------------------------
# Fix #3 — _save_state failure surfaces as a warning on the report
# ---------------------------------------------------------------------------


def test_save_state_failure_calls_report_add_warning_in_main() -> None:
    """AST sentinel: ``main()`` MUST call ``report.add_warning(...)``
    inside the ``except`` arm that wraps the ``_save_state(...)`` call.

    Pre-fix the exception was log-only and ``report.finish`` still
    recorded ``build_successful=True`` with no structured warning, so
    monitoring / dashboards saw a fully-successful build while
    first_seen / translations / stats drifted out of sync with the
    on-disk feed.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(build_feed))
    main_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break
    assert main_func is not None, "build_feed.main not found"

    found_warning_call = False
    for node in ast.walk(main_func):
        if not isinstance(node, ast.Try):
            continue
        # Try body calls _save_state?
        calls_save_state = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_save_state"
            for n in ast.walk(node)
        )
        if not calls_save_state:
            continue
        # Any except handler calls *.add_warning?
        for handler in node.handlers:
            for inner in ast.walk(handler):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "add_warning"
                ):
                    found_warning_call = True
                    break
            if found_warning_call:
                break
        if found_warning_call:
            break

    assert found_warning_call, (
        "build_feed.main() must call report.add_warning(...) inside the "
        "except arm wrapping _save_state(...). Pre-fix the exception was "
        "silently logged and the build still reported as fully successful."
    )
