#!/usr/bin/env python3

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.

- Secret OEBB_RSS_URL (Fallback: offizielle ÖBB-RSS-URL)
- Titel-Kosmetik:
  • Kategorie-Vorspann (bis Doppelpunkt) entfernen
  • „Wien X und Wien Y“ → „Wien X ↔ Wien Y“
  • Pfeile/Bindestriche normalisieren (ein „↔“), Bahnhof/Hbf/Bf entfernen
  • Spitze Klammern etc. entfernen
- Plain-Text-Description (HTML/Word raus, Entities decodiert; Trenner „ • “)
- Strenger GEO-Filter: Behalte NUR Meldungen, deren Endpunkte in Wien
  oder definierter Pendler-Region (Whitelist) liegen
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from ..feed_types import FeedItem
from ..utils.ids import make_guid
from ..utils.stations import (
    display_name,
    railway_station_names,
    station_by_oebb_id,
    station_info,
)
from ..utils.http import (
    fetch_content_safe,
    parse_retry_after,
    session_with_retries,
    validate_http_url,
)
from ..utils.logging import sanitize_log_arg
from ..utils import raw_capture, source_shape
from ..utils.vienna_clock import plausible_moment, vienna_wall_clock
from .oebb_title import (
    _UPDATE_PREFIX_RE,
    _clean_description,
    _clean_title_keep_places,
    _strip_update_prefix,
)
from .oebb_routes import (
    _extract_routes,
    _extract_zwischen_routes,
    _looks_like_facility_endpoint,
    _looks_like_station_name,
    _normalize_endpoint_name,
)
from .oebb_scope import _is_facility_or_weather_only
from .oebb_relevance import (
    _drop_reason,
    _find_stations_in_text,
    _is_relevant,
    _route_is_wien_relevant,
)
from .oebb_route_title import (
    _affected_lines,
    _apply_route_title,
    _extract_line_prefix,
    _format_route_title,
    _lies_on_route,
    _normalize_line_token,
    _try_chain_routes,
)

from defusedxml import ElementTree as ET # XXE Mitigation applied

if TYPE_CHECKING:
    # Annotations only: ``defusedxml.ElementTree`` parses into the standard
    # library's nodes but exports no ``Element`` class (``ET.Element`` worked
    # only because annotations are not evaluated). Parsing stays with
    # defusedxml.
    from xml.etree.ElementTree import Element  # nosec B405

log = logging.getLogger(__name__)

# Licence: the ÖBB notices are not openly licensed; oebb.at's general terms
# require written consent for copying. Michael asked ÖBB for it via the
# data.oebb.at contact on 2026-10-16 and decided: if ÖBB refuses, the ÖBB
# content (this provider and the HAFAS queries) leaves the project, because
# ÖBB owns the data (NOTICE.md, "ÖBB-Inhalte").
_OEBB_DEFAULT_URL = (
    "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"
)

# Security: only accept env overrides that point at the official ÖBB Fahrplan
# host. ``OEBB_URL`` ends up as the per-item ``<link>`` fallback (and the XML
# fetched from it can set per-item links directly), so an env override to an
# attacker host weaponises the public RSS feed as a phishing redirector and
# lets the attacker inject arbitrary feed content. ``validate_http_url()``
# only checks SSRF/DNS-rebinding properties, not host identity.
#
# 2026-05-10 (HTTPS-only Provider URL Drift): the validator additionally
# pins the scheme to ``https``. ``validate_http_url`` accepts both ``http``
# and ``https``; without this pin, an env override such as
# ``OEBB_RSS_URL=http://fahrplan.oebb.at/...`` would be accepted, the
# fetcher would issue a plaintext request, and an MITM (compromised
# network, BGP hijack, hostile public WiFi gateway) could substitute
# arbitrary RSS items that flow verbatim into the public ``docs/feed.xml``
# artefact. Mirrors the canonical ``validate_public_feed_url`` HTTPS-only
# pin (``src/utils/http.py``) and the VOR / WL sibling validators.
_OEBB_TRUSTED_HOSTS = frozenset({"fahrplan.oebb.at"})


def _validated_oebb_url(raw: str) -> str | None:
    # ``check_dns=False``: the host is pinned to a single trusted allow-list
    # value below, so the DNS-rebinding/SSRF resolution adds nothing here. It
    # would, however, run a blocking DNS lookup at module-import time (this is
    # called at import) and — worse — silently DROP a legitimate override on a
    # transient resolver hiccup. The https-scheme + exact-host pins remain.
    safe = validate_http_url(raw, check_dns=False)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — see header above.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _OEBB_TRUSTED_HOSTS:
        return None
    return safe


_OEBB_URL_ENV = os.getenv("OEBB_RSS_URL", "").strip()
_OEBB_URL_OVERRIDE = _validated_oebb_url(_OEBB_URL_ENV) if _OEBB_URL_ENV else None
if _OEBB_URL_ENV and _OEBB_URL_OVERRIDE is None:
    log.warning(
        "OEBB_RSS_URL %r ist kein bekannter ÖBB-Fahrplan-Host; verwende Standard.",
        _OEBB_URL_ENV,
    )
OEBB_URL = _OEBB_URL_OVERRIDE or _OEBB_DEFAULT_URL

# Item link when a notice carries none: the ÖBB-Streckeninformation page,
# not the RSS endpoint above (a reader would open raw XML).
OEBB_INFO_URL = (
    "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"
)

# Max wait time for Retry-After headers to prevent DoS
RETRY_AFTER_MAX_SEC = 60.0

# Security: ``MAX_OEBB_FETCH_TIMEOUT`` is the Slowloris-defence ceiling for the
# public ``fetch_events`` API. The ``timeout`` parameter flows through
# ``_fetch_xml`` -> ``fetch_content_safe`` as both the connect and read budget
# for every ÖBB Fahrplan request. The current call site in ``build_feed.py``
# uses ``effective_timeout`` (already capped at ``feed_config.MAX_PROVIDER_TIMEOUT``)
# and ``scripts/update_oebb_cache.py`` uses the 25-second default, so the
# production blast radius is currently zero. But ``fetch_events`` is exported
# as a public API (``__all__``) and a future caller passing an env-controlled
# or user-controlled value (e.g. a hypothetical ``OEBB_FETCH_TIMEOUT`` env var
# or a CLI flag wired into a maintenance script) would otherwise inherit the
# unbounded shape — ``timeout=99999`` (intentional misconfig, leaked CI env,
# compromised secret store) lets a sluggish or attacker-controlled upstream
# peer hold the worker for ~28 hours per fetch, stalling the cron pipeline.
# Capping inside the function (defense-in-depth) means every caller — current
# and future — inherits the ceiling without having to remember to add it.
# 25 seconds matches ``feed_config.MAX_PROVIDER_TIMEOUT`` (the orchestrator-
# level Slowloris ceiling) so no orchestrator-capped value is ever rejected.
# TIGHTEN-only contract mirrors ``MAX_PRUNE_CACHE_MAX_AGE_HOURS``
# (``src/utils/cache.py``) and ``MAX_LOG_PRUNE_KEEP_DAYS``
# (``src/feed/logging.py``) — same parameter-boundary defense-in-depth pattern
# documented in the 2026-05-07 ``prune_cache`` journal entry, but applied to
# the Slowloris-cap drift family rather than the ``timedelta`` underflow family.
MAX_OEBB_FETCH_TIMEOUT = 25

# ---------------- HTTP ----------------
USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"

# ---------------- Fallback Helpers ----------------
def _extract_id_from_url(url: str) -> int | None:
    """
    Extracts a numeric ID (e.g., station ID) from the end of a URL/GUID.
    Matches ...&123456 or ...?123456.
    """
    if not url:
        return None
    # Looking for &<digits> or ?<digits> at string end or before hash/other param
    # User example: ...&752992
    match = re.search(r"[?&](\d{6,})(?:$|[#&])", url)
    if match:
        return int(match.group(1))
    return None

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> Element | None:
    with session_with_retries(USER_AGENT) as s:
        for attempt in range(2):
            try:
                content = fetch_content_safe(
                    s,
                    url,
                    timeout=timeout,
                    allowed_content_types=(
                        "application/xml",
                        "text/xml",
                        "application/rss+xml",
                    ),
                )
                return ET.fromstring(content)
            except (ValueError, ET.ParseError, RecursionError) as e:
                # Resilience: defusedxml defuses XXE / billion-laughs /
                # quadratic blowup, but a deeply-nested (legitimate-looking)
                # element tree still triggers ``RecursionError`` on parse.
                # Catching it here ensures a malicious or pathological RSS
                # feed cannot crash the entire build.
                log.warning("ÖBB RSS: Content-Limit/Format-Fehler: %s", sanitize_log_arg(e))
                return None
            except requests.RequestException as e:
                log.warning("ÖBB RSS fetch fehlgeschlagen (Versuch %d): %s", attempt + 1, sanitize_log_arg(e))

                wait_seconds = 0.0
                if e.response is not None and e.response.status_code == 429:
                    header = e.response.headers.get("Retry-After")
                    parsed_delay = parse_retry_after(header)
                    # Default to 1.0s if header is missing or unparseable.
                    wait_seconds = parsed_delay if parsed_delay is not None else 1.0
                    # Sentinel: ``header`` is upstream-controlled HTTP header
                    # text; route through ``sanitize_log_arg`` so a hostile
                    # ÖBB peer cannot inject ANSI/BiDi/control characters
                    # into operator log streams via the Retry-After header.
                    log.warning(
                        "ÖBB RSS Rate-Limit (Retry-After: %s)",
                        sanitize_log_arg(str(header)),
                    )

                if attempt == 0:
                    if wait_seconds > 0:
                        if wait_seconds > RETRY_AFTER_MAX_SEC:
                            log.warning("ÖBB RSS Rate-Limit überschreitet Maximum (%.1fs). Überspringe (Fail-Fast).", wait_seconds)
                            break
                        log.warning("ÖBB RSS Rate-Limit erreicht. Warte %.1fs (Retry-After).", wait_seconds)
                        time.sleep(wait_seconds)
                    continue
                raise

    return None

def _get_text(elem: Element | None, tag: str) -> str:
    e = elem.find(tag) if elem is not None else None
    return (e.text or "") if e is not None else ""

def _parse_dt_rfc2822(s: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(s)
        if plausible_moment(dt) is None:
            return None
        return vienna_wall_clock(dt) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def _is_poor_title(t: str) -> bool:
    return not t or not any(c.isalnum() for c in t) or t == "-"


def _derive_guid(raw_guid: str, title: str, link: str) -> str:
    """Generate a stable GUID for an XML <item>. Hashes oversized GUIDs
    (security: 128-byte cap to prevent feed-bloat amplification) and falls
    back to a title+link hash when the upstream omits a GUID entirely.
    """
    if raw_guid and len(raw_guid) > 128:
        # Security: Prevent huge GUIDs from external feed
        return make_guid(raw_guid)
    return raw_guid or make_guid(title, link)


def _resolve_poor_title(title: str, link: str, guid: str, desc: str) -> str:
    """Three-attempt fallback ladder for unhelpful titles:

    1. Look up the station name from an OEBB ID embedded in the link or guid.
    2. Extract station names from the description text.
    3. Truncate the description as a last resort.

    Each attempt only fires if the prior result is still classified as poor
    by ``_is_poor_title``, preserving the original short-circuit behaviour.
    """
    if not _is_poor_title(title):
        return title

    # Attempt 1: ID from Link/GUID
    station_id = _extract_id_from_url(link) or _extract_id_from_url(guid)
    if station_id:
        found_name = station_by_oebb_id(station_id)
        if found_name:
            title = display_name(found_name)

    # Attempt 2: Text extraction (if still poor)
    if _is_poor_title(title):
        stations_found = _find_stations_in_text(desc)
        if len(stations_found) == 1:
            title = display_name(stations_found[0])
        elif len(stations_found) >= 2:
            title = (
                f"{display_name(stations_found[0])} ↔ "
                f"{display_name(stations_found[1])}"
            )

    # Attempt 3: Truncation
    if _is_poor_title(title):
        snippet = desc.strip()
        if len(snippet) > 40:
            snippet = snippet[:40] + "..."
        if snippet:
            title = snippet

    return title


_OEBB_TZ = ZoneInfo("Europe/Vienna")

# ÖBB stellt jeder Beschreibung den Gültigkeitszeitraum voran:
#
#     "03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können …"
#     "01.11.2026<br/><br/>Wegen Bauarbeiten können …"        (ein Tag)
#
# ``build_feed`` hat diesen Präfix bisher nur WEGGEWORFEN
# (``_DATE_RANGE_PREFIX_RE`` / ``_DATE_SINGLE_PREFIX_RE``), also als
# Metadatum erkannt, ohne es zu verwenden. Die Folge war doppelt schlecht:
#
# * ``starts_at`` blieb das VERÖFFENTLICHUNGSDATUM und ``ends_at`` leer.
#   Die Zeitzeile im Feed las sich dann „[Seit 10.09.2026]" für eine
#   Sperre, die erst am 05.12.2026 beginnt — auf einem Info-Display, das
#   genau diese Klammer zeigt, eine falsche Aussage.
# * Drei gleichzeitig laufende Bauvorhaben auf derselben Strecke trugen
#   denselben Titel (``Wien Hauptbahnhof ↔ Gramatneusiedl``) und waren
#   ohne Öffnen der Beschreibung nicht zu unterscheiden.
#
# Beides löst sich, sobald der Zeitraum in ``starts_at``/``ends_at``
# landet: :func:`feed_build.time_line.format_local_times` rendert daraus von sich
# aus „03.10.2026 – 05.10.2026", „Am 01.11.2026" oder „Ab …".
_PERIOD_PREFIX_RE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.(\d{4})"
    r"(?:\s*-\s*(\d{2})\.(\d{2})\.(\d{4}))?"
    r"\s*(?:<br\s*/?>|\u2022|$)",
    re.IGNORECASE,
)


def _parse_period(desc: str) -> tuple[datetime | None, datetime | None]:
    """Read the validity period ÖBB prepends to every description.

    Returns ``(start, end)`` in Europe/Vienna — start at 00:00, end at
    23:59:59 so an item stays visible for the whole final day rather than
    vanishing at midnight. Returns ``(None, None)`` when the description
    carries no such prefix; the caller then keeps the previous behaviour
    (publication date as start, no end). A side whose year lies outside
    ``PLAUSIBLE_YEARS`` is ``None`` on its own; the other side stays.

    A single date means a one-day disruption: start and end fall on the
    same day, which :func:`feed_build.time_line.format_local_times` renders as
    ``"Am TT.MM."`` (``"Heute"`` on the day itself).
    """
    match = _PERIOD_PREFIX_RE.match(desc or "")
    if not match:
        return None, None
    try:
        d1, m1, y1 = (int(match.group(i)) for i in (1, 2, 3))
        start = datetime(y1, m1, d1, tzinfo=_OEBB_TZ)
        if match.group(4):
            d2, m2, y2 = (int(match.group(i)) for i in (4, 5, 6))
            end_day = datetime(y2, m2, d2, tzinfo=_OEBB_TZ)
        else:
            end_day = start
    except ValueError:
        # Defensive: an upstream typo like "31.02.2026" must not abort the
        # whole fetch — fall back to the previous field semantics.
        return None, None
    if end_day < start:
        return None, None
    # A year at the edge of the calendar counts as not set (PLAUSIBLE_YEARS);
    # the other side of the period stays ("05.12.2026 - 31.12.9999" begins on
    # 05.12. and names no end).
    end = end_day.replace(hour=23, minute=59, second=59)
    return plausible_moment(start), plausible_moment(end)


def _build_item_from_xml(item: Element) -> FeedItem | str:
    """Convert one ``<item>`` XML element into a normalised ``FeedItem``,
    or return the drop reason (``_drop_reason``) when the Wien-relevance
    filter drops it. Pure ETL: no shared state, no I/O.

    The pipeline is:
        clean title → derive guid → clean description → parse pubDate
        → reconstruct route title → apply poor-title fallback
        → drop if not Wien-relevant.
    """
    raw_title = _get_text(item, "title")
    title = _clean_title_keep_places(raw_title)
    link = _get_text(item, "link").strip() or OEBB_INFO_URL
    raw_guid = _get_text(item, "guid").strip()
    desc = _clean_description(_get_text(item, "description"))
    pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))
    period_start, period_end = _parse_period(desc)

    # GUID derivation MUST use the upstream RAW title (and link), not the
    # post-cleanup ``title``. The cleanup output depends on station-alias
    # rules, ``_LEADING_LINE_PREFIX_RE``, etc.; any of those evolving
    # between runs would shift the derived GUID for the same upstream
    # item and surface a duplicate feed entry (the dedup key would
    # mismatch the cache's old entry). The raw upstream signal is the
    # stable anchor.
    guid = _derive_guid(raw_guid, raw_title, link)
    title = _apply_route_title(title, desc)
    title = _resolve_poor_title(title, link, guid, desc)
    # The railway station, not the Wiener Linien stop in front of it
    # ("Wien Bhf. Meidling (WL)" → "Wien Meidling", 2026-11-08).
    title = railway_station_names(title)

    # Region-Filter: Strict — drop messages that don't describe a
    # Wien-relevant connection or station. Run AFTER title fallback so
    # that fallback-derived titles (e.g. resolved via OEBB station ID)
    # contribute to the relevance check.
    reason = _drop_reason(title, desc)
    if reason is not None:
        return reason

    return {
        "source": "ÖBB",
        "category": "Störung",
        "title": title,          # bereits kurz & ohne Bahnhof/Hbf
        "description": desc,     # plain
        "link": link,
        "guid": guid,
        "pubDate": pub,
        "starts_at": period_start or pub,
        "ends_at": period_end,
        "_identity": f"oebb|{guid}",
    }


def _raw_snapshot(channel: Element) -> dict[str, Any]:
    """The RSS items as kept in ``data/raw/oebb/`` (``src/utils/raw_capture.py``).

    Every child element of every ``<item>`` as text, items ordered by
    ``guid``. The channel header (``lastBuildDate`` and friends) is left
    out: it changes on every call and says nothing about the notices.
    """
    items: list[dict[str, str]] = []
    for item in channel.findall("item"):
        record: dict[str, str] = {}
        for child in item:
            tag = child.tag if isinstance(child.tag, str) else str(child.tag)
            text = child.text or ""
            record[tag] = f"{record[tag]}\n{text}" if tag in record else text
        items.append(record)
    return {"items": raw_capture.sorted_records(items, "guid", "link", "title")}


def _has_identity(record: Any) -> bool:
    return source_shape.has("guid")(record) or source_shape.has("link")(record)


_OEBB_REQUIRED = {
    "title": (source_shape.has("title"), source_shape.MIN_RECORDS),
    "description": (source_shape.has("description"), source_shape.MIN_RECORDS),
    "guid/link": (_has_identity, source_shape.MIN_RECORDS),
}


def fetch_events(timeout: int = 25) -> list[FeedItem]:
    # Security: clamp ``timeout`` to ``MAX_OEBB_FETCH_TIMEOUT`` to defeat the
    # Slowloris vector documented at the constant declaration above. Without
    # the cap a caller passing ``timeout=99999`` would let a sluggish or
    # attacker-controlled upstream peer stall the cron for ~28 hours per fetch.
    if timeout > MAX_OEBB_FETCH_TIMEOUT:
        timeout = MAX_OEBB_FETCH_TIMEOUT
    root = _fetch_xml(OEBB_URL, timeout=timeout)

    if root is None:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    snapshot = _raw_snapshot(channel)
    if not snapshot["items"]:
        # The nationwide feed never runs empty; keep the last good snapshot.
        log.warning("ÖBB RSS: Antwort ohne Einträge – Cache bleibt stehen.")
        return []
    missing = source_shape.missing_fields(snapshot["items"], _OEBB_REQUIRED)
    if missing:
        # A renamed or dropped field breaks the Vienna filter instead of the
        # fetch (``src/utils/source_shape.py``): treat the answer like an
        # unreachable feed, so the cache keeps its last good state.
        log.warning(
            "ÖBB RSS: Feld(er) %s fehlen in allen %d Einträgen – Antwort unbrauchbar.",
            ", ".join(missing),
            len(snapshot["items"]),
        )
        return []
    raw_capture.write_snapshot("oebb", "rss", snapshot)
    raw_capture.reset_drops("oebb")
    out: list[FeedItem] = []
    for item in channel.findall("item"):
        feed_item = _build_item_from_xml(item)
        if isinstance(feed_item, str):
            raw_capture.note_drop("oebb", feed_item, _get_text(item, "title"))
        else:
            out.append(feed_item)
    raw_capture.write_drops("oebb")

    log.info("ÖBB: %d Items nach Region/Titel-Kosmetik", len(out))
    return out


# Names other files and tests read through this module; the code lives in
# the sibling modules oebb_title, oebb_routes, oebb_scope, oebb_relevance and
# oebb_route_title (700-line limit). ``OEBB_ONLY_VIENNA`` lives only in
# ``oebb_relevance``; ``station_info`` is looked up in oebb_routes,
# oebb_relevance and oebb_route_title, so patch it there.
__all__ = [
    "MAX_OEBB_FETCH_TIMEOUT",
    "fetch_events",
    "station_info",
    "_UPDATE_PREFIX_RE",
    "_clean_description",
    "_clean_title_keep_places",
    "_strip_update_prefix",
    "_extract_routes",
    "_extract_zwischen_routes",
    "_looks_like_facility_endpoint",
    "_looks_like_station_name",
    "_normalize_endpoint_name",
    "_is_facility_or_weather_only",
    "_drop_reason",
    "_find_stations_in_text",
    "_is_relevant",
    "_route_is_wien_relevant",
    "_affected_lines",
    "_apply_route_title",
    "_extract_line_prefix",
    "_format_route_title",
    "_lies_on_route",
    "_normalize_line_token",
    "_try_chain_routes",
]
