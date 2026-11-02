"""VAO ``/trip`` client and quota charge of the Stammstrecke monitors.

Moved verbatim from ``scripts/update_stammstrecke_status.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime
from typing import Any

import requests

from src.providers import vor as vor_provider
from src.stammstrecke.core import (
    MAX_QUERY_TIMEOUT,
    MAX_TRIPS_PER_QUERY,
    QUERY_TIMEOUT,
)
from src.stammstrecke.directions import _Direction
from src.utils.files import loads_finite
from src.utils.http import request_safe, session_with_retries

# ---- VAO ``/trip`` request + parse ----------------------------------------


class _QuotaExceeded(RuntimeError):
    """Raised when the VAO daily quota is exhausted before a request."""


def _build_session(stack: ExitStack) -> requests.Session:
    """Create a :class:`requests.Session` with VOR auth + retries.

    The session is registered with *stack* so the orchestrator's
    ``with ExitStack()`` block manages teardown deterministically (close
    + connection-pool drain) regardless of which direction's call
    raises. Mirrors the pattern used by ``scripts/update_vor_cache.py``.
    """

    session = stack.enter_context(
        session_with_retries(
            vor_provider.VOR_USER_AGENT,
            **vor_provider.VOR_RETRY_OPTIONS,
        )
    )
    vor_provider.apply_authentication(session)
    return session


def _charge_one_request(now: datetime) -> None:
    """Reserve one VAO request slot or raise :class:`_QuotaExceeded`.

    Delegates to :func:`src.providers.vor.reserve_request_slot`, which runs
    the budget check, the increment and the ledger write inside a single
    exclusive-file-lock critical section.

    Audit A.2 — why the check is no longer done here: the previous shape read
    the counter via ``load_request_count()`` (a per-process in-memory cache),
    compared it against the cap, and only then called ``save_request_count``.
    Those were three separate steps with no cross-process lock held across
    them, so two concurrent runs could both observe ``MAX - 1``, both pass the
    check and both fire a request. The ``external-api-fetch`` concurrency
    group makes that unlikely for the cron path, but it does not cover every
    workflow, and a silent contractual breach is not something to leave to
    workflow configuration.

    Audit A.3 — a refused reservation now also covers the case where the
    ledger could not be written or locked at all: ``granted`` is ``False``
    there too, so the caller fails closed instead of issuing an unaccounted
    request.
    """

    granted, usage = vor_provider.reserve_request_slot(now)
    if not granted:
        raise _QuotaExceeded(
            f"VAO daily quota exhausted ({usage}/"
            f"{vor_provider.MAX_REQUESTS_PER_DAY})"
        )


def _query_trips(
    session: requests.Session,
    direction: _Direction,
    *,
    when: datetime,
    timeout: int = QUERY_TIMEOUT,
) -> list[Mapping[str, Any]]:
    """Call ``/trip`` once for *direction* and return the parsed Trip list.

    Validates: HTTP status (handled by ``fetch_content_safe`` raising
    ``RequestException`` on 4xx/5xx), JSON shape (``dict`` with optional
    ``Trip`` list), and per-trip shape (``dict``). Anything else raises
    so the caller's per-direction error isolation runs.

    The wall-clock lookup window is ``date=today, time=now``; the VAO
    server returns the next ``MAX_TRIPS_PER_QUERY`` connections after
    that timestamp. ``rtMode=SERVER_DEFAULT`` enables realtime delays
    so ``Origin.rtTime`` is populated when available.
    """

    safe_timeout = max(1, min(timeout, MAX_QUERY_TIMEOUT))

    # Param-naming notes for the VAO ``/trip`` endpoint (sixth iteration
    # of the 2026-05-09 triage; see PR #1391's commit log for the
    # full chain). After the workflow-config fix exposed the accessId,
    # VAO's response advanced from ``"Missing value for required param
    # accessId"`` to ``"location missing or invalid (LOCATION)"`` —
    # confirming auth works but the ``originId=extId::<id>`` form
    # (Gemini's hypothesis from the manual) is NOT what the live
    # endpoint accepts. We revert to the form documented in the
    # ``trip.md`` curl example AND used by the working
    # ``_fetch_departure_board_for_station`` in
    # ``src/providers/vor.py``: bare numeric station IDs passed to
    # ``originId``/``destId`` directly.
    params: dict[str, str] = {
        "originId": direction.origin_id,
        "destId": direction.destination_id,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "numF": str(MAX_TRIPS_PER_QUERY),
        # Force direct connections — the only Stammstrecke-relevant
        # signal is the per-S-Bahn-leg delay, and a transfer would
        # dilute the sample with the (irrelevant) waiting time.
        "maxChange": "0",
        # Enable server-side realtime data so ``Origin.rtTime`` is
        # populated when available.
        "rtMode": "SERVER_DEFAULT",
        # Filter on product classes Train (1) + S-Bahn (2)
        "products": "3",
    }

    endpoint = f"{vor_provider.VOR_BASE_URL}trip"

    # ``request_safe(raise_for_status=False)`` rather than the
    # ``fetch_content_safe`` wrapper: the wrapper hardcodes
    # ``raise_for_status=True``, which means the request_safe ``except``
    # block runs ``r.close()`` on the response BEFORE the body is read
    # into ``r._content`` (see ``src/utils/http.py`` lines ~2068-2073).
    # The diagnostic helpers downstream (``_decode_error_body``,
    # ``_describe_error_body_keys``) would then see ``response.content
    # == b""`` and report ``body_keys=[EMPTY_BODY]`` — even though the
    # ``Content-Length`` header confirms the body has bytes (``cl=163``
    # in the 2026-05-09 cron run). With ``raise_for_status=False`` the
    # body is read into ``_content`` before any exception path can
    # close the stream. We then check status manually and synthesise
    # the same ``HTTPError`` the wrapper would have raised, but with a
    # response whose ``.content`` is fully populated.
    response = request_safe(
        session,
        endpoint,
        method="GET",
        raise_for_status=False,
        params=params,
        # Content negotiation via Accept header instead of the
        # ``format=json`` query parameter. The trip.md curl example
        # uses this exact pattern, and removing the query parameter
        # eliminates one variable in the iterative HTTP 400 triage.
        headers={"Accept": "application/json"},
        timeout=safe_timeout,
        allowed_content_types=("application/json",),
    )
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"VAO /trip returned HTTP {response.status_code}",
            response=response,
        )
    content = response.content

    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling). A compromised HAFAS VAO upstream /
        # MITM serving NaN / Infinity / 1e1000 literals in a trip
        # payload would otherwise propagate ``float('nan')`` /
        # ``float('inf')`` into delay-minute arithmetic and round-trip-
        # crash the writer pin (Round 1485) on pending-trip persistence.
        payload = loads_finite(content)
    except (ValueError, RecursionError) as exc:
        # Drift defence (JSON Depth-Bomb Round 5): a depth-bomb body
        # passes the size cap (it can be only a few KiB on the wire) but
        # blows the recursion limit on parse, and ``RecursionError`` is
        # NOT a subclass of ``ValueError``. Re-raise as ``ValueError`` so
        # the caller's per-direction error-isolation branch runs without
        # propagating the BaseException-rooted recursion failure further.
        raise ValueError(
            f"VAO /trip returned unparseable JSON: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise TypeError(
            f"VAO /trip returned non-dict payload: {type(payload).__name__}"
        )
    raw_trips = payload.get("Trip")
    if raw_trips is None:
        return []
    if isinstance(raw_trips, Mapping):
        # Some HAFAS-style JSON serialisers collapse single-element lists
        # to the bare object. Normalise to list-of-dict.
        return [raw_trips]
    if not isinstance(raw_trips, list):
        raise TypeError(
            f"VAO /trip Trip field has unexpected type: {type(raw_trips).__name__}"
        )
    return [t for t in raw_trips if isinstance(t, Mapping)]
