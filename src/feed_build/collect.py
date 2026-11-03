"""Collecting the items of every provider: cache readers and the thread pool.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any

import requests

from ..feed_types import FeedItem
from ..feed import config as feed_config
from ..feed.providers import provider_statuses
from ..feed.reporting import RunReport, clean_message
from ..utils.cache import register_cache_alert_hook
from ..utils.logging import sanitize_log_arg

from .base import _normalize_item_datetimes, log
from .providers_setup import (
    _call_fetch_with_timeout, _categorize_providers, _fetch_supports_timeout, _provider_concurrency_key,
    _provider_display_name, _provider_timeout_override, _provider_worker_limit, init_providers,
)

__all__ = [
    "_report_empty_provider", "_submit_network_fetches", "_drain_completed_futures", "_collect_items",
]


def _report_empty_provider(
    fetch: Any,
    provider_name: str,
    report: RunReport,
    cache_alerts: dict[str, list[str]],
) -> None:
    """Record a provider that returned zero items.

    Two different things wear the same shape here, and conflating them is the
    defect this splits apart (audit 2026-09-12, Befund 6):

    * A cache-backed provider with nothing in it has a problem — the cache
      should hold data and does not. That stays a ``WARNING`` plus an entry in
      the run report's warning list, and the detail carries whatever the cache
      alerts say went wrong.
    * The Stammstrecke provider with nothing to report is the S-Bahn trunk
      line running normally. Most builds look like this. Logging it as a
      warning every 30 minutes made "working" indistinguishable from "broken"
      on the dashboard and buried the warnings that mean something.
      The Stadt-Wien-Baustellen are the same since 2026-10-06: the cache
      holds only sites with an effect on public transport, often none. A
      cache alert (file missing, unreadable, stale) still makes either one
      a warning.

    Which one a provider is, is declared at registration
    (``register_provider(..., empty_is_normal=True)``) rather than guessed
    here from its name, so a new provider has to state its own contract and
    the safe default applies until it does.

    The distinction survives into the run-report summary as a status of its
    own (``ok-empty`` vs ``empty``): a dashboard that only sees ``:empty``
    cannot tell "nothing to report" from "no data".
    """
    cache_name = getattr(fetch, "_provider_cache_name", None)
    alerts = cache_alerts.get(str(cache_name), []) if cache_name is not None else []
    # A cache alert (file missing, unreadable, too large) means no data, even
    # for a provider whose empty answer is normally healthy.
    if getattr(fetch, "_provider_empty_is_normal", False) and not alerts:
        log.info(
            "Provider '%s' meldet keine Vorfälle – Normalzustand, kein Fehler.",
            provider_name,
        )
        report.provider_success(
            provider_name,
            items=0,
            status="ok-empty",
            detail="Keine Vorfälle",
        )
        return

    log.warning(
        "Cache für Provider '%s' leer – generiere Feed ohne aktuelle Daten.",
        provider_name,
    )
    detail = "Keine aktuellen Daten"
    if alerts:
        detail = "; ".join(dict.fromkeys(alerts))
    report.provider_success(
        provider_name, items=0, status="empty", detail=detail
    )
    report.add_warning(f"Provider {provider_name}: {detail}")


def _run_cache_fetchers(
    cache_fetchers: list[Any],
    provider_names: dict[Any, str],
    items: list[FeedItem],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Sequentially invoke each cache-backed fetcher and feed its result through
    ``merge_result``. Cache fetchers are sync because they read from disk; no
    timeout / executor machinery applies."""
    for fetch in cache_fetchers:
        name = getattr(fetch, "__name__", str(fetch))
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        report.provider_started(provider_name)
        result: list[FeedItem] | None = None
        try:
            result = fetch()
        except Exception as exc:
            log.exception("%s fetch fehlgeschlagen: %s", name, exc)
            report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
            continue
        if result is not None:
            merge_result(fetch, result, provider_name)


def _build_run_fetch(
    fetch: Any,
    effective_timeout: int | float,
    supports_timeout: bool,
    semaphore: BoundedSemaphore | None,
    provider_name: str,
) -> Any:
    """Wrap a network fetcher with semaphore-aware timeout accounting. The
    returned closure is what gets submitted to the ThreadPoolExecutor; it
    enforces both the per-call timeout and (when a concurrency limit applies)
    a semaphore acquisition timeout that subtracts wait time from the budget,
    preventing thread starvation under provider deadlock.
    """
    def _run_fetch() -> Any:
        timeout_arg = effective_timeout if effective_timeout >= 0 else None

        if semaphore is None:
            return _call_fetch_with_timeout(fetch, timeout_arg, supports_timeout)

        # Prevent thread starvation by enforcing timeout on semaphore acquisition.
        # If the provider is deadlocked/overloaded, we don't block the executor
        # worker forever.
        start_wait = perf_counter()

        # If effective_timeout < 0, fallback to global timeout + buffer to avoid infinite block
        sem_timeout = effective_timeout if effective_timeout >= 0 else (feed_config.PROVIDER_TIMEOUT + 5.0)

        acquired = semaphore.acquire(timeout=sem_timeout)
        if not acquired:
            raise TimeoutError(f"Semaphore acquisition timed out after {sem_timeout}s")

        # Subtract wait time from timeout
        try:
            elapsed = perf_counter() - start_wait
            remaining_timeout = timeout_arg - elapsed if timeout_arg is not None else None

            if remaining_timeout is not None and remaining_timeout <= 0:
                raise TimeoutError(
                    f"Semaphore acquisition took {elapsed:.2f}s, no realistic time left for fetch (threshold: <= 0s)"
                )

            return _call_fetch_with_timeout(fetch, remaining_timeout, supports_timeout)
        finally:
            semaphore.release()

    return _run_fetch


def _submit_network_fetches(
    executor: ThreadPoolExecutor,
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
) -> tuple[
    dict[Any, tuple[Any, str, int]],
    dict[Any, float | None],
    set[Any],
]:
    """Submit each network fetcher to the executor with its timeout/semaphore
    config, returning (futures-meta, deadlines, pending-set)."""
    futures: dict[Any, tuple[Any, str, int]] = {}
    deadlines: dict[Any, float | None] = {}
    pending: set[Any] = set()

    # Pre-compute the per-group worker limit across ALL fetchers BEFORE
    # the main submit loop. Pre-fix the loop only registered a
    # semaphore when the CURRENT fetcher's own per-provider env-limit
    # was set, so a sibling provider in the same ``concurrency_key``
    # group without its own env override ran unbounded — silently
    # defeating the operator-intended shared cap. If two group members
    # set different positive limits, the tighter one (``min``) wins so
    # the group respects every member's stated upper bound.
    group_limits: dict[str, int] = {}
    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        if worker_limit is not None and worker_limit > 0:
            current = group_limits.get(concurrency_key)
            group_limits[concurrency_key] = (
                min(current, worker_limit) if current is not None else worker_limit
            )
    semaphores: dict[str, BoundedSemaphore] = {
        key: BoundedSemaphore(limit) for key, limit in group_limits.items()
    }

    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        timeout_override = _provider_timeout_override(fetch, env_name, provider_name)
        effective_timeout = (
            timeout_override if timeout_override is not None else feed_config.PROVIDER_TIMEOUT
        )
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        # Every member of a group with a positive limit picks up the
        # pre-computed shared semaphore — including members whose OWN
        # ``worker_limit`` resolved to ``None``.
        semaphore: BoundedSemaphore | None = semaphores.get(concurrency_key)
        if timeout_override is not None:
            log.debug(
                "Provider %s nutzt Timeout-Override von %ss",
                provider_name,
                timeout_override,
            )
        if worker_limit is not None and worker_limit > 0:
            log.debug(
                "Provider %s begrenzt Worker auf %s (Schlüssel %s)",
                provider_name,
                worker_limit,
                concurrency_key,
            )
        supports_timeout = _fetch_supports_timeout(fetch)

        if effective_timeout == 0:
            report.provider_started(provider_name)
            name = getattr(fetch, "__name__", str(fetch))
            log.error("%s fetch Timeout nach 0s", name)
            report.provider_error(provider_name, "Timeout nach 0s")
            continue

        run_fetch = _build_run_fetch(
            fetch, effective_timeout, supports_timeout, semaphore, provider_name
        )

        report.provider_started(provider_name)
        future = executor.submit(run_fetch)
        futures[future] = (fetch, provider_name, effective_timeout)
        pending.add(future)
        start_time = perf_counter()
        if effective_timeout > 0:
            deadlines[future] = start_time + effective_timeout
        else:
            deadlines[future] = None

    return futures, deadlines, pending


def _evict_expired_futures(
    pending: set[Any],
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    cancelled_futures: set[Any],
    report: RunReport,
    now: float,
) -> None:
    """Per Apex Phase 1: poll deadlines on every loop turn so the busy-spin
    against real-time `perf_counter()` is bounded by the smallest remaining
    timeout. Mutates ``pending`` and ``cancelled_futures`` in place.
    """
    expired = [
        future for future in list(pending)
        if (deadline := deadlines.get(future)) is not None and now >= deadline
    ]
    for future in expired:
        pending.discard(future)
        fetch, provider_name, timeout_value = futures[future]
        name = getattr(fetch, "__name__", str(fetch))
        log.error("%s fetch Timeout nach %ss", name, timeout_value)
        report.provider_error(
            provider_name,
            f"Timeout nach {timeout_value}s",
        )
        future.cancel()
        cancelled_futures.add(future)


def _drain_completed_futures(
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    pending: set[Any],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Apex-Phase-1 deadline-eviction loop: alternate eviction sweep + bounded
    `wait()` until ``pending`` drains. Result handling distinguishes Timeout,
    CancelledError, and generic Exception so the report has accurate per-
    provider error categories.
    """
    cancelled_futures: set[Any] = set()
    while pending:
        now = perf_counter()
        _evict_expired_futures(pending, futures, deadlines, cancelled_futures, report, now)

        if not pending:
            break

        wait_timeout: float | None = None
        remaining = [
            deadline - now
            for fut in pending
            if (deadline := deadlines.get(fut)) is not None
        ]
        if remaining:
            wait_timeout = max(min(remaining), 0.1)

        done, _ = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
        if not done:
            continue

        for future in done:
            pending.discard(future)
            if future in cancelled_futures:
                continue
            fetch, provider_name, _timeout_value = futures[future]
            name = getattr(fetch, "__name__", str(fetch))
            try:
                result = future.result()
            except (TimeoutError, requests.exceptions.Timeout) as exc:
                # Security (Clear-Text-Logging Drift): the timeout
                # exception text may include the upstream URL (sanitised
                # by ``request_safe`` already) but a non-request_safe
                # path (e.g. fetch() composing its own timeout) could
                # still surface raw upstream bytes — sanitise.
                sanitised = sanitize_log_arg(str(exc))
                log.error("%s fetch Timeout: %s", name, sanitised)
                report.provider_error(provider_name, f"Timeout: {sanitised}")
            except CancelledError:
                report.provider_error(provider_name, "Fetch abgebrochen")
            except Exception as exc:
                # Security (Clear-Text-Logging Drift): broad ``Exception``
                # — sanitise both log emission and the operator-facing
                # report-error surface.  ``log.exception`` interpolates
                # the bound exc via ``%s`` for the *message string*; the
                # traceback itself is rendered by the logging framework
                # via formatException (a repr-escaping path) and is
                # therefore safe.
                sanitised = sanitize_log_arg(str(exc))
                log.exception("%s fetch fehlgeschlagen: %s", name, sanitised)
                report.provider_error(
                    provider_name, f"Fetch fehlgeschlagen: {sanitised}"
                )
            else:
                # Isolate a single provider's merge failure: ``merge_result``
                # (and the report bookkeeping it calls) runs OUTSIDE the
                # ``future.result()`` try above, so an exception here would
                # otherwise propagate out of the drain loop and lose every
                # already-collected item from the other providers.
                try:
                    merge_result(fetch, result, provider_name)
                except Exception as exc:
                    sanitised = sanitize_log_arg(str(exc))
                    log.exception("%s merge fehlgeschlagen: %s", name, sanitised)
                    report.provider_error(
                        provider_name, f"Merge fehlgeschlagen: {sanitised}"
                    )


def _run_network_fetchers(
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Run all network fetchers concurrently in a ThreadPoolExecutor with
    deadline-eviction-style timeout enforcement (Apex Phase 1). Pending
    futures are cancelled on early exit to free worker threads immediately.
    """
    desired_workers = len(network_fetchers)
    if feed_config.PROVIDER_MAX_WORKERS > 0:
        if desired_workers > feed_config.PROVIDER_MAX_WORKERS:
            log.debug(
                "Begrenze Provider-Threads von %s auf %s",
                desired_workers,
                feed_config.PROVIDER_MAX_WORKERS,
            )
        desired_workers = min(desired_workers, feed_config.PROVIDER_MAX_WORKERS)

    pending: set[Any] = set()
    with ThreadPoolExecutor(max_workers=max(1, desired_workers)) as executor:
        try:
            futures, deadlines, pending = _submit_network_fetches(
                executor, network_fetchers, provider_names, provider_envs, report
            )
            _drain_completed_futures(futures, deadlines, pending, report, merge_result)
        finally:
            # Cancel remaining futures if we exit early or with exceptions.
            # executor.shutdown(wait=False, cancel_futures=True) is automatically called
            # by the context manager's __exit__, but we explicitly cancel pending futures
            # to free resources immediately.
            for future in pending:
                future.cancel()


def _collect_items(report: RunReport | None = None) -> list[FeedItem]:
    """Run all enabled providers and merge their items into a single list.

    This is the central data-collection orchestrator of the build. It:

    1. Initialises the provider registry (lazy-loads plugins on first call).
    2. Categorises providers via :func:`_categorize_providers` into:
       - **cache fetchers** — loaders whose ``_provider_cache_name``
         attribute marks them as disk-bound (read from a local cache);
         run sequentially since the bottleneck is disk I/O, not network.
       - **network fetchers** — real upstream HTTP fetches; run
         concurrently in a :class:`ThreadPoolExecutor`.
    3. Wires up a :func:`register_cache_alert_hook` so that warnings
       emitted by the cache layer (e.g. "cache for VOR is 6h stale")
       attach to the run report instead of just logging.
    4. Runs cache fetchers via :func:`_run_cache_fetchers` (synchronous).
    5. Runs network fetchers via :func:`_run_network_fetchers`, which
       wraps each loader in a per-future timeout, evicts stragglers via
       a deadline-eviction loop (Apex Phase 1), and merges results
       through :func:`_merge_result`.
    6. Provides per-provider exception isolation — a crash in one
       loader becomes an error entry on the run report, not an abort.
       This is the project's primary bulkhead: a hostile or unhealthy
       upstream cannot bring down the other providers' data.

    The function intentionally accepts a ``RunReport`` to keep the
    health-reporting side-effect explicit. Tests inject a fresh report
    per call; production paths receive one from
    :func:`_invoke_collect_items`.

    Args:
        report: Optional :class:`RunReport` to record provider
            successes, errors, and cache alerts into. If ``None``, a
            fresh report is constructed from
            :func:`provider_statuses` so the function is safe to call
            standalone (tests, ad-hoc invocations).

    Returns:
        A list of :class:`FeedItem` dictionaries, concatenated from
        every successful provider in the order they completed. The
        result is **not** deduplicated; callers (typically
        :func:`main`) run :func:`_dedupe_items` and
        :func:`deduplicate_fuzzy` afterwards.

    See Also:
        ``docs/architecture.md`` §1 for the full sequence diagram.
    """
    init_providers()
    if report is None:
        report = RunReport(provider_statuses())
    items: list[FeedItem] = []

    cache_alerts: defaultdict[str, list[str]] = defaultdict(list)
    seen_cache_alerts: set[tuple[str, str]] = set()
    alert_lock = Lock()

    def _cache_alert_handler(provider_key: str, message: str) -> None:
        normalized_key = str(provider_key or "").strip()
        normalized_message = clean_message(message)
        if not normalized_key or not normalized_message:
            return
        with alert_lock:
            cache_alerts[normalized_key].append(normalized_message)
            if report is not None:
                key = (normalized_key, normalized_message)
                if key not in seen_cache_alerts:
                    seen_cache_alerts.add(key)
                    report.add_warning(f"Cache {normalized_key}: {normalized_message}")

    unregister_cache_alert = register_cache_alert_hook(_cache_alert_handler)
    try:
        buckets = _categorize_providers(report)

        if not buckets.cache_fetchers and not buckets.network_fetchers:
            return []

        def _merge_result(fetch: Any, result: Any, provider_name: str) -> None:
            name = getattr(fetch, "__name__", str(fetch))
            if not isinstance(result, list):
                log.error("%s fetch gab keine Liste zurück: %r", name, result)
                report.provider_error(provider_name, "Ungültige Antwort (keine Liste)")
                return
            # Cast raw dicts to FeedItem for typing compliance after normalization
            _normalize_item_datetimes(result)
            typed_result = result
            items.extend(typed_result)
            count = len(result)
            if count == 0:
                _report_empty_provider(fetch, provider_name, report, cache_alerts)
            else:
                report.provider_success(provider_name, items=count)

        _run_cache_fetchers(
            buckets.cache_fetchers, buckets.provider_names, items, report, _merge_result
        )

        if buckets.network_fetchers:
            _run_network_fetchers(
                buckets.network_fetchers,
                buckets.provider_names,
                buckets.provider_envs,
                report,
                _merge_result,
            )

        return items
    finally:
        unregister_cache_alert()
