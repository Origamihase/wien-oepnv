"""Reusable circuit-breaker primitive for transit-API resilience.

Implements the classic three-state pattern:

    CLOSED ─── failure_threshold consecutive failures ──▶ OPEN
       ▲                                                    │
       │                                                    │ recovery_timeout
       │                                                    ▼
       │                                                HALF_OPEN
       │     success on probe call          failure on probe call
       └─────────────────────┬──────────────────────────────┘
                             │
                             └────▶ CLOSED                ▶ OPEN

In CLOSED, calls pass through and successes/failures are counted.
In OPEN, every call short-circuits with ``CircuitBreakerOpen`` (no upstream
contact); after ``recovery_timeout`` seconds, the breaker auto-transitions
to HALF_OPEN so the next call probes whether the upstream has recovered.
In HALF_OPEN, exactly one probe is admitted; success closes the breaker,
failure opens it again with a fresh timer.

Why a project-local primitive instead of a third-party library:

* The codebase has three ad-hoc resilience implementations
  (``places/client.py`` instance counter, ``vor.py`` Emergency Stop,
  WL/ÖBB urllib3-only). A shared primitive eliminates drift when a
  fourth provider is added.
* The third-party options (``circuitbreaker``, ``pybreaker``) bring
  larger surface than we need and don't compose cleanly with our
  existing ``session_with_retries`` plumbing.
* This implementation is ~150 lines, thread-safe, fully typed, and
  has no dependencies outside the stdlib.

Thread-safety: all state transitions occur under a per-breaker
``threading.RLock``. The breaker itself is reentrant — a callable
invoked via ``protect`` can safely re-enter the same breaker (though
that's an unusual pattern).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitBreakerOpen(RuntimeError):
    """Raised when a call is rejected because the breaker is OPEN.

    Callers can catch this exception specifically to distinguish
    "upstream rejected my request" from "we never even tried because
    we're in fail-fast mode after recent failures."
    """


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Three-state circuit breaker with auto-recovery.

    State machine (see ``docs/architecture.md`` §3 for the rendered
    component diagram)::

        CLOSED ── failure_threshold consecutive failures ──▶ OPEN
           ▲                                                   │
           │ probe-call success            recovery_timeout    │
           │                                       elapses     │
           │                                                   ▼
           │                                              HALF_OPEN
           │                                                   │
           └──────── probe-call failure ──────────▶ OPEN ──────┘

    The breaker is reentrant (``threading.RLock``) and lazy: the
    ``OPEN → HALF_OPEN`` transition is evaluated on every read of the
    :attr:`state` property, so the auto-recovery does not require a
    background thread.

    When to use this vs. ``urllib3`` retries
    ----------------------------------------

    The two primitives solve different problems and stack
    complementarily:

    - **urllib3 retries** (configured via :func:`session_with_retries`,
      using ``JitterRetry`` with ±20% jitter) are *per-call* — they
      handle transient errors within a single request attempt
      (connection reset, 429, 503). They do not remember failures
      across calls; a fully-down upstream gets retried on every call.

    - **CircuitBreaker** is *per-process, per-upstream* — it remembers
      a streak of failures across calls. After ``failure_threshold``
      consecutive failures, every subsequent call short-circuits with
      :class:`CircuitBreakerOpen` for ``recovery_timeout`` seconds.
      This prevents self-DDoS against a known-down upstream.

    The recommended adoption pattern for a new provider (and the only
    pattern in use today by Google Places) is to *wrap* the
    network-fetcher entry point with the breaker, leaving the
    underlying ``session_with_retries`` retry policy unchanged::

        _BREAKER = CircuitBreaker("yourapi", failure_threshold=5,
                                   recovery_timeout=300.0)

        def fetch_events(timeout: int = 25) -> list[FeedItem]:
            try:
                return _BREAKER.call(_actual_fetch, timeout=timeout)
            except CircuitBreakerOpen:
                log.warning("yourapi breaker open; returning empty list")
                return []

    Args:
        name: Human-readable identifier used in log messages
            (``CircuitBreaker[name]: …``). Choose a name that uniquely
            identifies the protected upstream so operators can grep
            for it in build logs.
        failure_threshold: Number of consecutive failures while
            CLOSED that trip the breaker to OPEN. Defaults to 5,
            which matches the existing ``places/client.py`` and
            ``vor.py`` Emergency-Stop thresholds.
        recovery_timeout: Seconds the breaker stays OPEN before
            transitioning to HALF_OPEN. Defaults to 60. Tune higher
            (e.g. 300) for upstreams that take a long time to
            recover from outages, lower for upstreams where a
            transient blip is the more common failure mode.
        clock: Override the monotonic time source. Used by tests to
            drive the recovery-timeout transition deterministically.
            Defaults to :func:`time.monotonic`.

    Raises:
        ValueError: If ``failure_threshold`` is non-positive or
            ``recovery_timeout`` is negative.

    Example:
        >>> breaker = CircuitBreaker("vor", failure_threshold=5,
        ...                          recovery_timeout=300.0)
        >>> try:
        ...     result = breaker.call(lambda: requests.get(api_url))
        ... except CircuitBreakerOpen:
        ...     log.warning("VOR breaker open; skipping fetch")
        ...     result = None

    See Also:
        - ``docs/architecture.md`` §3 (resilience-layer stack) and §4
          (provider plugin contract) for visual context.
        - ``.jules/saboteur.md`` for the design-rationale entry that
          motivated this primitive over the three pre-existing
          ad-hoc resilience implementations.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be non-negative")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock or time.monotonic

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Current state, evaluated lazily — reading ``state`` may trigger
        the OPEN→HALF_OPEN transition if the recovery timeout has elapsed.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def _maybe_transition_to_half_open(self) -> None:
        """If we're OPEN and the recovery timeout has elapsed, move to
        HALF_OPEN so the next call gets to probe the upstream."""
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and self._clock() - self._opened_at >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            log.info(
                "CircuitBreaker[%s]: OPEN → HALF_OPEN after %.1fs; next call will probe",
                self.name,
                self.recovery_timeout,
            )

    def record_success(self) -> None:
        """Mark a successful call. Resets the failure counter; if the
        breaker was HALF_OPEN, this closes it (full recovery)."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                log.info(
                    "CircuitBreaker[%s]: HALF_OPEN → CLOSED (probe succeeded)",
                    self.name,
                )
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        """Mark a failed call. In CLOSED, increments the counter and may
        trip to OPEN. In HALF_OPEN, immediately re-opens with a fresh
        timer (the probe failed, so the upstream is still unhealthy)."""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                log.warning(
                    "CircuitBreaker[%s]: HALF_OPEN → OPEN (probe failed)",
                    self.name,
                )
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
                return

            self._consecutive_failures += 1
            if (
                self._state is CircuitState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                log.warning(
                    "CircuitBreaker[%s]: CLOSED → OPEN after %d consecutive failures",
                    self.name,
                    self._consecutive_failures,
                )
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Invoke ``func(*args, **kwargs)`` under the breaker's protection.

        Returns the function's return value on success. Re-raises any
        exception raised by the function (and counts it as a failure).
        Raises ``CircuitBreakerOpen`` instead of calling ``func`` if the
        breaker is OPEN and the recovery timeout has not yet elapsed.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.OPEN:
                raise CircuitBreakerOpen(
                    f"CircuitBreaker[{self.name}] is OPEN; refusing call"
                )

        try:
            result = func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result

    def reset(self) -> None:
        """Force the breaker back to CLOSED with a clean failure counter.
        Intended for administrative use (e.g. test setup, post-incident
        manual reset). Not part of the normal state machine.
        """
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            log.info("CircuitBreaker[%s]: reset to CLOSED", self.name)


__all__ = ["CircuitBreaker", "CircuitBreakerOpen", "CircuitState"]
