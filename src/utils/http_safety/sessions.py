"""Pinned-session cache, eviction daemon thread and cleanup at exit.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import logging
import atexit
import collections
import threading
import time
import queue
from typing import Any

import requests

from ..logging import sanitize_log_arg
from .adapters import PinnedHTTPSAdapter

# Thread-safe Session Cache (Task: HTTP Keep-Alive)
_HTTP_SESSION_CACHE: collections.OrderedDict[str, requests.Session] = collections.OrderedDict()
_HTTP_SESSION_LOCK = threading.Lock()
_HTTP_SESSION_CACHE_MAX_SIZE = 50

# Queue to hold evicted sessions that need to be closed
_EVICTED_SESSIONS_QUEUE: queue.Queue[tuple[requests.Session, float]] = queue.Queue()

log = logging.getLogger("src.utils.http")

def _cleanup_evicted_sessions_thread() -> None:
    """Daemon thread that closes evicted sessions after a grace period."""
    while True:
        try:
            session, eviction_time = _EVICTED_SESSIONS_QUEUE.get()
            now = time.time()
            wait_time = eviction_time + 60.0 - now
            if wait_time > 0:
                time.sleep(wait_time)

            try:
                session.close()
            except Exception as exc:
                # Security (Clear-Text-Logging Drift, src/utils/* round):
                # ``requests.Session.close()`` can surface adapter errors
                # whose ``__str__`` carries upstream-supplied bytes (e.g.
                # connection-pool errors echoing the URL).  Sanitise the
                # bound exception so the daemon-thread DEBUG log cannot
                # carry control / ANSI / BiDi payloads.
                log.debug(
                    "Error closing evicted session: %s", sanitize_log_arg(str(exc))
                )

            _EVICTED_SESSIONS_QUEUE.task_done()
        except Exception as exc:
            # Security (Clear-Text-Logging Drift): defensive catch-all on
            # the daemon-thread queue handler — sanitise the bound
            # exception text for the same reasons as the close path above.
            log.debug(
                "Error in _cleanup_evicted_sessions_thread: %s",
                sanitize_log_arg(str(exc)),
            )

_cleanup_thread = threading.Thread(target=_cleanup_evicted_sessions_thread, daemon=True)
_cleanup_thread.start()


def _get_pinned_session(
    target_ip: str,
    timeout: int | float | tuple[float, float] | None,
    max_retries: Any = 0,
    *,
    keepalive: bool = False,
) -> requests.Session:
    """Retrieve or create a cached session with a PinnedHTTPSAdapter for the target IP.

    *keepalive* carries the caller's TCP keepalive (``session_with_retries(...,
    keepalive=True)``) to the pinned connection, which is the one an HTTPS
    request of :func:`request_safe` really uses.
    """
    cache_key = f"{target_ip}_{max_retries}_{'keepalive' if keepalive else 'plain'}"
    with _HTTP_SESSION_LOCK:
        if cache_key in _HTTP_SESSION_CACHE:
            # Move to end to maintain LRU
            session = _HTTP_SESSION_CACHE.pop(cache_key)


            _HTTP_SESSION_CACHE[cache_key] = session
            return session

        # Cache miss, create new
        session = requests.Session()
        adapter = PinnedHTTPSAdapter(target_ip, timeout=timeout, max_retries=max_retries, keepalive=keepalive)
        session.mount("https://", adapter)

        # Add to cache and evict if necessary
        _HTTP_SESSION_CACHE[cache_key] = session
        if len(_HTTP_SESSION_CACHE) > _HTTP_SESSION_CACHE_MAX_SIZE:
            # We pop the oldest session but do NOT explicitly close it immediately
            # because another thread might still be actively reading from its socket.
            # Push it to the cleanup queue to be closed after a grace period.
            _, evicted_session = _HTTP_SESSION_CACHE.popitem(last=False)
            _EVICTED_SESSIONS_QUEUE.put((evicted_session, time.time()))

        return session


def cleanup_http_sessions() -> None:
    """Clear the HTTP session cache and gracefully close all sessions."""
    with _HTTP_SESSION_LOCK:
        for session in _HTTP_SESSION_CACHE.values():
            try:
                session.close()
            except Exception as exc:
                # Security (Clear-Text-Logging Drift): atexit-time
                # cleanup — sanitise the bound exception so a hostile
                # ``__str__`` cannot poison the final log line emitted
                # before process shutdown.
                log.debug(
                    "Error closing HTTP session during cleanup: %s",
                    sanitize_log_arg(str(exc)),
                )
        _HTTP_SESSION_CACHE.clear()

    # Also drain any sessions still pending in the eviction queue. The
    # background ``_cleanup_evicted_sessions_thread`` only closes one
    # entry per 60 s grace window, so a burst of evictions can leave
    # several sessions queued at shutdown. Without this drain, atexit
    # closed the active cache but leaked the queued sockets — visible
    # to test fixtures via ``responses`` ResourceWarnings.
    while True:
        try:
            session, _eviction_time = _EVICTED_SESSIONS_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            session.close()
        except Exception as exc:
            log.debug(
                "Error closing evicted HTTP session during cleanup: %s",
                sanitize_log_arg(str(exc)),
            )

atexit.register(cleanup_http_sessions)
