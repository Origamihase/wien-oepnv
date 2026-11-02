#!/usr/bin/env python3
"""Stammstrecke delay monitor via VAO ``/departureBoard`` at Wien Hauptbahnhof.

The design notes (motivation, quota, direction labels, platform gate,
latest-wins, migration) are the docstring of
:mod:`src.stammstrecke.hbf_board`, which holds the moved request and
constants; :mod:`src.stammstrecke.hbf_classify` holds the classifier.
"""

from __future__ import annotations

import logging
import os
import statistics
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
from src.utils import logging as utils_logging  # noqa: E402
from src.utils import vao_version  # noqa: E402
from src.utils.stammstrecke_poll import (  # noqa: E402
    POLL_STATUS_PATH,
    RESULT_BREAKER,
    RESULT_OK,
    answer_is_incomplete,
    load_poll_status,
    record_poll,
    save_poll_status,
)
from src.utils.stats import (  # noqa: E402
    DIRECTION_SILENCE_WINDOW_HOURS,
    append_ausfall_row,
    append_stammstrecke_row,
    find_silent_directions,
)

# Reuse pending-state + ledger infrastructure from the legacy /trip-
# based monitor. The private prefix is intentional in that script;
# importing across scripts is the transitional shape until the legacy
# script is removed in a follow-up PR.
from src.stammstrecke.core import (  # noqa: E402
    BREAKER_FAILURE_THRESHOLD as BREAKER_FAILURE_THRESHOLD,
    BREAKER_RECOVERY_TIMEOUT as BREAKER_RECOVERY_TIMEOUT,
    DELAY_THRESHOLD_MINUTES as DELAY_THRESHOLD_MINUTES,
    MAX_QUERY_TIMEOUT as MAX_QUERY_TIMEOUT,
    PENDING_TRIPS_LOCK_PATH as PENDING_TRIPS_LOCK_PATH,
    PENDING_TRIPS_PATH as PENDING_TRIPS_PATH,
    PENDING_TTL as PENDING_TTL,
    QUERY_TIMEOUT as QUERY_TIMEOUT,
    RECENTLY_FINALISED_PATH as RECENTLY_FINALISED_PATH,
    _now_vienna as _now_vienna,
    _S_BAHN_LINE_RE as _S_BAHN_LINE_RE,
)
from src.stammstrecke.ledger import (  # noqa: E402
    _canonical_line_name as _canonical_line_name,
    _ledger_lock as _ledger_lock,
    _load_pending_trips as _load_pending_trips,
    _load_recently_finalised as _load_recently_finalised,
    _purge_finalised_entries as _purge_finalised_entries,
    _purge_stale_entries as _purge_stale_entries,
    _save_pending_trips as _save_pending_trips,
    _save_recently_finalised as _save_recently_finalised,
)
from src.stammstrecke.vao_trip import (  # noqa: E402
    _QuotaExceeded as _QuotaExceeded,
    _build_session as _build_session,
    _charge_one_request as _charge_one_request,
)
from src.stammstrecke.legs import (  # noqa: E402
    _elapsed_minutes as _elapsed_minutes,
    _finalize_departed as _finalize_departed,
    _observe_legs as _observe_legs,
    _parse_vao_dt as _parse_vao_dt,
    _SbahnLegObservation as _SbahnLegObservation,
)
from src.stammstrecke.hbf_board import (  # noqa: E402
    HAUPTBAHNHOF_VOR_ID as HAUPTBAHNHOF_VOR_ID,
    DEPARTURE_BOARD_DURATION_MIN as DEPARTURE_BOARD_DURATION_MIN,
    IMPLAUSIBLE_DELAY as IMPLAUSIBLE_DELAY,
    STAMMSTRECKE_HBF_TRACK_TRUNKS as STAMMSTRECKE_HBF_TRACK_TRUNKS,
    _TRACK_TRUNK_RE as _TRACK_TRUNK_RE,
    DIRECTION_LABEL_SOUTHBOUND as DIRECTION_LABEL_SOUTHBOUND,
    DIRECTION_LABEL_NORTHBOUND as DIRECTION_LABEL_NORTHBOUND,
    LEGACY_DIRECTION_LABEL_NORTHBOUND as LEGACY_DIRECTION_LABEL_NORTHBOUND,
    DIRECTION_LABELS as DIRECTION_LABELS,
    HBF_REFERENCE_LATITUDE as HBF_REFERENCE_LATITUDE,
    _query_departure_board as _query_departure_board,
)
from src.stammstrecke.hbf_classify import (  # noqa: E402
    LOGGER as LOGGER,
    _is_sbahn_line as _is_sbahn_line,
    _track_trunk as _track_trunk,
    _extract_track_string as _extract_track_string,
    _departure_line_name as _departure_line_name,
    _departure_is_cancelled as _departure_is_cancelled,
    _departure_delay_minutes as _departure_delay_minutes,
    _CollectionDiagnostics as _CollectionDiagnostics,
    _collect_hbf_observations as _collect_hbf_observations,
    _log_track_drops as _log_track_drops,
    _count_readable_departures as _count_readable_departures,
    _TRAIN_NUMBER_NAME_RE as _TRAIN_NUMBER_NAME_RE,
    _without_train_number_twins as _without_train_number_twins,
)


# ---- Departure-board request + parse --------------------------------------


_BREAKER = CircuitBreaker(
    "stammstrecke-hbf-vor",
    failure_threshold=BREAKER_FAILURE_THRESHOLD,
    recovery_timeout=BREAKER_RECOVERY_TIMEOUT,
)


def configure_logging() -> None:
    """Install the project's :class:`SafeFormatter` for this script."""

    setup_script_logging(logging.INFO)
    # urllib3 emits one INFO line per VAO request which clutters the
    # workflow log without adding diagnostic value. Mirrors the existing
    # legacy-script pattern.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---- Main flow ------------------------------------------------------------


@dataclass
class _TickTally:
    """What one poll saw, for ``cache/stammstrecke/poll_status.json``.

    Filled by :func:`_process_tick` when the caller passes one. ``error``
    is a short, leak-safe reason: the HTTP code, or the exception type with
    its masked message (:func:`_failure_reason`).
    """

    departures: int = 0
    trains: int = 0
    error: str = ""


def _failure_reason(exc: BaseException) -> str:
    """Type and masked message of a failed poll (:func:`vor.describe_failure`)."""

    return vor_provider.describe_failure(exc)


def _process_tick(
    session: requests.Session,
    state: dict[Any, Any],
    *,
    when: datetime,
    recently_finalised: Mapping[str, datetime] | None = None,
    tally: _TickTally | None = None,
) -> str:
    """Single ``/departureBoard`` poll, classify, and observe.

    Returns one of:

    * ``"ok"`` — query succeeded and observations were folded into
      the pending state (also when the board held no Stammstrecke train:
      that is a working fetch, see :mod:`src.utils.stammstrecke_poll`);
    * ``"error"`` — VAO transport / parse error (already logged);
    * ``"incomplete"`` — the board lists departures but none carries the
      fields the monitor reads; nothing is observed;
    * ``"quota_exceeded"`` — daily quota cap hit before the call.

    ``CircuitBreakerOpen`` is re-raised so :func:`main` can short-
    circuit the rest of the tick.
    """
    if tally is None:
        tally = _TickTally()

    LOGGER.info(
        "Stammstrecke (Hbf): /departureBoard für %s (id=%s, duration=%d).",
        when.isoformat(),
        HAUPTBAHNHOF_VOR_ID,
        DEPARTURE_BOARD_DURATION_MIN,
    )

    try:
        # Charge the VAO daily quota BEFORE consulting the breaker: a quota
        # breach is a LOCAL budget signal, not an upstream-health failure, so
        # it must not count toward the breaker's failure threshold. Charging
        # inside the breaker-wrapped call let ~10 quota-blocked ticks OPEN the
        # breaker for BREAKER_RECOVERY_TIMEOUT and silently skip the first
        # ticks after the midnight quota reset. ``_QuotaExceeded`` is caught below.
        _charge_one_request(when)
        departures = _BREAKER.call(_query_departure_board, session, when=when)
    except CircuitBreakerOpen:
        raise
    except _QuotaExceeded as exc:
        LOGGER.warning(
            "Stammstrecke (Hbf): Tageslimit erreicht — Abfrage übersprungen (%s).",
            utils_logging.sanitize_log_arg(str(exc)),
        )
        return "quota_exceeded"
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        LOGGER.warning(
            "Stammstrecke (Hbf): /departureBoard fehlgeschlagen: HTTP %s.",
            utils_logging.sanitize_log_arg(str(status) if status is not None else "?"),
        )
        tally.error = f"HTTP {status if status is not None else '?'}"
        return "error"
    except Exception as exc:
        # Security: ``VorAuth`` injects the ``accessId`` into the URL
        # before the prepared request leaves the session, so a
        # ``RequestException`` may carry that URL in its message —
        # :func:`_failure_reason` masks it.
        reason = _failure_reason(exc)
        LOGGER.warning("Stammstrecke (Hbf): /departureBoard fehlgeschlagen: %s.", reason)
        tally.error = reason
        return "error"

    tally.departures = len(departures)
    readable = _count_readable_departures(departures)
    if answer_is_incomplete(departures=len(departures), readable=readable):
        LOGGER.warning(
            "Stammstrecke (Hbf): /departureBoard-Antwort unvollständig — %d "
            "Abfahrt(en), keine mit Linie, Planzeit und Bahnsteig.",
            len(departures),
        )
        tally.error = f"{len(departures)} Abfahrten ohne lesbare Felder"
        return "incomplete"

    by_direction, diagnostics = _collect_hbf_observations(departures)
    tally.trains = diagnostics.on_stammstrecke
    _log_track_drops(
        diagnostics.dropped_no_track,
        diagnostics.dropped_non_stammstrecke_track,
    )

    written_per_dir: dict[str, int] = {}
    for direction in DIRECTION_LABELS:
        observations = by_direction.get(direction, [])
        if not observations:
            written_per_dir[direction] = 0
            continue
        written_per_dir[direction] = _observe_legs(
            state,
            observations,
            direction=direction,
            now=when,
            recently_finalised=recently_finalised,
        )

    LOGGER.info(
        "Stammstrecke (Hbf): %d Abfahrten gesamt — %s=%d, "
        "%s=%d (Ledger-Updates), %d Ausfall-Beobachtung(en).",
        len(departures),
        DIRECTION_LABEL_SOUTHBOUND,
        written_per_dir.get(DIRECTION_LABEL_SOUTHBOUND, 0),
        DIRECTION_LABEL_NORTHBOUND,
        written_per_dir.get(DIRECTION_LABEL_NORTHBOUND, 0),
        diagnostics.cancelled_observed,
    )
    return "ok"


def _poll_listed_versions(
    session: requests.Session,
    state: dict[Any, Any],
    *,
    when: datetime,
    recently_finalised: Mapping[str, datetime] | None,
    tally: _TickTally,
    version_path: Path | None = None,
) -> str:
    """Poll the newest VAO version in service; when it fails, the one before.

    Operator decision 2026-10-29 (Michael): "Lies automatisch aus, was die
    neuste Version ist. Wenn sie nicht funktioniert, nimm automatisch die
    davor". The order, the 24-hour back seat of a failed version and the
    stored choice: :mod:`src.utils.vao_version`. A version pinned by the
    environment is polled as it is. Returns the outcome of the last attempt.
    """
    if vor_provider.version_pinned_by_env():
        return _process_tick(session, state, when=when,
                             recently_finalised=recently_finalised, tally=tally)
    listed = vor_provider.fetch_version_list()
    choice = vao_version.load_state(version_path)
    before = choice.version
    order = vao_version.candidates(listed, choice, default=vor_provider.DEFAULT_VERSION, now=when)
    status = "error"
    for attempt, version in enumerate(order):
        if attempt:
            LOGGER.warning(
                "Stammstrecke (Hbf): VAO-Version %s antwortet nicht brauchbar (%s) — versuche %s.",
                order[attempt - 1],
                tally.error,
                version,
            )
            tally.departures, tally.trains, tally.error = 0, 0, ""
        vor_provider.use_version(version)
        status = _process_tick(session, state, when=when,
                               recently_finalised=recently_finalised, tally=tally)
        if status == "quota_exceeded":
            break
        vao_version.record_outcome(choice, version=version, worked=status == "ok",
                                   now=when, listed=listed)
        if status == "ok":
            break
    if before is not None and choice.version != before:
        message = f"VAO-Version gewechselt: {before} → {choice.version}"
        LOGGER.warning("Stammstrecke (Hbf): %s.", message)
        _emit_workflow_warning("VAO-Version gewechselt", message)
    vao_version.save_state(choice, version_path)
    return status


def _record_poll_outcome(when: datetime, result: str, tally: _TickTally) -> None:
    """Fold this poll into ``cache/stammstrecke/poll_status.json`` (best-effort)."""

    try:
        previous = load_poll_status(POLL_STATUS_PATH)
        status = record_poll(
            previous,
            when=when,
            result=result,
            error="" if result == RESULT_OK else tally.error,
            departures=tally.departures,
            trains=tally.trains,
        )
        save_poll_status(status, POLL_STATUS_PATH)
    except Exception as exc:  # pragma: no cover - observability must not crash the tick
        LOGGER.warning(
            "Stammstrecke (Hbf): Abrufstatus nicht gespeichert: %s.",
            _failure_reason(exc),
        )


def _emit_workflow_warning(title: str, message: str) -> None:
    """Emit a GitHub Actions ``::warning`` annotation when running in CI.

    Annotations surface in the run summary and the job UI, which is what makes
    a degraded state visible without anyone grepping INFO logs. Suppressed
    outside Actions so local runs stay clean.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    safe_title = utils_logging.sanitize_log_arg(title).replace("\n", " ")
    safe_message = utils_logging.sanitize_log_arg(message).replace("\n", " ")
    print(f"::warning title={safe_title}::{safe_message}", flush=True)


def _report_silent_directions(now: datetime) -> list[str]:
    """Warn loudly when one direction has gone silent while the other reports.

    Audit B.1/B.2: the northbound direction stopped producing rows on
    2026-08-14 and nothing flagged it for 30 days, while the published 30-day
    figures kept presenting the surviving southbound sample as a whole-corridor
    one. ``_process_tick`` logs per-direction counts at INFO and returns ``ok``
    regardless, which is invisible in practice.

    The detection rule lives in :func:`src.utils.stats.find_silent_directions`
    so this script and ``scripts/health_check.py`` cannot drift apart; see that
    module for why a relative (direction-vs-peer) rule is used rather than an
    absolute staleness threshold.

    Returns the silent directions (empty when healthy) so callers and tests can
    act on the degraded state.
    """
    try:
        silent = find_silent_directions(directions=DIRECTION_LABELS, now=now)
    except Exception as exc:  # pragma: no cover - observability must not crash the tick
        LOGGER.warning(
            "Stammstrecke (Hbf): Richtungs-Abdeckung konnte nicht geprüft "
            "werden: %s.",
            utils_logging.sanitize_log_arg(str(exc)),
        )
        return []

    if not silent:
        return []

    joined = ", ".join(silent)
    LOGGER.warning(
        "Stammstrecke (Hbf): DEGRADED — Richtung %s ohne Messwerte in den "
        "letzten %.0f h, während die Gegenrichtung weiterhin liefert. "
        "Veröffentlichte Korridor-Kennzahlen decken derzeit nur die aktive "
        "Richtung ab.",
        utils_logging.sanitize_log_arg(joined),
        DIRECTION_SILENCE_WINDOW_HOURS,
    )
    _emit_workflow_warning(
        "stammstrecke-direction",
        f"Richtung {joined} liefert seit >= {DIRECTION_SILENCE_WINDOW_HOURS:.0f} h "
        "keine Messwerte, die Gegenrichtung schon — Kennzahlen decken nur den "
        "aktiven Korridor ab.",
    )
    return silent


def main() -> int:
    """Entry point. Returns ``0`` on success (incl. degraded), ``1`` on full failure.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded. A
    ``CircuitBreakerOpen`` short-circuit appends nothing to the CSV;
    the feed naturally degrades to "no Stammstrecke entry" because the
    most-recent observations roll out of the 1-hour feed window
    without replacement.
    """

    configure_logging()

    when = _now_vienna()

    with _ledger_lock(PENDING_TRIPS_LOCK_PATH):
        state = _load_pending_trips(PENDING_TRIPS_PATH)
        recently_finalised = _load_recently_finalised(RECENTLY_FINALISED_PATH)

        cutoff = when - PENDING_TTL
        purged_pending = _purge_stale_entries(state, cutoff=cutoff)
        purged_finalised = _purge_finalised_entries(
            recently_finalised, cutoff=cutoff
        )
        if purged_pending or purged_finalised:
            LOGGER.info(
                "Stammstrecke (Hbf): %d veraltete Pending-Einträge, %d "
                "veraltete Finalisiert-Einträge entfernt (TTL %s).",
                purged_pending,
                purged_finalised,
                PENDING_TTL,
            )

        successes = 0
        errors = 0
        tally = _TickTally()

        with ExitStack() as stack:
            try:
                session = _build_session(stack)
            except Exception as exc:  # pragma: no cover - defensive
                reason = _failure_reason(exc)
                LOGGER.error(
                    "Stammstrecke (Hbf): VOR-Session konnte nicht erstellt werden: %s.",
                    reason,
                )
                _record_poll_outcome(when, "error", _TickTally(error=reason))
                return 1

            try:
                status = _poll_listed_versions(
                    session,
                    state,
                    when=when,
                    recently_finalised=recently_finalised,
                    tally=tally,
                )
                poll_result = status
            except CircuitBreakerOpen:
                LOGGER.warning(
                    "Stammstrecke (Hbf): Circuit breaker offen (%d "
                    "aufeinanderfolgende Fehler) — Tick übersprungen.",
                    _BREAKER.consecutive_failures,
                )
                status = "error"
                poll_result = RESULT_BREAKER

            if status in ("error", "incomplete", "quota_exceeded"):
                errors += 1
            else:
                successes += 1

            # Every poll leaves its outcome behind, the failed ones too: the
            # health check turns red on a broken fetch, never on an empty
            # board (operator decision 2026-10-10, src/utils/stammstrecke_poll.py).
            _record_poll_outcome(when, poll_result, tally)

        # Finalisation pass per direction. Trains scheduled <= now are
        # popped from the pending state, their latest observation is
        # written to the CSV, and their identity key is registered in
        # ``recently_finalised`` so a VAO re-emission at the lookahead
        # boundary cannot produce a duplicate CSV row.
        #
        # Backwards-compat: for the northbound direction we also drain
        # any pending entries that survived the 2026-05-15 rename
        # under the legacy ``Floridsdorf`` direction value. The CSV
        # row is still written under the canonical ``Praterstern``
        # label so the dashboard sees a single direction-stream.
        csv_rows_written = 0
        ausfaelle_rows_written = 0
        for direction in DIRECTION_LABELS:
            finalised = _finalize_departed(
                state,
                direction=direction,
                now=when,
                recently_finalised=recently_finalised,
            )
            if direction == DIRECTION_LABEL_NORTHBOUND:
                legacy_finalised = _finalize_departed(
                    state,
                    direction=LEGACY_DIRECTION_LABEL_NORTHBOUND,
                    now=when,
                    recently_finalised=recently_finalised,
                )
                if legacy_finalised:
                    LOGGER.info(
                        "Stammstrecke (Hbf): %d Pending-Eintrag/Einträge mit "
                        "Legacy-Richtungs-Label '%s' finalisiert "
                        "(umgebucht auf '%s' im CSV).",
                        len(legacy_finalised),
                        LEGACY_DIRECTION_LABEL_NORTHBOUND,
                        DIRECTION_LABEL_NORTHBOUND,
                    )
                    finalised = list(finalised) + list(legacy_finalised)
            # Popped and registered in ``recently_finalised`` all the same,
            # so a dropped twin cannot come back on the next tick.
            finalised = _without_train_number_twins(finalised)
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
            # Split finalised trains: cancellations go to a dedicated
            # ledger (one CSV row per cancelled train so each shows up
            # as a discrete event in the dashboard count), non-
            # cancelled observations contribute to the per-tick mean-
            # delay aggregate (one CSV row per direction per year).
            # Both ledgers are appended atomically by ``src.utils.stats``;
            # an IO failure on one side does not block the other.
            cancelled_trips = [t for t in finalised if t.cancelled]
            observed_trips = [t for t in finalised if not t.cancelled]
            for trip in cancelled_trips:
                LOGGER.info(
                    "Stammstrecke (Hbf): Richtung %s — Zug %s (geplant %s) "
                    "als Ausfall finalisiert.",
                    direction,
                    utils_logging.sanitize_log_arg(trip.name),
                    utils_logging.sanitize_log_arg(trip.scheduled.isoformat()),
                )
                append_ausfall_row(
                    timestamp=trip.scheduled,
                    direction=direction,
                    line=trip.name,
                )
                ausfaelle_rows_written += 1
            if not observed_trips:
                continue
            by_year: dict[int, list[Any]] = {}
            for trip in observed_trips:
                by_year.setdefault(trip.scheduled.year, []).append(trip)
            for year in sorted(by_year):
                year_trips = by_year[year]
                mean_minutes = float(
                    statistics.mean(t.latest_delay_minutes for t in year_trips)
                )
                row_timestamp = max(t.scheduled for t in year_trips)
                LOGGER.info(
                    "Stammstrecke (Hbf): Richtung %s, Jahr %d — %d Zug/Züge "
                    "finalisiert, ⌀ %.2f Minuten (Schwelle %d).",
                    direction,
                    year,
                    len(year_trips),
                    mean_minutes,
                    DELAY_THRESHOLD_MINUTES,
                )
                append_stammstrecke_row(
                    timestamp=row_timestamp,
                    direction=direction,
                    delay_minutes=mean_minutes,
                )
                csv_rows_written += 1

        # Persist both ledgers AFTER finalisation. Save
        # recently_finalised FIRST so that on a crash between the two
        # writes, the suppression set is durable — the pending entry
        # will be re-observed on the next tick but skipped by the
        # ``recently_finalised`` guard instead of being double-
        # finalised.
        _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
        _save_pending_trips(PENDING_TRIPS_PATH, state)

    LOGGER.info(
        "Stammstrecke (Hbf): %d Beobachtungs-Tick(s), %d Delay-CSV-Zeile(n) "
        "+ %d Ausfall-CSV-Zeile(n) geschrieben (Erfolg=%d, Fehler=%d, "
        "Pending=%d offen, Finalisiert=%d).",
        1,
        csv_rows_written,
        ausfaelle_rows_written,
        successes,
        errors,
        len(state),
        len(recently_finalised),
    )

    degraded_directions = _report_silent_directions(when)

    if successes == 0 and errors > 0:
        return 1
    # A silent direction is reported, never fatal. Returning non-zero here
    # would abort the ``Refresh Stammstrecke status`` step (the workflow runs
    # it under ``bash -e``) and with it the rest of the cycle — so a known,
    # weeks-long closure would stop the feed build every 30 minutes. Data
    # collection must keep running precisely while half the corridor is down.
    # ``scripts/health_check.py`` repeats the finding as a note every 6 h, and
    # the website shows the coverage gap to readers.
    _ = degraded_directions
    return 0


# Public names of this script: its own entry points and the names other
# files and tests read through it. The code lives in src/ (700-line limit,
# 2026-11-02); listing the imports marks them as deliberate re-exports.
__all__ = [
    "DEPARTURE_BOARD_DURATION_MIN",
    "DIRECTION_LABELS",
    "DIRECTION_LABEL_NORTHBOUND",
    "DIRECTION_LABEL_SOUTHBOUND",
    "HAUPTBAHNHOF_VOR_ID",
    "HBF_REFERENCE_LATITUDE",
    "IMPLAUSIBLE_DELAY",
    "LEGACY_DIRECTION_LABEL_NORTHBOUND",
    "STAMMSTRECKE_HBF_TRACK_TRUNKS",
    "main",
    "MAX_QUERY_TIMEOUT",
    "QUERY_TIMEOUT",
    "_S_BAHN_LINE_RE",
    "_canonical_line_name",
    "_elapsed_minutes",
    "_parse_vao_dt",
    "_SbahnLegObservation",
    "_TRACK_TRUNK_RE",
    "_is_sbahn_line",
    "_track_trunk",
    "_extract_track_string",
    "_departure_line_name",
    "_departure_is_cancelled",
    "_departure_delay_minutes",
    "_CollectionDiagnostics",
    "_TRAIN_NUMBER_NAME_RE",
]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
