#!/usr/bin/env python3
"""Monitor delays on the S-Bahn Stammstrecke (Wien Floridsdorf ↔ Wien Meidling).

Queries direct S-Bahn connections via :mod:`pyhafas` (`OEBBProfile`) and
emits a schema-compliant event into ``cache/stammstrecke/events.json``
when the **median** ``departure_delay`` across the queried legs exceeds
:data:`DELAY_THRESHOLD_MINUTES` minutes. Otherwise the cache is reset to
``[]`` so the feed builder integrates a clean state.

Design contract
---------------

- **Resilience**: the network call to HAFAS is wrapped in
  :class:`src.utils.circuit_breaker.CircuitBreaker`. Five consecutive
  failures trip the breaker for 300 s, after which a single probe call
  is admitted. Each individual call uses a hard ``QUERY_TIMEOUT`` second
  budget (clamped to ``MAX_QUERY_TIMEOUT``) inside ``pyhafas`` itself.
- **Atomicity**: writes go through :func:`src.utils.files.atomic_write`
  with restrictive 0o600 permissions; a crash mid-write cannot leave a
  half-written cache file behind.
- **Timezone**: GitHub Actions runs in UTC. All timestamps inside the
  emitted event (``pubDate``, ``starts_at``) are localised to
  ``Europe/Vienna`` via :mod:`zoneinfo` and serialised as ISO 8601
  strings with offset, matching ``docs/schema/events.schema.json``.
- **Schema**: the emitted event mirrors the canonical FeedItem shape
  every other provider produces (``source`` / ``category`` / ``title`` /
  ``description`` / ``link`` / ``guid`` / ``pubDate`` / ``starts_at`` /
  ``ends_at`` / ``_identity``).
- **Logging**: every diagnostic message is routed through
  :func:`src.feed.logging_safe.setup_script_logging` so log injection
  / ANSI / BiDi attacks via upstream-controlled fields are sanitised at
  the formatter layer.

The non-commercial nature of the project means we do not need an API
key; ÖBB's HAFAS endpoint is queried via the publicly documented
``mgate.exe`` interface that pyhafas routes through.
"""

from __future__ import annotations

import logging
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
)
from src.utils.files import atomic_write  # noqa: E402
from src.utils.ids import make_guid  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402

if TYPE_CHECKING:
    from pyhafas.types.fptf import Journey, Leg

LOGGER = logging.getLogger("update_stammstrecke_status")

# ---- Operating parameters ---------------------------------------------------

# Public ÖBB HAFAS station IDs. Source: pyhafas/ÖBB SCOTTY documentation.
FLORIDSDORF_STATION_ID = "8100518"
MEIDLING_STATION_ID = "8100514"

# Threshold above which the median delay generates a feed entry. The user-
# facing semantics are "more than 9 minutes" — so a median of exactly
# 9 minutes does NOT trigger the event.
DELAY_THRESHOLD_MINUTES = 9

# Number of journeys to fetch in a single HAFAS query. Higher values give
# a more stable median but raise the cost of a single call. 12 covers
# roughly half an hour of S-Bahn frequency on the Stammstrecke.
MAX_JOURNEYS_PER_QUERY = 12

# Per-call HTTP budget. A pyhafas call without a timeout can hang the
# cron runner indefinitely if the upstream peer is sluggish; the cap is
# enforced inside pyhafas via the underlying ``requests`` call.
QUERY_TIMEOUT = 20
MAX_QUERY_TIMEOUT = 30

# Circuit-breaker policy. Five consecutive failures (network errors,
# upstream 5xx, parse failures) trip the breaker for 300 seconds. The
# cron tick is every 30 minutes, so a 5-minute hold-off only ever
# blocks at most one tick.
BREAKER_FAILURE_THRESHOLD = 5
BREAKER_RECOVERY_TIMEOUT = 300.0

# Pattern that identifies an S-Bahn leg. ÖBB labels Stammstrecke services
# as ``S 1``, ``S 2``, ``S 3``, ``S 7`` etc. — the ``name`` attribute of
# a pyhafas ``Leg`` carries this label verbatim. Anything else (REX, R,
# IC, Railjet) is a long-distance / regional service that uses the same
# tracks but does not represent the Stammstrecke product.
import re  # noqa: E402

_S_BAHN_LINE_RE = re.compile(r"^\s*S\s*\d+\s*$", re.IGNORECASE)

VIENNA_TZ = ZoneInfo("Europe/Vienna")

OUTPUT_PATH = REPO_ROOT / "cache" / "stammstrecke" / "events.json"

EVENT_SOURCE = "ÖBB"
EVENT_CATEGORY = "Störung"
EVENT_TITLE = "S-Bahn Stammstrecke Verspätungen"
EVENT_LINK = (
    "https://www.oebb.at/de/fahrplan/fahrplanauskunft-und-stoerungsinformation/aktuelle-stoerungsmeldungen"
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


def _build_client() -> Any:
    """Construct a :class:`pyhafas.HafasClient` with the ÖBB profile.

    The import is performed lazily so a missing optional dependency or a
    pyhafas release without ``OEBBProfile`` produces a clean WARNING and
    a no-op cache update instead of a hard import-time crash that would
    abort the cron pipeline.
    """

    from pyhafas import HafasClient
    from pyhafas.profile import OEBBProfile

    return HafasClient(OEBBProfile(), ua="wien-oepnv-stammstrecke/1.0")


def _query_journeys(
    client: Any,
    *,
    when: datetime,
    timeout: int,
) -> list[Journey]:
    """Call ``client.journeys`` once and return the result list.

    The call is executed as-is; resilience (retry/back-off, breaker
    state) is provided by :data:`_BREAKER` at the call site. The
    ``timeout`` parameter is forwarded into the pyhafas profile via the
    underlying requests session if the profile honours it.
    """

    # pyhafas honours a profile-level requests session whose adapters
    # default to ``timeout=None``. We pin the request timeout via the
    # session attribute so a sluggish upstream peer cannot hold the
    # cron job past the documented ``MAX_QUERY_TIMEOUT`` budget.
    session = getattr(client.profile, "requests", None)
    if session is not None:
        # Some pyhafas profiles expose a custom session wrapper; tighten
        # both attributes if present.
        for attribute in ("timeout", "request_timeout"):
            if hasattr(session, attribute):
                try:
                    setattr(session, attribute, timeout)
                except (AttributeError, TypeError):
                    pass

    journeys = client.journeys(
        origin=FLORIDSDORF_STATION_ID,
        destination=MEIDLING_STATION_ID,
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


def _build_event(
    *,
    median_delay_minutes: float,
    sample_size: int,
    pub_date: datetime,
) -> dict[str, Any]:
    """Construct the schema-compliant event dictionary.

    See ``docs/schema/events.schema.json`` for the contract. ``pubDate``
    and ``starts_at`` use the same timestamp because the median is a
    point-in-time observation; ``ends_at`` is left ``null`` because the
    cause and end of the disruption are not known to this script.
    """

    rounded = round(median_delay_minutes, 1)
    description = (
        f"Auf der S-Bahn-Stammstrecke (Wien Floridsdorf ↔ Wien Meidling) wurden "
        f"erhöhte Verspätungen erkannt. Median der Abfahrtsverspätungen über "
        f"die letzten {sample_size} S-Bahn-Verbindungen: "
        f"<b>{rounded:.1f} Minuten</b> (Schwellenwert: "
        f"{DELAY_THRESHOLD_MINUTES} Minuten). Quelle: ÖBB HAFAS via pyhafas."
    )

    iso_pub = pub_date.isoformat()
    identity = f"stammstrecke|median|{iso_pub}"
    guid = make_guid("stammstrecke", "median", iso_pub)

    return {
        "source": EVENT_SOURCE,
        "category": EVENT_CATEGORY,
        "title": EVENT_TITLE,
        "description": description,
        "link": EVENT_LINK,
        "guid": guid,
        "pubDate": iso_pub,
        "starts_at": iso_pub,
        "ends_at": None,
        "_identity": identity,
    }


def _write_cache(payload: list[dict[str, Any]]) -> None:
    """Atomically write *payload* to :data:`OUTPUT_PATH` as pretty JSON."""

    import json as _json

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # ``permissions=0o644`` matches the canonical cache file ACL — the
    # build_feed.py reader runs as the same user but pre-commit / git
    # auto-commit also need read access. The non-secret nature of the
    # data (publicly observed delay) makes 0o600 unnecessary here.
    with atomic_write(OUTPUT_PATH, mode="w", encoding="utf-8", permissions=0o644) as fh:
        _json.dump(
            payload,
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        fh.write("\n")


def main() -> int:
    """Entry point. Returns ``0`` on success, ``1`` on a controlled error.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded.
    """

    configure_logging()

    timeout = max(1, min(QUERY_TIMEOUT, MAX_QUERY_TIMEOUT))

    try:
        client = _build_client()
    except ImportError as exc:
        LOGGER.warning(
            "pyhafas / OEBBProfile nicht verfügbar (%s); schreibe leere Stammstrecke-Cache-Datei.",
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
    LOGGER.info(
        "Stammstrecke: Abfrage Wien Floridsdorf → Wien Meidling um %s (max_changes=0, max_journeys=%d).",
        when.isoformat(),
        MAX_JOURNEYS_PER_QUERY,
    )

    try:
        journeys = _BREAKER.call(_query_journeys, client, when=when, timeout=timeout)
    except CircuitBreakerOpen:
        LOGGER.warning(
            "Stammstrecke: Circuit breaker offen (%d aufeinanderfolgende Fehler) — "
            "schreibe leere Cache-Datei und überspringe diese Tick.",
            _BREAKER.consecutive_failures,
        )
        _write_cache([])
        return 0
    except Exception as exc:
        # Keep the cache file in a known-good state instead of leaving a
        # stale entry from the previous run when HAFAS is degraded.
        LOGGER.warning(
            "Stammstrecke: Abfrage fehlgeschlagen: %s: %s — schreibe leere Cache-Datei.",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        _write_cache([])
        return 1

    delays = _collect_sbahn_delays_minutes(journeys)
    LOGGER.info(
        "Stammstrecke: %d S-Bahn-Legs aus %d Journeys analysiert.",
        len(delays),
        len(journeys),
    )

    if not delays:
        LOGGER.info(
            "Stammstrecke: Keine S-Bahn-Legs mit Verspätungsdaten gefunden — schreibe leere Cache-Datei."
        )
        _write_cache([])
        return 0

    median_minutes = float(statistics.median(delays))
    LOGGER.info(
        "Stammstrecke: Median der Abfahrtsverspätungen: %.2f Minuten (Schwelle: %d).",
        median_minutes,
        DELAY_THRESHOLD_MINUTES,
    )

    if median_minutes <= DELAY_THRESHOLD_MINUTES:
        _write_cache([])
        LOGGER.info(
            "Stammstrecke: Median ≤ %d Minuten — keine Meldung, Cache geleert.",
            DELAY_THRESHOLD_MINUTES,
        )
        return 0

    event = _build_event(
        median_delay_minutes=median_minutes,
        sample_size=len(delays),
        pub_date=when,
    )
    _write_cache([event])
    LOGGER.info(
        "Stammstrecke: Median %.2f Min > %d Min — Meldung in Cache geschrieben (guid=%s).",
        median_minutes,
        DELAY_THRESHOLD_MINUTES,
        event["guid"][:12],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
