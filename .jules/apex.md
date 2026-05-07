# Apex ⚡ Performance Architect — Journal

Companion log to `.jules/sentinel.md`. Apex documents performance bottlenecks
and optimization patterns; Sentinel documents security learnings. Together
they form the project's institutional memory for cross-cutting non-feature
work.

Format mirrors Sentinel: only entries that capture a unique architectural
finding, a reusable optimization pattern, or an edge case where speed
conflicted with another property and how it was resolved. Routine micro-
optimizations belong in the PR description, not the journal.

---

## 2026-05-07 - Suite-Time Bottleneck: `wait`-Mocked Loops Busy-Spin Against Real-Time Deadlines
**Bottleneck:** `tests/test_thread_pool_cleanup.py::test_thread_pool_cleanup` was 25.02s — alone responsible for ~39% of the total suite wall-clock time (64.19s). The test's stated purpose is small: assert that `_collect_items` instantiates a `ThreadPoolExecutor` and uses it as a context manager. It correctly mocked `ThreadPoolExecutor`, `iter_providers`, and `wait`, but mocked `wait` to return `(set(), set())` — empty done set. The `_collect_items` loop's deadline-eviction branch (`while pending: ... if deadline is not None and now >= deadline: expired.append(future)`) reads `now` from `perf_counter()`, which advances with real wall-clock. The mocked `wait()` returned instantly but the loop kept polling `perf_counter()` until `feed_config.PROVIDER_TIMEOUT` (default 25s) had elapsed in real time. Net: a tight, CPU-bound 25-second spin in CI on every run.
**Why it matters beyond this single test:** the same anti-pattern can recur whenever a unit test mocks one half of a deadline-driven loop. If the loop reads time from a real clock (`perf_counter()`, `time.time()`, `time.monotonic()`) AND the mocked half returns instantly, the test executes the loop against real time. Future tests that mock `wait`, `select`, `poll`, or `socket.recv` while leaving deadlines intact will inherit the same spin.
**Optimization applied:** patch `feed_config.PROVIDER_TIMEOUT` to a small positive value (`0.05` — 50 ms) so the deadline arrives almost immediately. The test's actual assertions (`MockExecutor.called`, `__enter__/__exit__` triggered) are unchanged; the timeout is purely a numeric tuning of the deadline math. Result: 25.02s → 0.08s for that test, suite total 64.19s → 37.50s (~42% faster).
**Pattern for future tests:** when mocking concurrency primitives in `_collect_items`-shaped loops, also patch the timeout/deadline constant from `feed_config` to a small positive value. The deadline-eviction branch is the loop's only natural exit when the mocked wait can't deliver a "done" future. This applies equally to `wait`, `as_completed`, and any future helper that sits between the loop and the executor.
