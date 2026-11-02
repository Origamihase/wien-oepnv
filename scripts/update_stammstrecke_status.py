#!/usr/bin/env python3
"""Monitor delays on the S-Bahn Stammstrecke (Wien Floridsdorf ↔ Wien Meidling).

Queries direct S-Bahn connections via the VOR/VAO ReST ``/trip`` endpoint
for **both directions** independently. Per-cron tick, every observed
S-Bahn leg is recorded into a small JSON ledger
(``cache/stammstrecke/pending_trips.json``) keyed by
``(direction, line_name, scheduled_origin_dt)``. Re-observations of the
same train across multiple cron ticks *overwrite* the previous reading
— the value that eventually flows into ``data/stats/stammstrecke_
<YYYY>.csv`` is therefore the observation taken closest to the train's
actual departure, which is the most accurate one. When the train's
scheduled departure has passed, its identity key is moved to a sibling
ledger (``cache/stammstrecke/recently_finalised.json``) so any
anomalous VAO re-emission at the lookahead boundary cannot produce a
duplicate CSV row.

The CSV row's ``delay_minutes`` is the arithmetic mean of the
finalised trains for that direction *in that calendar year* — legs
without realtime signal are skipped (status unknown ≠ on-time) so the
mean reflects only verified observations. Directions are evaluated
strictly separately because merging both into a single sample dilutes
the signal — a station with a major incident in one direction often
runs normally in the opposite direction.

Migration history
-----------------

This script was originally implemented against the public ``mgate.exe``
endpoint via :mod:`pyhafas` (``OEBBProfile``). The 2026-05-09 audit
discovered that ``OEBBProfile`` is not exported by any released
``pyhafas`` version on PyPI (the import had been silently failing for
weeks, leaving ``data/stats/stammstrecke_*.csv`` empty). The script was
re-architected to use the project's existing VOR/VAO infrastructure:
the same authenticated session, quota counter, and ``fetch_content_safe``
size-/header-/SSRF-defended HTTP layer that the disruption providers
already rely on.

Design contract
---------------

- **Two-direction split**: each cron tick runs two ``/trip`` calls — one
  ``Floridsdorf → Meidling`` and one ``Meidling → Floridsdorf``. Each
  call's per-sample mean is computed independently and persisted to
  the CSV ledger; the feed builder later reads those rows to emit at
  most one event per direction.
- **Direct-connection filter**: only single-ride-leg trips are eligible
  for the per-sample mean. The VAO ``maxChange=0`` query parameter
  gives the upstream a hint, and a client-side leg-count check is the
  second layer of defence — so a multi-stop trip the API still returns
  under ``maxChange=0`` does not bleed into the signal.
- **S-Bahn product filter**: the eligible leg must carry an S-Bahn or regional train
  product label. The VAO `/trip` call uses `products=3` to pre-filter Train (1) + S-Bahn (2).
  We accept either ``leg.category in {"S", "R", "REX", "CJX"}``,
  ``leg.name`` matching ``(S|R|REX|CJX)\\d+``, or ``leg.Product[].catOut`` /
  ``Product[].line`` matching the same — covering the known
  upstream-shape variants without committing to a single one. Cityjet Express
  (``CJX``) was added 2026-05-17 after ÖBB rebranded selected REX rolling-
  stock; the corridor coverage is unchanged. Long-distance trains
  (Railjet, IC, etc.) are filtered out.
- **CSV ledger, not events**: this script is now a pure CSV appender —
  the former ``events.json`` / RSS output (``OUTPUT_PATH`` plus the event
  builders) was removed on 2026-05-09. Each tick appends one per-direction
  mean-delay row to ``data/stats/stammstrecke_<YYYY>.csv`` (and cancellation
  rows to ``data/stats/ausfaelle_<YYYY>.csv``); on an unreachable API or a
  degraded signal simply no row is recorded for that tick. Event emission —
  self-healing of stale warnings and per-episode GUID / ``first_seen``
  stability — now lives in the feed builder
  (:mod:`src.feed.stammstrecke`), not here.
- **Quota integration**: every ``/trip`` call increments the shared
  ``data/vor_request_count.json`` counter via :func:`save_request_count`
  *before* the request is sent. A run that would push the day's usage
  over :data:`MAX_REQUESTS_PER_DAY` aborts cleanly (``[]`` cache, exit
  ``0``) rather than risking a contractual breach.
- **Resilience**: the network call is wrapped in
  :class:`src.utils.circuit_breaker.CircuitBreaker` (`failure_threshold=10`,
  `recovery_timeout=3600`s — same budget shape the original HAFAS
  client used). Per-call HTTP timeout is enforced at the
  ``fetch_content_safe`` layer.
- **Atomicity**: writes go through :func:`src.utils.files.atomic_write`
  with permissive ``0o644`` permissions; a crash mid-write cannot
  leave a half-written cache file behind.
- **Timezone**: GitHub Actions runs in UTC. The scheduled / observation
  timestamps written to the CSV ledger are localised to ``Europe/Vienna``
  via :mod:`zoneinfo` and serialised as ISO 8601 strings with offset.
- **Schema**: the CSV ledger rows carry the per-direction mean delay (and
  cancellations) keyed by timestamp and direction; the canonical FeedItem
  event shape they feed into is built downstream in
  :mod:`src.feed.stammstrecke`.
- **Logging**: every diagnostic message is routed through
  :func:`src.feed.logging_safe.setup_script_logging` so log injection
  / ANSI / BiDi attacks via upstream-controlled fields are sanitised
  at the formatter layer. URLs containing the post-``VorAuth``
  ``accessId`` query parameter are NEVER logged via ``%s`` /
  ``exc_info=True`` (mirrors the canonical pattern from
  ``scripts/update_vor_cache.py``).
"""

from __future__ import annotations

import logging
import statistics
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.utils.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
)
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stats import (  # noqa: E402
    append_ausfall_row,
    append_stammstrecke_row,
)

from src.stammstrecke.core import (  # noqa: E402
    LOGGER as LOGGER,
    FLORIDSDORF_VOR_ID as FLORIDSDORF_VOR_ID,
    MEIDLING_VOR_ID as MEIDLING_VOR_ID,
    MEIDLING_CANONICAL_SEED as MEIDLING_CANONICAL_SEED,
    DELAY_THRESHOLD_MINUTES as DELAY_THRESHOLD_MINUTES,
    MAX_TRIPS_PER_QUERY as MAX_TRIPS_PER_QUERY,
    PENDING_TRIPS_PATH as PENDING_TRIPS_PATH,
    PENDING_TRIPS_MAX_BYTES as PENDING_TRIPS_MAX_BYTES,
    PENDING_TTL as PENDING_TTL,
    RECENTLY_FINALISED_PATH as RECENTLY_FINALISED_PATH,
    RECENTLY_FINALISED_MAX_BYTES as RECENTLY_FINALISED_MAX_BYTES,
    PENDING_TRIPS_LOCK_PATH as PENDING_TRIPS_LOCK_PATH,
    _PendingTrip as _PendingTrip,
    QUERY_TIMEOUT as QUERY_TIMEOUT,
    MAX_QUERY_TIMEOUT as MAX_QUERY_TIMEOUT,
    BREAKER_FAILURE_THRESHOLD as BREAKER_FAILURE_THRESHOLD,
    BREAKER_RECOVERY_TIMEOUT as BREAKER_RECOVERY_TIMEOUT,
    _S_BAHN_LINE_RE as _S_BAHN_LINE_RE,
    VIENNA_TZ as VIENNA_TZ,
    _now_vienna as _now_vienna,
    _format_minutes as _format_minutes,
)
from src.stammstrecke.directions import (  # noqa: E402
    _short_target_label as _short_target_label,
    _Direction as _Direction,
    DIRECTIONS as DIRECTIONS,
)
from src.stammstrecke.ledger import (  # noqa: E402
    _NAME_NORMALISE_RE as _NAME_NORMALISE_RE,
    _canonical_line_name as _canonical_line_name,
    _identity_key as _identity_key,
    _coerce_aware as _coerce_aware,
    _trip_to_json as _trip_to_json,
    _trip_from_json as _trip_from_json,
    _load_pending_trips as _load_pending_trips,
    _save_pending_trips as _save_pending_trips,
    _purge_stale_entries as _purge_stale_entries,
    _load_recently_finalised as _load_recently_finalised,
    _save_recently_finalised as _save_recently_finalised,
    _purge_finalised_entries as _purge_finalised_entries,
    _ledger_lock as _ledger_lock,
)
from src.stammstrecke.vao_trip import (  # noqa: E402
    _QuotaExceeded as _QuotaExceeded,
    _build_session as _build_session,
    _charge_one_request as _charge_one_request,
    _query_trips as _query_trips,
)
from src.stammstrecke.legs import (  # noqa: E402
    _is_sbahn_leg as _is_sbahn_leg,
    _parse_vao_dt as _parse_vao_dt,
    _leg_departure_delay_minutes as _leg_departure_delay_minutes,
    _elapsed_minutes as _elapsed_minutes,
    _ambiguous_local as _ambiguous_local,
    _SbahnLegObservation as _SbahnLegObservation,
    _leg_is_cancelled as _leg_is_cancelled,
    _collect_sbahn_leg_observations as _collect_sbahn_leg_observations,
    _collect_sbahn_delays_minutes as _collect_sbahn_delays_minutes,
    _observe_legs as _observe_legs,
    _finalize_departed as _finalize_departed,
)
from src.stammstrecke.vao_errors import (  # noqa: E402
    _ERROR_CODE_MAX_LEN as _ERROR_CODE_MAX_LEN,
    _ERROR_BODY_MAX_BYTES as _ERROR_BODY_MAX_BYTES,
    _VAO_ERROR_CODE_RE as _VAO_ERROR_CODE_RE,
    _BODY_KEYS_MAX_LEN as _BODY_KEYS_MAX_LEN,
    _DIAG_NO_RESPONSE as _DIAG_NO_RESPONSE,
    _DIAG_MISSING as _DIAG_MISSING,
    _DIAG_BAD_SHAPE as _DIAG_BAD_SHAPE,
    _DIAG_EMPTY_BODY as _DIAG_EMPTY_BODY,
    _DIAG_NO_KEYS as _DIAG_NO_KEYS,
    _DIAG_REDACTED_KEY as _DIAG_REDACTED_KEY,
    _extract_http_status as _extract_http_status,
    _extract_response_header as _extract_response_header,
    _decode_error_body as _decode_error_body,
    _extract_vao_error_code as _extract_vao_error_code,
    _describe_error_body_keys as _describe_error_body_keys,
    _ERROR_TEXT_MAX_LEN as _ERROR_TEXT_MAX_LEN,
    _extract_vao_error_code_length as _extract_vao_error_code_length,
    _extract_vao_error_text as _extract_vao_error_text,
    _extract_vao_internal_error_text as _extract_vao_internal_error_text,
    _extract_vao_internal_error_code as _extract_vao_internal_error_code,
    _extract_vao_request_id as _extract_vao_request_id,
)


_BREAKER = CircuitBreaker(
    "stammstrecke-vor",
    failure_threshold=BREAKER_FAILURE_THRESHOLD,
    recovery_timeout=BREAKER_RECOVERY_TIMEOUT,
)


def configure_logging() -> None:
    """Install the project's :class:`SafeFormatter` for this script."""

    setup_script_logging(logging.INFO)
    # urllib3 emits one INFO line per VAO request which clutters the
    # workflow log without adding diagnostic value. Mirrors the existing
    # scripts pattern.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _process_direction(
    session: requests.Session,
    direction: _Direction,
    state: dict[str, _PendingTrip],
    *,
    when: datetime,
    recently_finalised: Mapping[str, datetime] | None = None,
) -> str:
    """Query ``direction`` once and observe its legs into *state*.

    Returns one of:

    * ``"ok"`` — query succeeded and zero-or-more legs were observed
      into the pending-trip ledger (the CSV row is *not* written
      here — :func:`main` does that once finalisation runs across
      all directions);
    * ``"error"`` — VAO/parse raised an exception (already logged);
    * ``"quota_exceeded"`` — the daily quota cap hit before the call;
      caller treats the same as ``"error"`` for self-healing purposes.

    The ``CircuitBreakerOpen`` case is *not* handled here — the caller
    catches it so it can break out of the per-direction loop instead
    of consuming further breaker-protected slots.

    2026-05-11: split CSV-write off into :func:`main` so the cron
    tick can observe both directions before deciding which rows to
    finalise. The latest-observation-wins ledger (see
    :data:`PENDING_TRIPS_PATH`) records every seen leg by
    ``(direction, line_name, scheduled)`` so a train re-observed
    across multiple cron ticks contributes exactly one CSV row —
    fixing the lookahead-overlap double-counting in which the same
    physical train inflated the persisted mean of every tick that
    saw it.
    """

    LOGGER.info(
        "Stammstrecke: Abfrage Richtung %s (%s → %s) um %s.",
        direction.target_label,
        direction.origin_id,
        direction.destination_id,
        when.isoformat(),
    )
    try:
        # Charge the VAO daily quota BEFORE consulting the breaker: a quota
        # breach is a LOCAL budget signal, not an upstream-health failure, so
        # it must not count toward the breaker's failure threshold (which would
        # otherwise OPEN the breaker for BREAKER_RECOVERY_TIMEOUT after ~10
        # quota-blocked ticks and skip the first ticks after the midnight reset).
        # ``_QuotaExceeded`` raised here is caught below.
        _charge_one_request(when)
        trips = _BREAKER.call(_query_trips, session, direction, when=when)
    except CircuitBreakerOpen:
        # Re-raise so main() can break out of the loop without re-trying
        # the next direction (the breaker would short-circuit it anyway).
        raise
    except _QuotaExceeded as exc:
        LOGGER.warning(
            "Stammstrecke: Tageslimit erreicht — Richtung %s übersprungen (%s).",
            direction.target_label,
            sanitize_log_arg(str(exc)),
        )
        return "quota_exceeded"
    except requests.HTTPError as exc:
        # Diagnostic-rich branch for non-2xx responses. The previous
        # PR (#1389) made the body readable, revealing the canonical
        # VAO error envelope shape ``{dialectVersion, errorCode,
        # errorText, requestId, serverVersion}``. The 2026-05-09 cron
        # then showed ``errorCode=***`` — VAO echoes the supplied
        # accessId into the ``errorCode`` field, which the project's
        # SafeFormatter / GitHub Actions secret-masker then redacts
        # entirely.
        #
        # This iteration adds three more leak-free diagnostics:
        # * ``code_len`` — the length of ``errorCode`` (4-8 chars =
        #   canonical short code; 16+ chars = accessId-shaped token);
        # * ``err_text`` — the human-readable ``errorText`` field
        #   with the secret-masking applied at the ``sanitize_log_arg``
        #   layer (preserves surrounding text like "Invalid origin
        #   location: ***");
        # * ``req_id`` — the VAO server-side trace ID for support
        #   triage.
        status_code = _extract_http_status(exc)
        error_code = _extract_vao_error_code(exc)
        code_len = _extract_vao_error_code_length(exc)
        error_text = _extract_vao_error_text(exc)
        internal_code = _extract_vao_internal_error_code(exc)
        internal_text = _extract_vao_internal_error_text(exc)
        request_id = _extract_vao_request_id(exc)
        body_keys = _describe_error_body_keys(exc)
        content_type = _extract_response_header(exc, "Content-Type")
        content_length = _extract_response_header(exc, "Content-Length")
        server = _extract_response_header(exc, "Server")
        www_auth = _extract_response_header(exc, "WWW-Authenticate")
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: HTTP %s "
            "(errorCode=%s, code_len=%s, err_text=%s, "
            "int_code=%s, int_text=%s, req_id=%s, "
            "body_keys=%s, ct=%s, cl=%s, server=%s, www_auth=%s).",
            direction.target_label,
            status_code,
            sanitize_log_arg(error_code),
            sanitize_log_arg(code_len),
            sanitize_log_arg(error_text),
            sanitize_log_arg(internal_code),
            sanitize_log_arg(internal_text),
            sanitize_log_arg(request_id),
            sanitize_log_arg(body_keys),
            sanitize_log_arg(content_type),
            sanitize_log_arg(content_length),
            sanitize_log_arg(server),
            sanitize_log_arg(www_auth),
        )
        return "error"
    except Exception as exc:
        # Security: never log the full exception via ``%s`` / ``exc_info``
        # — ``VorAuth`` injected the ``accessId`` into the prepared
        # request URL, and ``RequestException`` may carry that URL in
        # its message. ``describe_failure`` masks it (2026-10-28).
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: %s.",
            direction.target_label,
            vor_provider.describe_failure(exc),
        )
        return "error"

    observations = _collect_sbahn_leg_observations(trips)
    written = _observe_legs(
        state,
        observations,
        direction=direction.target_label,
        now=when,
        recently_finalised=recently_finalised,
    )
    LOGGER.info(
        "Stammstrecke: Richtung %s — %d S-Bahn-Legs aus %d Trips beobachtet "
        "(Ledger-Updates: %d, Anti-Doppelzähl-Skips: %d).",
        direction.target_label,
        len(observations),
        len(trips),
        written,
        len(observations) - written,
    )
    return "ok"


def main() -> int:
    """Entry point. Returns ``0`` on success (incl. partial), ``1`` on full failure.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded.

    The script writes only to the CSV ledger
    ``data/stats/stammstrecke_<YYYY>.csv``; the feed builder reads from
    that ledger directly (see :mod:`src.feed.stammstrecke`). A
    CircuitBreakerOpen short-circuit or all-directions-failed run
    appends nothing — the feed naturally degrades to "no Stammstrecke
    entry" because the most-recent observations roll out of the
    1-hour feed window without replacement.
    """

    configure_logging()

    when = _now_vienna()
    successes = 0
    errors = 0

    # OS-level lock around the entire load-modify-save cycle: cron
    # ticks are concurrency-grouped, but ``workflow_dispatch`` /
    # manual triggers can bypass that gate. Without the lock, two
    # concurrent runs would each load the same baseline ledger and
    # the slower writer's observations would be silently dropped at
    # save time.
    with _ledger_lock(PENDING_TRIPS_LOCK_PATH):
        # Pending-trip ledger: latest-observation-wins dedup across
        # cron ticks. Load up front, observe both directions, then
        # finalise departed trains. Load failures / corruption start
        # the script with an empty ledger (logged at WARNING by the
        # loader) so a one-off bad state file never blocks the cron.
        state = _load_pending_trips(PENDING_TRIPS_PATH)
        recently_finalised = _load_recently_finalised(RECENTLY_FINALISED_PATH)

        cutoff = when - PENDING_TTL
        purged_pending = _purge_stale_entries(state, cutoff=cutoff)
        purged_finalised = _purge_finalised_entries(
            recently_finalised, cutoff=cutoff
        )
        if purged_pending or purged_finalised:
            LOGGER.info(
                "Stammstrecke: %d veraltete Pending-Einträge, %d veraltete "
                "Finalisiert-Einträge entfernt (TTL %s).",
                purged_pending,
                purged_finalised,
                PENDING_TTL,
            )

        with ExitStack() as stack:
            try:
                session = _build_session(stack)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.error(
                    "Stammstrecke: VOR-Session konnte nicht erstellt werden: %s.",
                    vor_provider.describe_failure(exc),
                )
                return 1

            for direction in DIRECTIONS:
                try:
                    status = _process_direction(
                        session,
                        direction,
                        state,
                        when=when,
                        recently_finalised=recently_finalised,
                    )
                except CircuitBreakerOpen:
                    LOGGER.warning(
                        "Stammstrecke: Circuit breaker offen (%d "
                        "aufeinanderfolgende Fehler) — weitere "
                        "Richtungen werden übersprungen.",
                        _BREAKER.consecutive_failures,
                    )
                    break

                if status in ("error", "quota_exceeded"):
                    errors += 1
                else:
                    successes += 1

        # Finalisation pass: every train whose scheduled departure
        # is in the past (relative to this tick) is committed to the
        # CSV with its latest observed delay and removed from the
        # pending ledger; its identity key flows into the
        # recently-finalised ledger so any VAO re-emission at the
        # lookahead boundary cannot produce a second CSV row.
        #
        # Trains are grouped by their scheduled year so a cron tick
        # straddling the New-Year boundary (e.g. tick at 00:05 on
        # Jan 1 finalising a train scheduled at 23:55 on Dec 31)
        # writes the row into the calendar year of the actual
        # departure — preventing the year-wide dashboard counter
        # from quietly losing the observation.
        csv_rows_written = 0
        ausfaelle_rows_written = 0
        for direction in DIRECTIONS:
            finalised = _finalize_departed(
                state,
                direction=direction.target_label,
                now=when,
                recently_finalised=recently_finalised,
            )
            if not finalised:
                continue
            # Durably record the suppression set BEFORE writing any CSV row for
            # this direction. ``_finalize_departed`` has already popped these
            # trips from ``state`` (in memory) and registered their keys in
            # ``recently_finalised``; persisting it now closes the crash window
            # between a CSV append below and the post-loop ledger save. On a
            # crash in that window the still-on-disk pending entry is re-observed
            # next tick but skipped by the now-durable ``recently_finalised``
            # guard instead of being double-finalised (a duplicate row would
            # inflate the mean delay). Trade-off: a crash here means a missing
            # row (under-count), which is preferable to a duplicate.
            _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
            # Split finalised trains into cancellations (each gets one
            # ``ausfaelle_<YYYY>.csv`` row keyed on the train's
            # scheduled departure) and regular delay observations (one
            # per-direction-per-year aggregate row with the mean delay).
            # The split is performed AFTER ``_finalize_departed`` so
            # cancelled trains still pass through the recently-finalised
            # dedup guard — a VAO re-emission of a cancelled train at
            # the lookahead boundary cannot double-count it.
            cancelled_trips = [t for t in finalised if t.cancelled]
            observed_trips = [t for t in finalised if not t.cancelled]
            for trip in cancelled_trips:
                LOGGER.info(
                    "Stammstrecke: Richtung %s — Zug %s (geplant %s) "
                    "als Ausfall finalisiert.",
                    direction.target_label,
                    sanitize_log_arg(trip.name),
                    sanitize_log_arg(trip.scheduled.isoformat()),
                )
                append_ausfall_row(
                    timestamp=trip.scheduled,
                    direction=direction.target_label,
                    line=trip.name,
                )
                ausfaelle_rows_written += 1
            if not observed_trips:
                continue
            by_year: dict[int, list[_PendingTrip]] = {}
            for trip in observed_trips:
                by_year.setdefault(trip.scheduled.year, []).append(trip)
            for year in sorted(by_year):
                year_trips = by_year[year]
                mean_minutes = float(
                    statistics.mean(t.latest_delay_minutes for t in year_trips)
                )
                # Use the latest scheduled time in the year-group as
                # the row timestamp — anchors the row to the actual
                # departure window rather than the (potentially
                # next-year) cron wall clock.
                row_timestamp = max(t.scheduled for t in year_trips)
                LOGGER.info(
                    "Stammstrecke: Richtung %s, Jahr %d — %d Zug/Züge "
                    "finalisiert, ⌀ %.2f Minuten (Schwelle %d).",
                    direction.target_label,
                    year,
                    len(year_trips),
                    mean_minutes,
                    DELAY_THRESHOLD_MINUTES,
                )
                append_stammstrecke_row(
                    timestamp=row_timestamp,
                    direction=direction.target_label,
                    delay_minutes=mean_minutes,
                )
                csv_rows_written += 1

        # Persist both ledgers AFTER finalisation. Save recently_finalised
        # FIRST so that, on a crash between the two writes, the
        # suppression set is already durable — the pending entry will
        # be re-observed on the next tick but skipped by the
        # recently_finalised guard instead of being double-finalised.
        _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
        _save_pending_trips(PENDING_TRIPS_PATH, state)

    LOGGER.info(
        "Stammstrecke: %d Beobachtung(en), %d Delay-CSV-Zeile(n) + "
        "%d Ausfall-CSV-Zeile(n) geschrieben (Erfolg=%d, Fehler=%d, "
        "Pending=%d offen, Finalisiert=%d).",
        successes,
        csv_rows_written,
        ausfaelle_rows_written,
        successes,
        errors,
        len(state),
        len(recently_finalised),
    )

    # Exit 1 only if every direction failed AND at least one was attempted —
    # a CircuitBreakerOpen-only run (errors=0, successes=0) is intentional
    # short-circuiting and exits 0.
    if successes == 0 and errors > 0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
