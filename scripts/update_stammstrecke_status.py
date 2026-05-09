#!/usr/bin/env python3
"""Monitor delays on the S-Bahn Stammstrecke (Wien Floridsdorf ↔ Wien Meidling).

Queries direct S-Bahn connections via the VOR/VAO ReST ``/trip`` endpoint
for **both directions** independently and emits up to two schema-compliant
events into ``cache/stammstrecke/events.json`` — one per direction whose
**median** departure delay exceeds :data:`DELAY_THRESHOLD_MINUTES`
minutes. Directions are evaluated strictly separately because merging
both into a single sample dilutes the signal — a station with a major
incident in one direction often runs normally in the opposite direction.

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
  call's medians and events are computed independently. The cache
  output contains 0, 1, or 2 events depending on which direction(s)
  exceeded the threshold.
- **Direct-connection filter**: only single-ride-leg trips are eligible
  for the median. The VAO ``maxChange=0`` query parameter gives the
  upstream a hint, and a client-side leg-count check is the second
  layer of defence — so a multi-stop trip the API still returns under
  ``maxChange=0`` does not bleed into the signal.
- **S-Bahn product filter**: the eligible leg must carry an S-Bahn
  product label. We accept either ``leg.category in {"S","SB"}``,
  ``leg.name`` matching ``S\\d+``, or ``leg.Product[].catOut`` /
  ``Product[].line`` matching the same — covering the known
  upstream-shape variants without committing to a single one.
- **Self-Healing on degradation**: if either condition holds the
  events file is *unconditionally* reset to ``[]``:

      * the API is unreachable (``RequestException``,
        ``CircuitBreakerOpen``, JSON decode failure, malformed payload);
      * the median for *all* monitored directions is ``≤ 9`` minutes.

  This keeps the RSS feed free of stale warnings — a transient blip or
  recovery becomes invisible to feed readers within at most one cron
  tick (30 minutes).
- **First-seen persistence**: when a direction was *already* over the
  threshold in the previous run, its ``first_seen`` timestamp is
  carried over so the GUID stays stable for the duration of an
  episode. RSS readers therefore see one continuously-updated entry
  per episode rather than a flood of new entries every 30 minutes.
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
- **Timezone**: GitHub Actions runs in UTC. All timestamps inside the
  emitted events (``pubDate``, ``starts_at``, ``first_seen``) are
  localised to ``Europe/Vienna`` via :mod:`zoneinfo` and serialised as
  ISO 8601 strings with offset, matching
  ``docs/schema/events.schema.json``.
- **Schema**: each emitted event mirrors the canonical FeedItem shape
  every other provider produces (``source`` / ``category`` / ``title``
  / ``description`` / ``link`` / ``guid`` / ``pubDate`` / ``starts_at``
  / ``ends_at`` / ``first_seen`` / ``_identity``). Per-direction
  events differ in ``description`` (target station name +
  ``[Seit DD.MM.YYYY]``), ``guid`` and ``_identity`` so feed readers
  treat them as separate notifications.
- **Logging**: every diagnostic message is routed through
  :func:`src.feed.logging_safe.setup_script_logging` so log injection
  / ANSI / BiDi attacks via upstream-controlled fields are sanitised
  at the formatter layer. URLs containing the post-``VorAuth``
  ``accessId`` query parameter are NEVER logged via ``%s`` /
  ``exc_info=True`` (mirrors the canonical pattern from
  ``scripts/update_vor_cache.py``).
"""

from __future__ import annotations

import json as _json_lib
import logging
import re
import statistics
import sys
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, TypeGuard
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.feed.providers import MAX_STAMMSTRECKE_CACHE_BYTES  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.utils.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
)
from src.utils.files import atomic_write, read_capped_json  # noqa: E402
from src.utils.http import request_safe, session_with_retries  # noqa: E402
from src.utils.ids import make_guid  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stations import canonical_name, display_name  # noqa: E402
from src.utils.stats import append_stammstrecke_row  # noqa: E402

LOGGER = logging.getLogger("update_stammstrecke_status")

# ---- Operating parameters ---------------------------------------------------

# VOR/VAO station IDs for the two Stammstrecke termini. Sourced from
# ``data/stations.json`` and pinned here so a station-directory drift
# cannot silently change which stops the script polls.
FLORIDSDORF_VOR_ID = "490033400"
MEIDLING_VOR_ID = "490101500"

# Canonical station-directory keys used to look up the user-facing labels
# via ``src.utils.stations``. These names MUST exist in
# ``data/stations.json`` (or one of the configured aliases) so the
# directory lookup succeeds. The fallback path in ``_short_target_label``
# preserves the literal value if the lookup fails.
FLORIDSDORF_CANONICAL_SEED = "Wien Floridsdorf"
MEIDLING_CANONICAL_SEED = "Wien Meidling"

# Threshold above which the median delay of a direction generates a feed
# entry. The user-facing semantics are "more than 9 minutes" — a median
# of exactly 9 minutes does NOT trigger the event.
DELAY_THRESHOLD_MINUTES = 9

# Number of trips to fetch per direction in a single ``/trip`` call.
# Pinned to ``6`` — the VAO contractual maximum (``numF`` accepts 1..6,
# see ``docs/reference/trip.md``). The 30-minute cron tick combined
# with ``maxChange=0`` typically yields 4-6 S-Bahn legs after the
# product filter; pinning at the API ceiling maximises the median's
# sample size without inflating the per-day quota cost (the VAO
# response size is identical between numF=5 and numF=6).
MAX_TRIPS_PER_QUERY = 6

# Per-call HTTP budget (seconds). Enforced at the ``fetch_content_safe``
# layer (``src/utils/http.py``) which forwards the kwarg verbatim to
# ``requests.Session.get``. Bound between 1 and ``MAX_QUERY_TIMEOUT`` so
# a future env-driven misconfiguration cannot disable the timeout.
QUERY_TIMEOUT = 20
MAX_QUERY_TIMEOUT = 30

# Circuit-breaker policy. Aligned with the existing VAO Start budget of
# 100 requests/day — the per-direction-level breaker stays OPEN for one
# hour after 10 consecutive failures, capping outage-mode traffic at 10
# attempts/hour. Normal operation produces 4 calls/h (2 directions × 2
# fires/h via the ``*/30`` cron), well below the breaker ceiling.
BREAKER_FAILURE_THRESHOLD = 10
BREAKER_RECOVERY_TIMEOUT = 3600.0

# Pattern that identifies an S-Bahn line label (``S 1``, ``S 7``,
# ``S 80`` …). Used as the secondary signal when the VAO ``category``
# field is missing or non-canonical; primary signal is
# ``category in {"S", "SB"}`` / ``Product.catOut in {"S", "SB"}``.
_S_BAHN_LINE_RE = re.compile(r"^\s*S\s*\d+\s*$", re.IGNORECASE)

VIENNA_TZ = ZoneInfo("Europe/Vienna")

OUTPUT_PATH = REPO_ROOT / "cache" / "stammstrecke" / "events.json"

# Event metadata: kept verbatim from the original pyhafas-era script so
# subscribers see the same source/category/title/link strings (i.e. the
# RSS migration is invisible to feed readers).
EVENT_SOURCE = "ÖBB"
EVENT_CATEGORY = "Störung"
EVENT_TITLE = "S-Bahn Stammstrecke Verspätungen"
EVENT_LINK = (
    "https://www.oebb.at/de/fahrplan/fahrplanauskunft-und-stoerungsinformation/aktuelle-stoerungsmeldungen"
)


def _short_target_label(seed_name: str) -> str:
    """Return the compact user-facing label for *seed_name*.

    Looks up the canonical station name in the project's station
    directory (``data/stations.json`` via :mod:`src.utils.stations`),
    applies ``display_name`` for project-wide overrides (e.g.
    ``Wien Mitte-Landstraße`` → ``Wien Mitte``), and strips the
    leading ``Wien `` prefix. The Stammstrecke description text
    ("in Richtung Meidling") implicitly assumes Vienna, so omitting
    the prefix produces natural German — but the canonical lookup
    still drives the suffix portion, so a future rename in the
    directory propagates automatically.

    The fallback chain — try directory, accept any
    :class:`Exception`, finally strip ``Wien `` from the seed —
    keeps the script resilient against a missing/corrupt directory
    file (fresh clone before stations sync, restricted CI runner
    without ``data/`` mounted, etc.).
    """

    try:
        canonical = canonical_name(seed_name)
    except Exception:  # pragma: no cover - defensive: directory load failure
        canonical = None

    name = display_name(canonical) if canonical else seed_name.strip()
    if name.startswith("Wien "):
        return name[len("Wien ") :]
    return name


@dataclass(frozen=True)
class _Direction:
    """A single Stammstrecke query direction.

    Carries the per-direction parameters (origin/destination VOR IDs,
    user-facing target label for the description, identity prefix for
    the deduplication key and GUID) so the main loop can iterate over
    both directions without branching on direction-specific logic.

    ``target_label`` is populated at module import time via
    :func:`_short_target_label`, which routes through the project's
    canonical station directory rather than hardcoding the display
    name.
    """

    origin_id: str
    destination_id: str
    target_label: str
    identity_prefix: str


DIRECTIONS: tuple[_Direction, ...] = (
    _Direction(
        origin_id=FLORIDSDORF_VOR_ID,
        destination_id=MEIDLING_VOR_ID,
        target_label=_short_target_label(MEIDLING_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_meidling",
    ),
    _Direction(
        origin_id=MEIDLING_VOR_ID,
        destination_id=FLORIDSDORF_VOR_ID,
        target_label=_short_target_label(FLORIDSDORF_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_floridsdorf",
    ),
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


def _now_vienna() -> datetime:
    """Return the current time, anchored to Europe/Vienna."""

    return datetime.now(tz=VIENNA_TZ)


def _format_minutes(value: float) -> str:
    """Format *value* as a German-readable minute count.

    ``round(x, 1)`` followed by ``:g`` strips trailing ``.0`` so a
    whole-minute median renders as ``"12"`` rather than ``"12.0"``,
    while a fractional median keeps its single decimal (``"12.5"``).
    """

    rounded = round(value, 1)
    return f"{rounded:g}"


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

    Threading: the ``_QUOTA_LOCK`` from :mod:`src.providers.vor` is
    held across the read-then-increment so two parallel script
    invocations cannot race past the cap. The Stammstrecke cron does
    not run in parallel (concurrency group ``external-api-fetch``),
    but the lock keeps the contract identical to every other VOR
    consumer for free.
    """

    with vor_provider._QUOTA_LOCK:
        _, current_usage = vor_provider.load_request_count()
        if current_usage >= vor_provider.MAX_REQUESTS_PER_DAY:
            raise _QuotaExceeded(
                f"VAO daily quota exhausted ({current_usage}/"
                f"{vor_provider.MAX_REQUESTS_PER_DAY})"
            )
        vor_provider.save_request_count(now)


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

    # Param-naming notes for the VAO ``/trip`` endpoint (Senior-API-
    # Integration audit, 2026-05-09 — third iteration after PR #1385
    # diagnostics + PR #1387 originExtId attempt both yielded HTTP 400):
    #
    # * **ID encoding** — VAO accepts external station IDs (bare-numeric
    #   IBNR-style codes, e.g. ``490033400`` from ``data/stations.json``)
    #   in two forms:
    #     1. ``originExtId=490033400`` — DB Navigator HAFAS convention.
    #     2. ``originId=extId::490033400`` — VAO/ÖBB convention per
    #        Kapitel "Identifikationsarten von Ortsobjekten" (S. 19) of
    #        the Handbuch_VAO_ReST_API_latest.pdf: the ``extId::``
    #        prefix encodes "treat the value as an external ID". The
    #        2026-05-09 cron runs revealed that form #1 (PR #1387)
    #        triggered HTTP 400 with empty body on this VAO instance;
    #        we now use form #2.
    # * ``Accept: application/json`` header — replaces the
    #   ``format=json`` query parameter. The trip.md curl example does
    #   not include ``format=json``, only the Accept header; the
    #   ``departureBoard`` endpoint is the outlier that documents both.
    # * ``date`` / ``time`` — explicitly pinned to the current Vienna
    #   wall clock so the upstream cannot drift to UTC or fail to
    #   infer "now".
    params: dict[str, str] = {
        "originId": f"extId::{direction.origin_id}",
        "destId": f"extId::{direction.destination_id}",
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "numF": str(MAX_TRIPS_PER_QUERY),
        # Force direct connections — the only Stammstrecke-relevant
        # signal is the per-S-Bahn-leg delay, and a transfer would
        # dilute the median with the (irrelevant) waiting time.
        "maxChange": "0",
        # Enable server-side realtime data so ``Origin.rtTime`` is
        # populated when available.
        "rtMode": "SERVER_DEFAULT",
    }

    endpoint = f"{vor_provider.VOR_BASE_URL}trip"

    _charge_one_request(when)

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
        payload = _json_lib.loads(content)
    except (ValueError, RecursionError) as exc:
        # Drift defence (JSON Depth-Bomb Round 5): a depth-bomb body
        # passes the size cap (it can be only a few KiB on the wire) but
        # blows the recursion limit on parse, and ``RecursionError`` is
        # NOT a subclass of ``ValueError``. Re-raise as ``ValueError`` so
        # the caller's per-direction error-isolation branch runs without
        # propagating the BaseException-rooted recursion failure further.
        raise ValueError(
            f"VAO /trip returned unparseable JSON: {type(exc).__name__}"
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


def _is_sbahn_leg(leg: object) -> bool:
    """Return ``True`` when *leg* represents a Vienna S-Bahn product.

    The filter is **strict-S**: only the literal Vienna S-Bahn product
    family (``S 1``, ``S 2``, ``S 7``, ``S 80`` …) is accepted.
    Regional Express (``REX``), Regional (``R``), InterCity (``IC``),
    Railjet (``RJ``), and any non-rail product is rejected.

    Checks (any single signal is sufficient):

    * ``leg.category == "S"`` — VAO's preferred field;
    * ``leg.name`` matching ``^\\s*S\\s*\\d+\\s*$`` — fallback for older
      VAO peers that only set the human-readable label;
    * ``leg.Product[].catOut == "S"`` or ``Product[].line`` matching
      ``^\\s*S\\s*\\d+\\s*$`` — the JSON-RPC nested form some VAO
      releases use.

    The previous-generation matcher also accepted ``"SB"`` as category;
    the 2026-05-09 Senior-API-Integration audit removed it because
    ``SB`` is ambiguous in the German-speaking ÖV space (it can denote
    *Schnellbahn* — a synonym for S-Bahn — but also *Schnellbus* in
    some VAO/ÖBB regional dialects, and there is no SB service on the
    Stammstrecke). Strict ``"S"`` keeps the filter aligned with the
    user-visible Vienna S-Bahn product mapping. A future legitimate
    ``SB`` line would be picked up by the ``name``/``line`` regex
    anyway (``"SB 1"`` does not match, but Vienna does not run that
    line).

    Accepts ``object`` (rather than ``Mapping``) so the defensive
    ``isinstance(leg, Mapping)`` gate is reachable at type-check time
    — a non-mapping payload (a planted ``None`` / ``str`` / ``list``
    that slipped past upstream JSON parsing) returns ``False`` cleanly
    instead of triggering an unreachable-statement diagnostic.
    """

    if not isinstance(leg, Mapping):
        return False

    category = (str(leg.get("category") or "")).strip().upper()
    if category == "S":
        return True

    name = str(leg.get("name") or "").strip()
    if _S_BAHN_LINE_RE.match(name):
        return True

    products = leg.get("Product")
    if isinstance(products, list):
        candidates: list[Mapping[str, Any]] = [
            p for p in products if isinstance(p, Mapping)
        ]
    elif isinstance(products, Mapping):
        candidates = [products]
    else:
        candidates = []

    for product in candidates:
        cat_out = str(product.get("catOut") or "").strip().upper()
        if cat_out == "S":
            return True
        line = str(product.get("line") or "").strip()
        if _S_BAHN_LINE_RE.match(line):
            return True

    return False


def _parse_vao_dt(date_str: Any, time_str: Any) -> datetime | None:
    """Parse a VAO ``date``/``time`` pair into a Vienna-localised datetime.

    Accepts ``date`` in ``YYYY-MM-DD`` and ``time`` in either
    ``HH:MM:SS`` or ``HH:MM``. Returns ``None`` on any parse failure so
    callers can skip the leg without crashing the whole run.

    A naive ``datetime`` is force-localised to Europe/Vienna with
    ``fold=0`` (matches :func:`src.providers.vor._parse_dt`'s convention
    for the once-a-year DST ambiguity).
    """

    date_txt = str(date_str or "").strip()
    if not date_txt:
        return None
    time_txt = str(time_str or "").strip()
    if time_txt:
        # VAO returns either ``HH:MM:SS`` or ``HH:MM`` — accept both by
        # truncating to ``HH:MM`` (the median-aggregation arithmetic
        # below operates on minutes, so dropping seconds is lossless
        # in practice and avoids strptime branching).
        time_txt = time_txt[:5]
    else:
        time_txt = "00:00"
    try:
        naive = datetime.strptime(f"{date_txt} {time_txt}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=VIENNA_TZ, fold=0)


def _leg_departure_delay_minutes(leg: Mapping[str, Any]) -> float | None:
    """Return the leg's departure delay in fractional minutes, or None.

    Computes ``Origin.rtTime - Origin.time`` (resp. ``rtDepTime -
    depTime`` on legacy peers) in minutes:

    * **On-time (rtTime missing)** — VAO parsimoniously omits
      ``rtTime`` when realtime data confirms an on-time departure
      (echoing ``time`` would double the response size for the
      majority of trips). The 2026-05-09 Senior-API-Integration audit
      established that *missing* ``rtTime`` MUST be treated as
      ``0.0`` minutes rather than skipped — skipping it would exclude
      every on-time train from the median, biasing the result so far
      upward that an off-peak window with a single delayed train would
      cross the 9-minute threshold and emit a spurious feed event.
    * **On-time (rtTime == time)** — falls through the same arithmetic
      and yields exactly ``0.0`` minutes.
    * **Cancelled** — returns ``None`` (no signal; cancelled trains
      are not "delayed", they are "absent").
    * **Schedule unparseable** — returns ``None`` (the leg cannot
      contribute a meaningful delay value to the median).
    * **Realtime field present but unparseable** — returns ``None``
      (a malformed ``rtTime`` is treated like a missing schedule
      rather than silently coerced to zero).

    Negative delays (early departure) are possible at the timetable
    level and contribute negative values to the median, which is
    still meaningful — keep them.
    """

    origin = leg.get("Origin")
    if not isinstance(origin, Mapping):
        return None
    if origin.get("cancelled") is True or leg.get("cancelled") is True:
        return None

    sched_date = origin.get("date") or origin.get("depDate")
    sched_time = origin.get("time") or origin.get("depTime")
    scheduled = _parse_vao_dt(sched_date, sched_time)
    if scheduled is None:
        return None

    rt_time = origin.get("rtTime") or origin.get("rtDepTime")
    if not rt_time:
        # On-time per VAO contract — see docstring.
        return 0.0

    rt_date = origin.get("rtDate") or origin.get("rtDepDate") or sched_date
    actual = _parse_vao_dt(rt_date, rt_time)
    if actual is None:
        return None

    return (actual - scheduled).total_seconds() / 60.0


def _collect_sbahn_delays_minutes(
    trips: Iterable[Mapping[str, Any]],
) -> list[float]:
    """Extract S-Bahn departure delays (in minutes) from *trips*.

    Filters:

    * **Direct only** — exactly one ride leg in ``LegList.Leg``.
      Walk-only segments before/after the ride are tolerated; multi-ride
      trips (changes) are rejected because the change-waiting time would
      dilute the median.
    * **S-Bahn only** — the single ride leg must pass
      :func:`_is_sbahn_leg`.
    * **Cancellation excluded** — a cancelled leg has no delay signal.
    * **On-time legs counted** — :func:`_leg_departure_delay_minutes`
      returns ``0.0`` (not ``None``) when ``rtTime`` is missing, so
      on-time S-Bahn departures contribute to the median rather than
      being silently dropped.
    """

    delays: list[float] = []
    for trip in trips:
        leg_list = trip.get("LegList")
        if not isinstance(leg_list, Mapping):
            continue
        raw_legs = leg_list.get("Leg")
        if isinstance(raw_legs, Mapping):
            # Single-leg trips are sometimes serialised as a bare object.
            legs: list[Mapping[str, Any]] = [raw_legs]
        elif isinstance(raw_legs, list):
            legs = [item for item in raw_legs if isinstance(item, Mapping)]
        else:
            continue

        ride_legs = [
            leg
            for leg in legs
            if str(leg.get("type") or "").strip().upper() != "WALK"
        ]
        if len(ride_legs) != 1:
            continue
        leg = ride_legs[0]
        if not _is_sbahn_leg(leg):
            continue

        delay = _leg_departure_delay_minutes(leg)
        if delay is None:
            continue
        delays.append(delay)
    return delays


# ---- Cache field-preservation validators ---------------------------------

# XML 1.0 control characters that have no readability value in a preserved
# cache field. Mirrors ``src/build_feed.py:_CONTROL_RE`` so the per-field
# shape validators reject the same control-character set the canonical
# ``_sanitize_text`` filter strips from the rendered feed output. Defence
# in depth on the cached ``_identity`` / ``first_seen`` strings before they
# re-enter the build pipeline through the preserved-first-seen path.
_PRESERVED_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Max length for the preserved ``first_seen`` field. ISO 8601 timestamps
# with offset are ~25 bytes (e.g. ``2026-05-09T08:30:00+02:00``); 64 is
# 2.5x headroom for sub-second precision variants and offset names.
_MAX_PRESERVED_FIRST_SEEN_LENGTH = 64

# Max length for the preserved ``_identity`` field. The canonical identity
# is ``<prefix>|<iso>`` where prefix is ~30 chars and iso is ~25 chars; 256
# is ~4x headroom for any future prefix expansion.
_MAX_PRESERVED_IDENTITY_LENGTH = 256


def _is_valid_preserved_first_seen(value: object) -> TypeGuard[str]:
    """Return ``True`` when *value* is a safe preserved ``first_seen`` string.

    Validates: (a) ``isinstance(value, str)`` (TypeGuard narrows for mypy),
    (b) non-empty after strip, (c) length ``<=
    _MAX_PRESERVED_FIRST_SEEN_LENGTH``, (d) no XML 1.0 control characters,
    (e) parseable via :func:`datetime.fromisoformat`.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or len(stripped) > _MAX_PRESERVED_FIRST_SEEN_LENGTH:
        return False
    if _PRESERVED_CONTROL_CHAR_RE.search(stripped):
        return False
    try:
        datetime.fromisoformat(stripped)
    except (ValueError, TypeError):
        return False
    return True


def _is_valid_preserved_identity(value: object) -> TypeGuard[str]:
    """Return ``True`` when *value* is a safe preserved ``_identity`` string."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or len(stripped) > _MAX_PRESERVED_IDENTITY_LENGTH:
        return False
    if _PRESERVED_CONTROL_CHAR_RE.search(stripped):
        return False
    return True


def _read_existing_first_seen() -> dict[str, str]:
    """Map ``identity_prefix`` → ``first_seen`` (ISO) from the existing cache.

    See the module docstring for the threat model and defences. All
    failure modes (missing file, oversized file, malformed JSON,
    unexpected shape, missing/typed-wrong / oversized / non-ISO fields)
    collapse to an empty map.
    """

    if not OUTPUT_PATH.exists():
        return {}
    payload = read_capped_json(
        OUTPUT_PATH,
        max_bytes=MAX_STAMMSTRECKE_CACHE_BYTES,
        label="Stammstrecke",
        logger=LOGGER,
    )
    if not isinstance(payload, list):
        return {}

    result: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        identity = item.get("_identity")
        first_seen = item.get("first_seen")
        if not _is_valid_preserved_identity(identity):
            continue
        if not _is_valid_preserved_first_seen(first_seen):
            continue
        prefix = identity.split("|", 1)[0]
        if prefix:
            result[prefix] = first_seen
    return result


def _resolve_first_seen(
    prefix: str,
    previous_first_seen: dict[str, str],
    now: datetime,
) -> datetime:
    """Pick ``first_seen`` for *prefix*: prior value if present, else *now*.

    Parses the prior ISO 8601 string back into a tz-aware
    :class:`datetime`; on any parse failure falls back to *now* so a
    corrupt prior cache cannot poison the new event. A naive parsed
    timestamp is force-localised to ``Europe/Vienna`` to match the
    project's timezone contract.
    """

    prev_iso = previous_first_seen.get(prefix)
    if prev_iso:
        try:
            parsed = datetime.fromisoformat(prev_iso)
        except (ValueError, TypeError):
            LOGGER.warning(
                "Stammstrecke: konnte first_seen %r nicht parsen — "
                "verwende aktuellen Zeitpunkt für %s.",
                sanitize_log_arg(prev_iso),
                prefix,
            )
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=VIENNA_TZ)
            return parsed
    return now


def _build_event(
    *,
    direction: _Direction,
    median_delay_minutes: float,
    pub_date: datetime,
    first_seen: datetime,
) -> dict[str, Any]:
    """Construct a schema-compliant event dictionary for *direction*."""

    date_str = first_seen.strftime("%d.%m.%Y")
    description = (
        f"Durchschnittliche Verspätung von "
        f"{_format_minutes(median_delay_minutes)} Minuten "
        f"in Richtung {direction.target_label} "
        f"[Seit {date_str}]"
    )

    iso_pub = pub_date.isoformat()
    iso_first_seen = first_seen.isoformat()

    identity = f"{direction.identity_prefix}|{iso_first_seen}"
    guid = make_guid(direction.identity_prefix, iso_first_seen)

    return {
        "source": EVENT_SOURCE,
        "category": EVENT_CATEGORY,
        "title": EVENT_TITLE,
        "description": description,
        "link": EVENT_LINK,
        "guid": guid,
        "pubDate": iso_pub,
        "starts_at": iso_first_seen,
        "ends_at": None,
        "first_seen": iso_first_seen,
        "_identity": identity,
    }


def _write_cache(payload: list[dict[str, Any]]) -> None:
    """Atomically write *payload* to :data:`OUTPUT_PATH` as pretty JSON."""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(OUTPUT_PATH, mode="w", encoding="utf-8", permissions=0o644) as fh:
        _json_lib.dump(
            payload,
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        fh.write("\n")


# Hard cap on the ``errorCode`` string length. Real VAO codes are
# ~4-8 chars (``H890``, ``H892``, ``H730``, ``SVC_LOC_INVALID``); the
# 64-char ceiling is generous headroom while preventing a planted
# upstream from poisoning a structured log record with a multi-KiB
# blob via the otherwise-trusted error-body extraction path.
_ERROR_CODE_MAX_LEN: Final = 64

# Hard cap on the JSON body we will load into memory before scanning
# for ``errorCode``. The legitimate VAO error envelope is <1 KiB; 16
# KiB is ~16x headroom for any plausible future evolution while still
# preventing a planted upstream from amplifying the diagnostic-logging
# branch into a memory-pressure DoS. ``response.content`` would
# otherwise materialise the entire (potentially huge) error body.
_ERROR_BODY_MAX_BYTES: Final = 16 * 1024

# Strict character whitelist for the rendered ``errorCode``. Real VAO
# codes are ``[A-Za-z][A-Za-z0-9_]*`` shape (``H890``, ``H892``,
# ``H730``, ``SVC_LOC_INVALID``, ``API_GEN``…). The 2026-05-09 cron
# run revealed that the upstream sometimes echoes the supplied
# ``accessId`` into the ``errorCode`` field on bad-request responses
# (``HTTP 400`` with the accessId verbatim in the body — GitHub
# Actions then masked the log line as ``errorCode=***``, defeating
# the purpose of the diagnostic). The strict regex bails to
# ``"<malformed>"`` whenever the upstream value is not a canonical
# short code, so the diagnostic CANNOT inadvertently surface
# user-supplied or upstream-echoed secrets even when GitHub Actions'
# secret-masker is unavailable.
_VAO_ERROR_CODE_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

# Hard cap on the rendered body-keys diagnostic. Real VAO error
# envelopes carry ~3-5 top-level keys (``errorCode``, ``errorText``,
# ``Message``, ``serverVersion``, ``planRtTs``); 256 chars is generous
# headroom while bounding the worst case where a planted upstream
# returns thousands of single-letter top-level keys.
_BODY_KEYS_MAX_LEN: Final = 256


# Sentinel strings for the diagnostic logging branch.
#
# Why brackets-and-uppercase-words:
# The 2026-05-09 cron run revealed that the project's ``SafeFormatter``
# (or one of its delegated maskers) treats lowercase alphanumeric
# tokens like ``unknown`` as "potential secret"-shaped and replaces
# them with ``***``. The original ``<unknown>`` sentinel therefore
# became ``***`` in the live log line, defeating the purpose of the
# diagnostic. Switching to ``[BRACKET_TAG]``-shape tokens (uppercase,
# underscores, hard delimiters) keeps the sentinels safely outside
# any token-shape heuristic the masker applies.
_DIAG_NO_RESPONSE: Final = "[NO_RESPONSE]"
_DIAG_MISSING: Final = "[MISSING]"
_DIAG_BAD_SHAPE: Final = "[BAD_SHAPE]"
_DIAG_EMPTY_BODY: Final = "[EMPTY_BODY]"
_DIAG_NO_KEYS: Final = "[NO_KEYS]"
_DIAG_REDACTED_KEY: Final = "[REDACTED]"


def _extract_http_status(exc: requests.HTTPError) -> str:
    """Return the HTTP status code from *exc* as a stringy diagnostic.

    Falls back to :data:`_DIAG_NO_RESPONSE` when the exception carries
    no response object (network-level failure, redirect-loop break,
    etc.).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return _DIAG_MISSING
    return str(status)


def _extract_response_header(exc: requests.HTTPError, name: str) -> str:
    """Return the value of the *name* header from *exc.response*, or a sentinel.

    The headers we surface (``Content-Type``, ``Content-Length``,
    ``Server``, ``WWW-Authenticate``) are server-set and do NOT carry
    user-controlled content — logging them is leak-free. The value is
    truncated at :data:`_BODY_KEYS_MAX_LEN` to bound a planted-huge-
    header poisoning shape (an upstream that returns a multi-KiB
    ``Server`` header would otherwise pollute the structured log
    record).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    headers = getattr(response, "headers", None)
    if headers is None:
        return _DIAG_MISSING
    value = headers.get(name)
    if not isinstance(value, str):
        return _DIAG_MISSING
    stripped = value.strip()
    if not stripped:
        return _DIAG_MISSING
    if len(stripped) > _BODY_KEYS_MAX_LEN:
        return stripped[:_BODY_KEYS_MAX_LEN] + "…"
    return stripped


def _decode_error_body(exc: requests.HTTPError) -> Mapping[str, Any] | None:
    """Decode the response body as a top-level JSON mapping, or return ``None``.

    Bounded by :data:`_ERROR_BODY_MAX_BYTES` to prevent a planted
    upstream from amplifying the diagnostic-logging branch into a
    memory-pressure DoS.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = getattr(response, "content", None)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) == 0 or len(raw) > _ERROR_BODY_MAX_BYTES:
        return None
    try:
        body = _json_lib.loads(raw)
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None
    if not isinstance(body, Mapping):
        return None
    return body


def _extract_vao_error_code(exc: requests.HTTPError) -> str:
    """Return the ``errorCode`` from a VAO error-envelope JSON body.

    Strict-match against :data:`_VAO_ERROR_CODE_RE` (canonical short
    HAFAS code shape) — a value that does not match (e.g. a free-form
    error message that happens to echo upstream-controlled content,
    including the supplied ``accessId``) collapses to
    :data:`_DIAG_BAD_SHAPE` rather than risking a secret leak through
    the diagnostic line.

    Falls back to :data:`_DIAG_MISSING` for every other failure mode
    (no response, body too large, body not JSON, body not a mapping,
    errorCode field absent or non-stringy).
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_MISSING
    raw_code = body.get("errorCode") or body.get("error")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    code = raw_code.strip()
    if not code:
        return _DIAG_MISSING
    if not _VAO_ERROR_CODE_RE.match(code):
        # Defensive bail: the upstream value is NOT a canonical short
        # code — most plausibly a verbose error message whose contents
        # cannot be safely surfaced. The body-keys diagnostic from
        # :func:`_describe_error_body_keys` will still expose enough
        # structural shape information to triage the failure.
        return _DIAG_BAD_SHAPE
    return code[:_ERROR_CODE_MAX_LEN]


def _describe_error_body_keys(exc: requests.HTTPError) -> str:
    """Return a comma-separated list of top-level keys from the JSON body.

    The keys are alphabetised so the diagnostic is stable across
    runs, capped at :data:`_BODY_KEYS_MAX_LEN` chars to bound a
    planted-huge-key-set body, and filtered through
    :func:`_VAO_ERROR_CODE_RE` so a top-level key whose name itself
    smuggles upstream-controlled content (an unusual but possible
    shape) does not slip into the log line. Keys that do NOT match
    the canonical HAFAS field-name pattern are rendered as
    :data:`_DIAG_REDACTED_KEY` so the diagnostic still indicates
    "the body has N fields" without exposing what they are.

    Falls back to :data:`_DIAG_EMPTY_BODY` for non-JSON / non-mapping
    / oversize payloads, mirroring :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    keys: list[str] = []
    # ``Mapping[str, Any]`` from ``_decode_error_body`` guarantees the
    # iteration yields ``str`` keys; we only need to gate against the
    # canonical-shape regex.
    for key in body.keys():
        stripped = key.strip()
        if _VAO_ERROR_CODE_RE.match(stripped):
            keys.append(stripped)
        else:
            keys.append(_DIAG_REDACTED_KEY)
    keys.sort()
    rendered = ",".join(keys) or _DIAG_NO_KEYS
    if len(rendered) > _BODY_KEYS_MAX_LEN:
        return rendered[:_BODY_KEYS_MAX_LEN] + "…"
    return rendered


def _process_direction(
    session: requests.Session,
    direction: _Direction,
    *,
    when: datetime,
    previous_first_seen: dict[str, str],
) -> tuple[dict[str, Any] | None, str]:
    """Query ``direction`` once and return ``(event_or_none, status)``.

    The return tuple's second element is one of:

    * ``"event"`` — direction exceeded threshold, event was built;
    * ``"no_delays"`` — direction succeeded but emitted no S-Bahn legs
      with delay data, or median ≤ threshold;
    * ``"error"`` — VAO/parse raised an exception (already logged);
    * ``"quota_exceeded"`` — the daily quota cap hit before the call;
      caller treats the same as ``"error"`` for self-healing purposes.

    The ``CircuitBreakerOpen`` case is *not* handled here — the caller
    catches it so it can break out of the per-direction loop instead
    of consuming further breaker-protected slots.
    """

    LOGGER.info(
        "Stammstrecke: Abfrage Richtung %s (%s → %s) um %s.",
        direction.target_label,
        direction.origin_id,
        direction.destination_id,
        when.isoformat(),
    )
    try:
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
        return None, "quota_exceeded"
    except requests.HTTPError as exc:
        # Diagnostic-rich branch for non-2xx responses. Logs the HTTP
        # status code, the canonical-short VAO ``errorCode`` from the
        # JSON body when present, the body's top-level key set, and
        # four server-set response headers (Content-Type,
        # Content-Length, Server, WWW-Authenticate) so the next cron
        # run reveals whether we are dealing with an auth rejection
        # (401 + WWW-Authenticate set), a gateway/proxy issue (Server
        # naming a CDN rather than the VAO origin), or a payload
        # mismatch (Content-Type=text/html points at an HTML error
        # page). All fields are safe to surface (no URL → no
        # ``accessId`` leak; ``errorCode`` is regex-validated against
        # the canonical HAFAS short-code shape; key names go through
        # the same regex filter; response headers are server-set and
        # do not echo user-controlled content).
        status_code = _extract_http_status(exc)
        error_code = _extract_vao_error_code(exc)
        body_keys = _describe_error_body_keys(exc)
        content_type = _extract_response_header(exc, "Content-Type")
        content_length = _extract_response_header(exc, "Content-Length")
        server = _extract_response_header(exc, "Server")
        www_auth = _extract_response_header(exc, "WWW-Authenticate")
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: HTTP %s "
            "(errorCode=%s, body_keys=%s, ct=%s, cl=%s, server=%s, "
            "www_auth=%s).",
            direction.target_label,
            status_code,
            sanitize_log_arg(error_code),
            sanitize_log_arg(body_keys),
            sanitize_log_arg(content_type),
            sanitize_log_arg(content_length),
            sanitize_log_arg(server),
            sanitize_log_arg(www_auth),
        )
        return None, "error"
    except Exception as exc:
        # Security: never log the full exception via ``%s`` / ``exc_info``
        # — ``VorAuth`` injected the ``accessId`` into the prepared
        # request URL, and ``RequestException`` may carry that URL in
        # its message. Logging the class name only suppresses the leak
        # while preserving the failure-mode diagnostic.
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: %s.",
            direction.target_label,
            type(exc).__name__,
        )
        return None, "error"

    delays = _collect_sbahn_delays_minutes(trips)
    LOGGER.info(
        "Stammstrecke: Richtung %s — %d S-Bahn-Legs aus %d Trips analysiert.",
        direction.target_label,
        len(delays),
        len(trips),
    )
    if not delays:
        return None, "no_delays"

    median_minutes = float(statistics.median(delays))
    LOGGER.info(
        "Stammstrecke: Richtung %s — Median: %.2f Minuten (Schwelle: %d).",
        direction.target_label,
        median_minutes,
        DELAY_THRESHOLD_MINUTES,
    )
    # Stats: persist every successful median observation, regardless of
    # whether it exceeds the feed-trigger threshold. The dashboard's
    # value comes from the *full* distribution, not just the events that
    # made it onto the RSS feed.
    append_stammstrecke_row(
        timestamp=when,
        direction=direction.target_label,
        delay_minutes=median_minutes,
    )

    if median_minutes <= DELAY_THRESHOLD_MINUTES:
        return None, "no_delays"

    first_seen = _resolve_first_seen(
        direction.identity_prefix, previous_first_seen, when
    )
    event = _build_event(
        direction=direction,
        median_delay_minutes=median_minutes,
        pub_date=when,
        first_seen=first_seen,
    )

    is_new = first_seen >= when  # tolerant equality for fresh episodes
    LOGGER.info(
        "Stammstrecke: Richtung %s — Median %.2f > %d → Event %s "
        "(guid=%s, first_seen=%s).",
        direction.target_label,
        median_minutes,
        DELAY_THRESHOLD_MINUTES,
        "neu" if is_new else "fortgeführt",
        event["guid"][:12],
        event["first_seen"],
    )
    return event, "event"


def main() -> int:
    """Entry point. Returns ``0`` on success (incl. partial), ``1`` on full failure.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded.

    Self-healing rule: the cache file is *unconditionally* set to ``[]``
    when (a) every monitored direction fails before producing an
    observation, OR (b) no direction's median exceeds the threshold.
    Per-direction error isolation still applies: a single direction's
    transient failure does not discard a successfully observed
    disruption from the other direction.
    """

    configure_logging()

    when = _now_vienna()
    previous_first_seen = _read_existing_first_seen()
    events: list[dict[str, Any]] = []
    successes = 0
    errors = 0
    breaker_short_circuited = False

    with ExitStack() as stack:
        try:
            session = _build_session(stack)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.error(
                "Stammstrecke: VOR-Session konnte nicht erstellt werden: %s.",
                type(exc).__name__,
            )
            _write_cache([])
            return 1

        for direction in DIRECTIONS:
            try:
                event, status = _process_direction(
                    session,
                    direction,
                    when=when,
                    previous_first_seen=previous_first_seen,
                )
            except CircuitBreakerOpen:
                LOGGER.warning(
                    "Stammstrecke: Circuit breaker offen (%d aufeinanderfolgende Fehler) — "
                    "leere Cache-Datei und überspringe verbleibende Richtungen.",
                    _BREAKER.consecutive_failures,
                )
                breaker_short_circuited = True
                events = []  # Self-Healing: discard any partial results.
                break

            if status in ("error", "quota_exceeded"):
                errors += 1
                continue
            successes += 1
            if event is not None:
                events.append(event)

    # Self-Healing rule: if every direction errored AND none succeeded,
    # treat the API as unreachable globally and clear the cache.
    if not breaker_short_circuited and successes == 0 and errors > 0:
        events = []

    _write_cache(events)
    LOGGER.info(
        "Stammstrecke: Cache mit %d Event(s) aktualisiert (Erfolg=%d, Fehler=%d).",
        len(events),
        successes,
        errors,
    )

    # Exit 1 only if every direction failed AND at least one was attempted —
    # a CircuitBreakerOpen-only run (errors=0, successes=0) is intentional
    # short-circuiting and exits 0.
    if successes == 0 and errors > 0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
