#!/usr/bin/env python3
"""Monitor delays on the S-Bahn Stammstrecke (Wien Floridsdorf ↔ Wien Meidling).

Queries direct S-Bahn connections via :mod:`pyhafas` (`OEBBProfile`) for
**both directions** independently and emits up to two schema-compliant
events into ``cache/stammstrecke/events.json`` — one per direction
whose **median** ``departure_delay`` exceeds
:data:`DELAY_THRESHOLD_MINUTES` minutes. Directions are evaluated
strictly separately because merging both into a single sample dilutes
the signal — a station with a major incident in one direction often
runs normally in the opposite direction.

Design contract
---------------

- **Two-direction split**: each cron tick runs two HAFAS calls — one
  ``Floridsdorf → Meidling`` and one ``Meidling → Floridsdorf``. Each
  call's medians and events are computed independently. The cache
  output contains 0, 1, or 2 events depending on which direction(s)
  exceeded the threshold.
- **Self-Healing on degradation**: if either condition holds the
  events file is *unconditionally* reset to ``[]``:

      * the API is unreachable (any pyhafas exception, ``ImportError``,
        or :class:`CircuitBreakerOpen`);
      * the median for *all* monitored directions is ``≤ 9`` minutes.

  This keeps the RSS feed free of stale warnings — a transient blip or
  recovery becomes invisible to feed readers within at most one cron
  tick (30 minutes).
- **First-seen persistence**: when a direction was *already* over the
  threshold in the previous run, its ``first_seen`` timestamp is
  carried over so the GUID stays stable for the duration of an
  episode. RSS readers therefore see one continuously-updated entry
  per episode rather than a flood of new entries every 30 minutes.
  Only when the cache contained no event for that direction (recovery
  followed by re-entry, or fresh start) does ``first_seen`` advance to
  the current observation time.
- **Resilience**: the network call to HAFAS is wrapped in
  :class:`src.utils.circuit_breaker.CircuitBreaker` (`failure_threshold=10`,
  `recovery_timeout=3600`s — semantically aligning with a documented
  "≤ 10 requests per hour" API budget for ÖBB). The per-call HTTP
  timeout is enforced by :func:`_patch_session_timeout` which
  monkey-patches ``profile.request_session.request`` to inject a
  default ``timeout`` kwarg.
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
- **Station-name resolution**: target station labels in the description
  are resolved through :mod:`src.utils.stations` (``canonical_name`` +
  ``display_name``) instead of being hardcoded.
- **Logging**: every diagnostic message is routed through
  :func:`src.feed.logging_safe.setup_script_logging` so log injection
  / ANSI / BiDi attacks via upstream-controlled fields are sanitised
  at the formatter layer.

The non-commercial nature of the project means we do not need an API
key; ÖBB's HAFAS endpoint is queried via the publicly documented
``mgate.exe`` interface that pyhafas routes through.
"""

from __future__ import annotations

import json as _json_lib
import logging
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeGuard
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.feed.providers import MAX_STAMMSTRECKE_CACHE_BYTES  # noqa: E402
from src.utils.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
)
from src.utils.files import atomic_write, read_capped_json  # noqa: E402
from src.utils.ids import make_guid  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stations import canonical_name, display_name  # noqa: E402

if TYPE_CHECKING:
    from pyhafas.types.fptf import Journey, Leg

LOGGER = logging.getLogger("update_stammstrecke_status")

# ---- Operating parameters ---------------------------------------------------

# Public ÖBB HAFAS station IDs. Source: pyhafas/ÖBB SCOTTY documentation.
FLORIDSDORF_STATION_ID = "8100518"
MEIDLING_STATION_ID = "8100514"

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

# Number of journeys to fetch per direction in a single HAFAS query.
# We pin this to ``5`` so the median is computed from the immediately
# upcoming five S-Bahn departures per direction — that is the smallest
# odd-ish window that still yields a stable median while keeping the
# pyhafas / HAFAS payload minimal (one call per direction × 2
# directions = 10 journey objects per cron tick).
MAX_JOURNEYS_PER_QUERY = 5

# Per-call HTTP budget. Enforced via :func:`_patch_session_timeout` —
# pyhafas does NOT pass a timeout to its ``session.post`` calls, so
# without the patch a hung HAFAS endpoint would hang the cron runner
# indefinitely.
QUERY_TIMEOUT = 20
MAX_QUERY_TIMEOUT = 30

# Circuit-breaker policy aligned with a documented ÖBB API budget of
# 10 requests per hour. After 10 consecutive failures the breaker
# stays OPEN for one hour, capping ÖBB-bound traffic at 10 attempts/h
# in any outage scenario. With the cron schedule (``*/30``, 2 fires/h)
# and 2 directions per fire, normal operation produces only 4 calls/h —
# well below the configured ceiling.
BREAKER_FAILURE_THRESHOLD = 10
BREAKER_RECOVERY_TIMEOUT = 3600.0

# Pattern that identifies an S-Bahn leg. ÖBB labels Stammstrecke services
# as ``S 1``, ``S 2``, ``S 3``, ``S 7`` etc. — the ``name`` attribute of
# a pyhafas ``Leg`` carries this label verbatim. Anything else (REX, R,
# IC, Railjet) is a long-distance / regional service that uses the same
# tracks but does not represent the Stammstrecke product.
_S_BAHN_LINE_RE = re.compile(r"^\s*S\s*\d+\s*$", re.IGNORECASE)

VIENNA_TZ = ZoneInfo("Europe/Vienna")

OUTPUT_PATH = REPO_ROOT / "cache" / "stammstrecke" / "events.json"

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
    (`"in Richtung Meidling"`) implicitly assumes Vienna, so omitting
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

    Carries the per-direction parameters (origin/destination HAFAS IDs,
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
        origin_id=FLORIDSDORF_STATION_ID,
        destination_id=MEIDLING_STATION_ID,
        target_label=_short_target_label(MEIDLING_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_meidling",
    ),
    _Direction(
        origin_id=MEIDLING_STATION_ID,
        destination_id=FLORIDSDORF_STATION_ID,
        target_label=_short_target_label(FLORIDSDORF_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_floridsdorf",
    ),
)


_BREAKER = CircuitBreaker(
    "stammstrecke-hafas",
    failure_threshold=BREAKER_FAILURE_THRESHOLD,
    recovery_timeout=BREAKER_RECOVERY_TIMEOUT,
)


def configure_logging() -> None:
    """Install the project's :class:`SafeFormatter` for this script."""

    setup_script_logging(logging.INFO)
    # urllib3 emits one INFO line per HAFAS request which clutters the
    # workflow log without adding diagnostic value once requests is
    # known to work. Mirrors the existing scripts pattern.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _patch_session_timeout(profile: Any, timeout: float) -> None:
    """Inject a default ``timeout`` kwarg into the profile's HTTP session.

    pyhafas's ``BaseProfile.request`` calls ``self.request_session.post``
    without a timeout argument, so a hung HAFAS endpoint would block the
    cron runner forever. ``requests.Session.timeout`` (as an attribute)
    is *not* honoured by the requests library — only an explicit
    ``timeout`` kwarg on the per-call method is. Wrapping
    :meth:`requests.Session.request` (the lower-level method that
    ``get/post/put/etc.`` all delegate to) lets us inject the default
    without subclassing requests or modifying pyhafas internals.

    Failing silently when the profile lacks ``request_session`` (e.g.
    a future pyhafas refactor renaming the attribute) keeps the script
    resilient — better to run with no timeout enforcement and a clear
    log line than to crash on construction. The dropped enforcement is
    bounded by the GitHub Actions wallclock kill anyway; we are not
    relying on the timeout for correctness, only for liveness.

    Args:
        profile: A pyhafas profile-like object exposing
            ``request_session`` (a :class:`requests.Session`).
        timeout: Default timeout in seconds, applied as the ``timeout``
            kwarg to every session ``request`` call that does not
            already specify one.
    """

    session = getattr(profile, "request_session", None)
    if session is None or not hasattr(session, "request"):
        LOGGER.warning(
            "Stammstrecke: pyhafas-Profil ohne ``request_session`` — "
            "kein Timeout-Enforcement aktiv (Fallback auf GitHub-Actions-"
            "Wallclock)."
        )
        return

    original_request = session.request

    def _request_with_default_timeout(
        method: str, url: str, **kwargs: Any
    ) -> Any:
        kwargs.setdefault("timeout", timeout)
        return original_request(method, url, **kwargs)

    # Monkey-patching the bound method on a single session instance is
    # intentional — the alternative (subclassing requests.Session and
    # replacing it on the profile) would diverge from pyhafas's
    # session lifecycle.
    session.request = _request_with_default_timeout


def _build_client() -> Any:
    """Construct a :class:`pyhafas.HafasClient` with the ÖBB profile.

    The import is performed lazily so a missing optional dependency or a
    pyhafas release without ``OEBBProfile`` produces a clean WARNING and
    a no-op cache update instead of a hard import-time crash that would
    abort the cron pipeline.

    The HTTP timeout is enforced at construction time by
    :func:`_patch_session_timeout` — see that function's docstring for
    why the patch is required and what semantics it gives.
    """

    from pyhafas import HafasClient
    from pyhafas.profile import OEBBProfile

    profile = OEBBProfile()
    timeout = max(1, min(QUERY_TIMEOUT, MAX_QUERY_TIMEOUT))
    _patch_session_timeout(profile, float(timeout))
    return HafasClient(profile, ua="wien-oepnv-stammstrecke/1.0")


def _query_journeys(
    client: Any,
    direction: _Direction,
    *,
    when: datetime,
) -> list[Journey]:
    """Call ``client.journeys`` once for *direction* and return the result.

    The call is executed as-is; resilience (retry/back-off, breaker
    state) is provided by :data:`_BREAKER` at the call site, and the
    HTTP timeout is enforced via the session-level patch installed in
    :func:`_build_client`.
    """

    journeys = client.journeys(
        origin=direction.origin_id,
        destination=direction.destination_id,
        date=when,
        max_changes=0,
        max_journeys=MAX_JOURNEYS_PER_QUERY,
    )
    if not isinstance(journeys, list):
        raise TypeError(
            f"pyhafas returned non-list journeys payload: {type(journeys).__name__}"
        )
    return journeys


def _is_sbahn_leg(leg: Leg) -> bool:
    """Return ``True`` when ``leg.name`` denotes an S-Bahn service."""

    name = getattr(leg, "name", None)
    if not isinstance(name, str):
        return False
    return bool(_S_BAHN_LINE_RE.match(name))


def _collect_sbahn_delays_minutes(journeys: list[Journey]) -> list[float]:
    """Extract S-Bahn ``departure_delay`` values in minutes.

    Cancelled legs and legs without a delay value are excluded — there
    is no signal in either, and including ``0`` for a missing delay
    would deflate the median. The departure_delay attribute is a
    :class:`datetime.timedelta`; we coerce to fractional minutes via
    ``total_seconds()`` to keep the median stable for sub-minute values
    that some HAFAS peers report.
    """

    delays: list[float] = []
    for journey in journeys:
        legs = getattr(journey, "legs", None) or []
        for leg in legs:
            if not _is_sbahn_leg(leg):
                continue
            if getattr(leg, "cancelled", False):
                continue
            raw_delay = getattr(leg, "departure_delay", None)
            if not isinstance(raw_delay, timedelta):
                continue
            delays.append(raw_delay.total_seconds() / 60.0)
    return delays


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

    Defence-in-depth pattern from
    ``.jules/sentinel.md`` (GTFS Stammstrecke Cache Field-Preservation
    Amplification, 2026-05-08): a planted-huge cache file can carry a
    multi-MiB string in the ``first_seen`` field; without per-field shape
    validation the preservation loop perpetuates the corruption forward
    on every cron tick. The size cap on the file as a whole bounds the
    individual field sizes too, but the per-field validator is the second
    layer that explicitly rejects oversized / non-ISO strings before they
    flow into ``_resolve_first_seen`` or any log emission.
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
    """Return ``True`` when *value* is a safe preserved ``_identity`` string.

    Validates: (a) ``isinstance(value, str)`` (TypeGuard narrows for mypy),
    (b) non-empty after strip, (c) length ``<=
    _MAX_PRESERVED_IDENTITY_LENGTH``, (d) no XML 1.0 control characters.

    The shape ``<prefix>|<iso>`` is not pinned here on purpose — only the
    ``prefix`` portion is consumed (via ``identity.split("|", 1)[0]``), so
    a malformed identity that happens to lack the separator still
    contributes the whole string as the prefix. The length and
    control-character defences bound the worst case.
    """
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

    The Stammstrecke cache is the source of truth for "is there a known
    ongoing episode for this direction?". We parse it once at the start
    of every run and use the resulting map to decide whether each
    direction's emitted event should keep its prior ``first_seen``
    timestamp (continuing episode) or get a fresh one (new episode).

    Threat model: a planted-huge ``cache/stammstrecke/events.json``
    (compromised CI runner / partial flush + power loss / corrupted
    previous run / parallel orchestrator process performing an atomic
    state swap mid-read) buffered into memory via bare
    :func:`json.load` allocates O(file_size) bytes plus a multiplier of
    object overhead, exhausts the runner's cgroup memory limit, and
    propagates :class:`MemoryError` (a :class:`BaseException` subclass
    that is NOT caught by ``except (OSError, JSONDecodeError,
    UnicodeDecodeError)``) past the loader to crash the cron pipeline.
    Worse, a crash before :func:`_write_cache` skips the unconditional
    self-heal write, so the corruption persists indefinitely — every
    subsequent cron tick re-tries and re-crashes, permanently disabling
    the monitor.

    Defences (all routed through :func:`src.utils.files.read_capped_json`):
    (i) per-loader byte cap :data:`MAX_STAMMSTRECKE_CACHE_BYTES`
    (~128x the largest legitimate state shape — 50,000x tighter than
    the canonical 50 MiB :data:`DEFAULT_MAX_JSON_FILE_BYTES`); (ii) TOCTOU
    defence via ``os.fstat(handle.fileno())`` on the opened file
    descriptor, immune to symlink swaps between :meth:`Path.stat` and
    :meth:`Path.open`; (iii) special-file safety via
    ``handle.read(max_bytes + 1)`` — a FIFO / ``/dev/zero`` / character
    device with ``st_size == 0`` cannot stream unbounded bytes; (iv)
    :class:`RecursionError` and :class:`MemoryError` no longer escape
    (the former via ``read_capped_json`` 's catch tuple, the latter via
    the size cap that prevents the allocation in the first place); (v)
    per-preserved-field shape validation rejects oversized / non-ISO /
    control-character-bearing ``_identity`` and ``first_seen`` strings
    before they re-enter :func:`_resolve_first_seen` or any
    log-emission path.

    All failure modes (missing file, oversized file, malformed JSON,
    unexpected shape, missing/typed-wrong / oversized / non-ISO fields)
    collapse to an empty map. We log nothing here — the next
    :func:`_write_cache` will overwrite whatever was there, so a
    corrupt prior cache cannot persist.
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
    """Construct a schema-compliant event dictionary for *direction*.

    See ``docs/schema/events.schema.json`` for the contract:

    * ``pubDate`` is the *current* observation timestamp — updates every
      cron tick, signalling freshness to feed readers.
    * ``starts_at`` and ``first_seen`` both carry the *episode-start*
      timestamp — stable across cron ticks while the disruption
      persists.
    * ``guid`` is derived from ``(identity_prefix, iso_first_seen)`` so
      it remains stable for the lifetime of an episode; feed readers
      treat re-published events with the same GUID as updates rather
      than new entries.

    The description follows the spec format::

        Durchschnittliche Verspätung von {X} Minuten in Richtung
        {Zielbahnhof} [Seit DD.MM.YYYY]

    The date inside the brackets is derived from ``first_seen`` (so a
    continuous episode keeps the same "Seit"-date).
    """

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
    # ``permissions=0o644`` matches the canonical cache file ACL — the
    # build_feed.py reader runs as the same user but pre-commit / git
    # auto-commit also need read access. The non-secret nature of the
    # data (publicly observed delay) makes 0o600 unnecessary here.
    with atomic_write(OUTPUT_PATH, mode="w", encoding="utf-8", permissions=0o644) as fh:
        _json_lib.dump(
            payload,
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        fh.write("\n")


def _process_direction(
    client: Any,
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
    * ``"error"`` — pyhafas raised an exception (already logged).

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
        journeys = _BREAKER.call(
            _query_journeys, client, direction, when=when
        )
    except CircuitBreakerOpen:
        # Re-raise so main() can break out of the loop without re-trying
        # the next direction (the breaker would short-circuit it anyway).
        raise
    except Exception as exc:
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: %s: %s.",
            direction.target_label,
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None, "error"

    delays = _collect_sbahn_delays_minutes(journeys)
    LOGGER.info(
        "Stammstrecke: Richtung %s — %d S-Bahn-Legs aus %d Journeys analysiert.",
        direction.target_label,
        len(delays),
        len(journeys),
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
    when the API is unreachable (any pyhafas exception, ``ImportError``,
    or :class:`CircuitBreakerOpen`) **or** when no direction's median
    exceeds the threshold. Per-direction error isolation still applies:
    a single direction's transient failure does not discard a
    successfully observed disruption from the other direction.
    """

    configure_logging()

    try:
        client = _build_client()
    except ImportError as exc:
        LOGGER.warning(
            "pyhafas / OEBBProfile nicht verfügbar (%s); leere Stammstrecke-Cache-Datei.",
            sanitize_log_arg(str(exc)),
        )
        _write_cache([])
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.error(
            "pyhafas-Client konnte nicht initialisiert werden: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        _write_cache([])
        return 1

    when = _now_vienna()
    previous_first_seen = _read_existing_first_seen()
    events: list[dict[str, Any]] = []
    successes = 0
    errors = 0
    breaker_short_circuited = False

    for direction in DIRECTIONS:
        try:
            event, status = _process_direction(
                client,
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

        if status == "error":
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
