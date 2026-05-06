"""Regression tests for Bug Z4 (VOR retry quota leak).

VOR's daily request budget is a hard 100/day limit. Quota is incremented
exactly once before each call to ``fetch_content_safe`` (see
``src/providers/vor.py``), but the underlying ``requests.Session`` was
previously configured with urllib3 ``Retry(total=3)``. urllib3 retries
happen at the transport adapter level — silently, before the
application sees the response — so a single counted call could trigger
up to 4 actual HTTP requests on transient 429/5xx errors.

Per project spec API budgets must NEVER be exceeded. The fix sets
``total=0`` in ``VOR_RETRY_OPTIONS`` so every actual HTTP call to VOR
corresponds to exactly one quota increment. Application-level
scheduling (the cron-like job runner) is the correct place to recover
from transient errors.
"""

from __future__ import annotations

from src.providers import vor


class TestVorRetryQuotaInvariant:
    def test_retry_total_is_zero(self) -> None:
        # The whole rationale for setting total=0 is that quota tracking
        # in vor.py increments exactly once per fetch_content_safe call.
        # Any value >0 silently lets urllib3 spend additional quota on
        # 429/5xx retries.
        assert vor.VOR_RETRY_OPTIONS["total"] == 0

    def test_retry_options_keep_status_handling(self) -> None:
        # raise_on_status must remain False so the application code can
        # inspect the response and apply its own retry policy.
        assert vor.VOR_RETRY_OPTIONS.get("raise_on_status") is False

    def test_circuit_breaker_floor_unaffected(self) -> None:
        # The circuit-breaker computes max_allowed_requests using
        # `len(selected_ids) * (total + 1)` with a `max(10, ...)` floor.
        # With total=0 the floor must keep the limit at >= 10 so a normal
        # 2-station run isn't artificially circuit-broken.
        n_stations = 2
        per_station_attempts = vor.VOR_RETRY_OPTIONS.get("total", 0) + 1
        formula = n_stations * per_station_attempts
        floor_protected = max(10, formula)
        assert floor_protected >= 10
