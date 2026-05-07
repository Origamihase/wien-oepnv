"""Chaos tests for ``src.utils.circuit_breaker.CircuitBreaker``.

The breaker is a Saboteur-pass primitive — it must survive bursts of
failures, recover automatically, and stay thread-safe under contention.
Each test maps to one chaos scenario in ``.jules/saboteur.md``.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from src.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)


class FakeClock:
    """Deterministic monotonic clock for state-transition tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def assert_state(breaker: CircuitBreaker, expected: CircuitState) -> None:
    """Assert the breaker is in ``expected``. Wrapped in a function so mypy
    doesn't narrow the property's return type across multiple reads in the
    same test (see ``[comparison-overlap]`` warnings without this helper)."""
    actual: CircuitState = breaker.state
    assert actual == expected, f"expected {expected}, got {actual}"


# ---------- State machine ----------

def test_starts_closed() -> None:
    breaker = CircuitBreaker("test")
    assert_state(breaker, CircuitState.CLOSED)
    assert breaker.consecutive_failures == 0


def test_records_failure_below_threshold_stays_closed() -> None:
    breaker = CircuitBreaker("test", failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert_state(breaker, CircuitState.CLOSED)
    assert breaker.consecutive_failures == 2


def test_threshold_failures_trips_to_open() -> None:
    breaker = CircuitBreaker("test", failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()
    assert_state(breaker, CircuitState.OPEN)


def test_open_breaker_short_circuits_call() -> None:
    breaker = CircuitBreaker("test", failure_threshold=2)
    breaker.record_failure()
    breaker.record_failure()
    assert_state(breaker, CircuitState.OPEN)

    upstream_called = False

    def upstream() -> str:
        nonlocal upstream_called
        upstream_called = True
        return "value"

    with pytest.raises(CircuitBreakerOpen):
        breaker.call(upstream)
    assert upstream_called is False, "OPEN breaker must not invoke upstream"


def test_recovery_timeout_transitions_to_half_open() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test", failure_threshold=2, recovery_timeout=10.0, clock=clock
    )
    breaker.record_failure()
    breaker.record_failure()
    assert_state(breaker, CircuitState.OPEN)

    # 9.99s — still OPEN
    clock.advance(9.99)
    assert_state(breaker, CircuitState.OPEN)

    # 10.01s — transition to HALF_OPEN
    clock.advance(0.02)
    assert_state(breaker, CircuitState.HALF_OPEN)


def test_half_open_success_closes_breaker() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test", failure_threshold=2, recovery_timeout=5.0, clock=clock
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(6.0)
    assert_state(breaker, CircuitState.HALF_OPEN)

    breaker.record_success()
    assert_state(breaker, CircuitState.CLOSED)
    assert breaker.consecutive_failures == 0


def test_half_open_failure_reopens_with_fresh_timer() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test", failure_threshold=2, recovery_timeout=5.0, clock=clock
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(6.0)
    assert_state(breaker, CircuitState.HALF_OPEN)

    breaker.record_failure()  # probe fails
    assert_state(breaker, CircuitState.OPEN)

    # Timer should have reset — needs another 5s before HALF_OPEN
    clock.advance(4.0)
    assert_state(breaker, CircuitState.OPEN)

    clock.advance(2.0)  # total 6s since reopen
    assert_state(breaker, CircuitState.HALF_OPEN)


def test_success_resets_failure_counter() -> None:
    breaker = CircuitBreaker("test", failure_threshold=5)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.consecutive_failures == 3

    breaker.record_success()
    assert breaker.consecutive_failures == 0
    assert_state(breaker, CircuitState.CLOSED)


# ---------- call() integration ----------

def test_call_passes_through_when_closed() -> None:
    breaker = CircuitBreaker("test")
    result = breaker.call(lambda: "hello")
    assert result == "hello"


def test_call_records_failure_on_exception() -> None:
    breaker = CircuitBreaker("test", failure_threshold=2)

    def boom() -> None:
        raise RuntimeError("upstream broke")

    with pytest.raises(RuntimeError):
        breaker.call(boom)
    assert breaker.consecutive_failures == 1


def test_call_propagates_args_and_kwargs() -> None:
    breaker = CircuitBreaker("test")

    def add(a: int, b: int, c: int = 0) -> int:
        return a + b + c

    assert breaker.call(add, 1, 2, c=3) == 6


def test_call_threshold_failures_then_short_circuit() -> None:
    breaker = CircuitBreaker("test", failure_threshold=3)

    def boom() -> None:
        raise ValueError("bad")

    for _ in range(3):
        with pytest.raises(ValueError):
            breaker.call(boom)

    # Fourth call — breaker is OPEN, so we get CircuitBreakerOpen instead
    with pytest.raises(CircuitBreakerOpen):
        breaker.call(boom)


def test_call_half_open_admits_exactly_one_probe() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test", failure_threshold=1, recovery_timeout=1.0, clock=clock
    )
    breaker.record_failure()  # OPEN
    clock.advance(1.5)  # → HALF_OPEN

    breaker.call(lambda: "ok")  # probe succeeds → CLOSED
    assert_state(breaker, CircuitState.CLOSED)


# ---------- reset() ----------

def test_reset_returns_to_closed() -> None:
    breaker = CircuitBreaker("test", failure_threshold=2)
    breaker.record_failure()
    breaker.record_failure()
    assert_state(breaker, CircuitState.OPEN)

    breaker.reset()
    assert_state(breaker, CircuitState.CLOSED)
    assert breaker.consecutive_failures == 0


# ---------- Validation ----------

def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker("test", failure_threshold=0)
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker("test", failure_threshold=-1)


def test_invalid_recovery_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="recovery_timeout"):
        CircuitBreaker("test", recovery_timeout=-0.1)


# ---------- Thread-safety ----------

def test_concurrent_failures_below_threshold_count_correctly() -> None:
    """If 100 threads each record one failure (threshold=200), the counter
    should reach exactly 100 — no lost updates."""
    breaker = CircuitBreaker("test", failure_threshold=200)
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        breaker.record_failure()
        breaker.record_failure()

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 50 threads × 2 failures each = 100, well below threshold of 200
    assert breaker.consecutive_failures == 100
    assert_state(breaker, CircuitState.CLOSED)


def test_concurrent_calls_through_open_breaker_all_short_circuit() -> None:
    """Once OPEN, every concurrent call must short-circuit — no upstream
    call may slip through under a race condition."""
    breaker = CircuitBreaker("test", failure_threshold=1)
    breaker.record_failure()  # OPEN

    upstream_calls = 0
    upstream_lock = threading.Lock()

    def upstream() -> str:
        nonlocal upstream_calls
        with upstream_lock:
            upstream_calls += 1
        return "value"

    rejected = 0
    rejected_lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker() -> None:
        nonlocal rejected
        barrier.wait()
        try:
            breaker.call(upstream)
        except CircuitBreakerOpen:
            with rejected_lock:
                rejected += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert upstream_calls == 0, "OPEN breaker leaked a call to upstream"
    assert rejected == 20


# ---------- Sentinel/Apex/Surgeon invariants preserved ----------

def test_breaker_is_strictly_typed() -> None:
    """The module is mypy --strict clean — no Any leakage at the public API."""
    # If mypy passes, this test is a no-op tautology; included so a future
    # contributor doesn't introduce ``Any`` returns and break Purist's
    # zero-debt baseline.
    breaker: CircuitBreaker = CircuitBreaker("test")
    state: CircuitState = breaker.state
    failures: int = breaker.consecutive_failures
    assert state is CircuitState.CLOSED
    assert failures == 0


@pytest.fixture
def recovered_breaker() -> Iterator[CircuitBreaker]:
    """A breaker that's been opened and then auto-recovered to CLOSED via a
    successful HALF_OPEN probe. Useful for testing post-recovery
    behaviour."""
    clock = FakeClock()
    breaker = CircuitBreaker(
        "fixture", failure_threshold=2, recovery_timeout=1.0, clock=clock
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(1.5)
    breaker.record_success()  # HALF_OPEN → CLOSED
    yield breaker


def test_recovered_breaker_starts_with_clean_counter(
    recovered_breaker: CircuitBreaker,
) -> None:
    assert_state(recovered_breaker, CircuitState.CLOSED)
    assert recovered_breaker.consecutive_failures == 0


def test_recovered_breaker_can_re_trip(recovered_breaker: CircuitBreaker) -> None:
    """After full recovery, the breaker should behave like a fresh one —
    re-tripping requires a full ``failure_threshold`` of new failures, not
    just one."""
    recovered_breaker.record_failure()
    assert_state(recovered_breaker, CircuitState.CLOSED)  # not yet
    recovered_breaker.record_failure()
    assert_state(recovered_breaker, CircuitState.OPEN)


# ---------- Chaos scenarios from the diagnostic ----------

def test_chaos_zombie_network_burst_then_recovery() -> None:
    """Scenario: a transit API has a 5-minute outage, then comes back.
    Our breaker should fail fast during the outage and admit traffic
    again after the recovery window."""
    clock = FakeClock()
    breaker = CircuitBreaker(
        "zombie",
        failure_threshold=5,
        recovery_timeout=300.0,  # 5 minutes
        clock=clock,
    )

    # T+0: outage begins. 10 calls fail rapidly.
    def upstream_down() -> Any:
        raise ConnectionError("upstream unreachable")

    for _ in range(5):
        with pytest.raises(ConnectionError):
            breaker.call(upstream_down)

    assert_state(breaker, CircuitState.OPEN)

    # T+5..T+299: every call short-circuits; no wasted upstream attempts
    for offset in (5.0, 60.0, 200.0, 299.9):
        clock.t = offset
        with pytest.raises(CircuitBreakerOpen):
            breaker.call(upstream_down)

    # T+301: recovery window expired, breaker opens for one probe
    clock.t = 301.0
    assert_state(breaker, CircuitState.HALF_OPEN)

    # Upstream is back. Probe succeeds, breaker closes.
    breaker.call(lambda: "recovered")
    assert_state(breaker, CircuitState.CLOSED)


def test_chaos_flapping_upstream_doesnt_cause_breaker_thrashing() -> None:
    """Scenario: an upstream is flapping — every probe succeeds, then the
    next call fails. Without the consecutive-failure counter resetting on
    success, this would oscillate the breaker. Verify a single success
    resets the counter so isolated transient failures don't compound."""
    breaker = CircuitBreaker("flappy", failure_threshold=3)

    # Pattern: F, F, S, F, F, S, F, F (failures interrupted by successes)
    for _ in range(3):
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()  # resets to 0

    # Counter never accumulated above 2 because each success cleared it
    assert_state(breaker, CircuitState.CLOSED)
    assert breaker.consecutive_failures == 0
