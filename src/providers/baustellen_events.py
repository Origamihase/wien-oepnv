"""Turn the GeoJSON features of the Stadt-Wien Baustellen WFS into cache events.

Moved verbatim from ``scripts/update_baustellen_cache.py``; the script
re-exports every name, so ``scripts.update_baustellen_cache.X`` still works.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from collections.abc import Iterable, Sequence

from dateutil import parser as dtparser
from zoneinfo import ZoneInfo

from .baustellen import oepnv_lead
from ..utils import raw_capture
from ..utils.ids import make_guid
from ..utils.serialize import serialize_for_cache

# Item link: the Stadt Wien page listing the current construction sites. The
# former data.gv.at catalogue page answers 404 since the portal moved
# (checked 17.10.2026); the feed also links every Stadt Wien item there.
DEFAULT_INFO_URL = "https://www.wien.gv.at/verkehr/baustellen"
VIENNA_TZ = ZoneInfo("Europe/Vienna")

TITLE_KEYS: tuple[str, ...] = (
    "BEZEICHNUNG",
    "MASSNAHME",
    "MASSNAHME_TEXT",
    "BAUMASSNAHME",
    "TITLE",
    "NAME",
)
STREET_KEYS: tuple[str, ...] = (
    "STRASSE",
    "STRASSENNAME",
    "STRASSEN",
    "STR",
)
FROM_KEYS: tuple[str, ...] = (
    "VON",
    "ABSCHNITT_VON",
    "VON_NR",
    "VON_KM",
)
TO_KEYS: tuple[str, ...] = (
    "BIS",
    "ABSCHNITT_BIS",
    "BIS_NR",
    "BIS_KM",
)
INFO_KEYS: tuple[str, ...] = (
    "PRESSETEXT",
    "HINWEIS",
    "INFO",
    "BESCHREIBUNG",
    "BEMERKUNG",
    "DETAIL",
    "DETAILS",
    "ANMERKUNG",
)
START_KEYS: tuple[str, ...] = (
    "OBJEKT_BEGINN",
    "ANFANGSZEIT",
    "BEGINN",
    "BEGINN_DATUM",
    "BEGINNZEIT",
    "START",
    "START_DATUM",
    "STARTZEIT",
    "DATUM_VON",
    "VON_DATUM",
)
END_KEYS: tuple[str, ...] = (
    "OBJEKT_ENDE",
    "ENDEZEIT",
    "ENDE",
    "END_DATUM",
    "ENDZEIT",
    "ENDE_DATUM",
    "DATUM_BIS",
    "BIS_DATUM",
    # NB: bare "BIS" is intentionally NOT an end-date key. It is a spatial
    # "to" descriptor (sibling of BIS_NR / BIS_KM / ABSCHNITT_BIS in
    # TO_KEYS); the explicit date variants are DATUM_BIS / BIS_DATUM. Since
    # _parse_datetime uses the lenient dateutil parser, a spatial value such
    # as a bare house number ("22") would otherwise be misread as a date
    # (day-of-current-month), planting a bogus ends_at/GUID and risking the
    # item being aged out as already-expired.
)


@dataclass(slots=True)
class ConstructionEvent:
    """Internal representation for cache items."""

    guid: str
    title: str
    description: str
    starts_at: datetime | None
    ends_at: datetime | None
    pub_date: datetime
    source: str = "Stadt Wien – Baustellen"
    category: str = "Baustelle"
    link: str = DEFAULT_INFO_URL
    context: dict[str, Any] | None = None
    location: dict[str, Any] | None = None

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "source": self.source,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "link": self.link,
            "guid": self.guid,
            "pubDate": self.pub_date,
            # ``starts_at`` is a schema-required key (events.schema.json) that
            # every other provider (WL/ÖBB/Stammstrecke) always emits, with a
            # null value when unknown. Emit it unconditionally here too — the
            # former ``if self.starts_at`` guard dropped the key entirely for a
            # Baustelle without a parseable start date, producing a cache item
            # that violates the published schema. ``ends_at`` stays optional
            # (it is NOT in the schema's ``required`` set).
            "starts_at": self.starts_at,
        }
        if self.ends_at:
            item["ends_at"] = self.ends_at
        if self.context:
            item["context"] = self.context
        if self.location:
            item["location"] = self.location
        return item


def _iter_features(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    return _feature_list(payload) or []


def _feature_list(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The features of a WFS answer, or ``None`` when it carries no feature list."""
    if payload.get("type") == "FeatureCollection":
        features: Any = payload.get("features")
    elif "features" in payload:
        features = payload.get("features")
    elif "data" in payload and isinstance(payload["data"], dict):
        features = payload["data"].get("features")
    else:
        features = None
    # Zero Trust: ``payload`` is verified as a dict by ``_load_json_from_content``
    # / ``_load_fallback`` (top-level shape), but the ``features`` value extracted
    # from it is still ``Any`` — a misbehaving / compromised upstream peer (or a
    # tampered local fallback file) could ship a truthy non-list shape such as
    # ``42``, ``True``, ``{"a":"b"}`` or ``"abc"``. The existing ``or []``
    # collapses falsy values but lets truthy non-lists through; the resulting
    # ``for f in features`` then either raises ``TypeError`` (int/bool) — which
    # is not caught by ``_collect_events`` and crashes the whole cache update —
    # or silently iterates dict keys / string characters and emits zero events
    # (looking like an empty upstream). Mirror the ``for entry in payload`` /
    # ``for item in payload`` shape guards landed for the sibling VOR mapping
    # loaders (``_load_vor_mapping`` in ``scripts/enrich_station_aliases.py``,
    # ``scripts/update_wl_stations.py``, ``src/providers/vor.py``) so the
    # documented ``return None`` / ``return []`` fallback runs instead.
    if not isinstance(features, list):
        return None
    return [f for f in features if isinstance(f, dict)]


def _first_match(properties: dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_datetime(value: str | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), tz=VIENNA_TZ)
        except (ValueError, OSError, OverflowError):
            # ``OverflowError`` (NOT a ValueError/OSError subclass) is raised by
            # ``datetime.fromtimestamp`` for an out-of-range epoch — e.g. a
            # garbled WFS ``BEGINN``/``ENDE`` numeric field carrying ``1e20``
            # (``loads_finite`` admits any finite number). Without this catch it
            # propagated through ``_feature_to_event`` → ``_collect_events`` →
            # ``main`` and crashed the whole baustellen cache update. Mirrors the
            # ``OverflowError`` already caught in the string/dateutil branch below.
            return None
    # value is narrowed to str here by the type annotation and prior branches
    candidate = value.strip()
    if not candidate:
        return None
    # Stadt-Wien WFS emits date-only values with a trailing ``Z``
    # (e.g. ``2026-03-22Z``), a shape ``dateutil`` rejects. The value is
    # the UTC date of a Vienna time of day (see ``_UTC_DATE_RE`` below):
    # it is read as Vienna midnight of that calendar day, and
    # ``_first_day`` moves a start taken from such a value to the
    # following day. Reading the ``Z`` as UTC midnight instead shifted
    # the time of day by 1-2 h.
    date_only_z = re.fullmatch(r"(\d{4}-\d{2}-\d{2})Z", candidate)
    if date_only_z:
        try:
            return datetime.strptime(
                date_only_z.group(1), "%Y-%m-%d"
            ).replace(tzinfo=VIENNA_TZ)
        except ValueError:
            return None
    try:
        # Annotated so the result is a ``datetime`` for mypy with and without
        # the dateutil stubs (without them ``parse`` returns ``Any``; CI has
        # them since 2026-11-01, requirements-dev.txt).
        parsed: datetime = dtparser.parse(candidate)
    except (ValueError, OverflowError):
        return None
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=VIENNA_TZ)
    else:
        parsed = parsed.astimezone(VIENNA_TZ)
    return parsed


def _parse_range(properties: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    start = None
    end = None
    for key in START_KEYS:
        start = _parse_datetime(properties.get(key))
        if start:
            break
    for key in END_KEYS:
        end = _parse_datetime(properties.get(key))
        if end:
            break
    if start or end:
        return start, end
    duration_value = properties.get("DAUER") or properties.get("ZEITRAUM")
    if isinstance(duration_value, str) and "/" in duration_value:
        raw_start, raw_end = duration_value.split("/", 1)
        start = _parse_datetime(raw_start)
        end = _parse_datetime(raw_end)
    return start, end


# Der Beginn steht im WFS einen Kalendertag zu früh. Das ``Z`` eines
# Datums wie ``2026-06-22Z`` ist eine echte Zeitzonenangabe: Die Stadt hält
# Beginn und Ende als Wiener Zeitpunkte und gibt davon das UTC-Datum aus.
# Der Beginn (Mitternacht in Wien) fällt dabei auf den Vortag, das Ende
# (Ende des letzten Tages) bleibt auf seinem Datum. Gemessen am 2026-10-06
# an allen 69 Baustellen der Cache-Historie seit Mai, deren Text selbst ein
# Datum nennt: 13 von 13 nennen als ersten Tag der ersten Bauphase den Tag
# NACH ``OBJEKT_BEGINN`` ("Bauphase 1 von 2 (23.06.2026 bis 28.08.2026)" bei
# ``2026-06-22Z``, "seit 28. März 2022" bei ``2022-03-27Z``), 8 von 9 als
# letzten Tag genau ``OBJEKT_ENDE`` ("bis 13.10.2026" bei ``2026-10-13Z``).
# Der Feed zeigte den Beginn deshalb einen Tag zu früh, und eine
# angekündigte Baustelle rückte einen Tag zu früh nach vorne.
_UTC_DATE_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}Z")


def _start_is_utc_date(properties: dict[str, Any]) -> bool:
    """True when the start :func:`_parse_range` takes is an UTC date (``…Z``)."""
    for key in START_KEYS:
        value = properties.get(key)
        if _parse_datetime(value) is not None:
            return isinstance(value, str) and bool(_UTC_DATE_RE.fullmatch(value.strip()))
    return False


def _first_day(start: datetime | None, end: datetime | None, properties: dict[str, Any]) -> datetime | None:
    """The first day of the works, Vienna midnight (see :data:`_UTC_DATE_RE`).

    Never later than the end: a site that would then start after its last
    day keeps the date as delivered (not seen in the data so far).
    """
    if start is None or not _start_is_utc_date(properties):
        return start
    following = start.date() + timedelta(days=1)
    first = datetime(following.year, following.month, following.day, tzinfo=VIENNA_TZ)
    if end is not None and first.date() > end.date():
        return start
    return first


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=VIENNA_TZ)
    return value.astimezone(VIENNA_TZ)


def _build_context(properties: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    district = properties.get("BEZIRK") or properties.get("BZR")
    if district:
        context["district"] = str(district).strip()
    measure = (
        properties.get("BEHINDERUNGSART")
        or properties.get("VERKEHRSMASSNAHME")
        or properties.get("VERKEHRSART")
    )
    if measure:
        context["measure"] = str(measure).strip()
    status = properties.get("STATUS")
    if status:
        context["status"] = str(status).strip()
    return context


# GeoJSON geometries arrive as Point (``[lon, lat]``), LineString
# (``[[lon, lat], ...]``), Polygon (``[[[lon, lat], ...]]``) or their
# Multi* variants. The station-proximity test only needs one
# representative coordinate, so we descend to the first vertex. The depth
# is bounded so a maliciously deep nested array from a compromised
# ``data.wien.gv.at`` upstream cannot drive unbounded recursion.
_MAX_COORD_DEPTH = 8


def _first_lonlat(coordinates: Any, _depth: int = 0) -> tuple[float, float] | None:
    """Return the first ``(lon, lat)`` pair from a GeoJSON coordinate array."""
    if _depth > _MAX_COORD_DEPTH:
        return None
    if not isinstance(coordinates, list | tuple) or not coordinates:
        return None
    first = coordinates[0]
    if isinstance(first, bool):
        return None
    if isinstance(first, int | float):
        # Leaf level: a coordinate needs at least a lon/lat pair.
        if len(coordinates) < 2:
            return None
        try:
            return float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            return None
    return _first_lonlat(first, _depth + 1)


def _build_location(properties: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {}
    address_parts: list[str] = []
    street = _first_match(properties, STREET_KEYS)
    if street:
        address_parts.append(street)
    from_desc = _first_match(properties, FROM_KEYS)
    to_desc = _first_match(properties, TO_KEYS)
    if from_desc and to_desc:
        address_parts.append(f"zwischen {from_desc} und {to_desc}")
    elif from_desc:
        address_parts.append(f"auf Höhe {from_desc}")
    if address_parts:
        location["address"] = ", ".join(address_parts)
    if isinstance(geometry, dict):
        point = _first_lonlat(geometry.get("coordinates"))
        if point is not None:
            lon, lat = point
            # Security (Coordinate finite/range drift, parser-level
            # floor): mirror the canonical scrub pinned at
            # ``src/places/hafas_client.py:_extract_first_location`` and
            # ``src/places/osm_client.py:filter_complete_places``
            # (Round 1485 / Round 1486). A compromised
            # ``data.wien.gv.at`` upstream (or MITM / DNS rebind)
            # planting GeoJSON ``coordinates: [NaN, Infinity]``
            # otherwise lands the poisoned float in
            # ``location["coordinates"]`` — which propagates verbatim
            # through ``write_cache`` into ``cache/baustellen/
            # events.json`` (committed to ``main`` by
            # ``update-cycle.yml``).
            # ``math.isfinite`` is the strict superset of
            # ``not math.isnan`` — it rejects ``NaN`` AND ``±Inf``
            # in one check. The WGS84-range guard (-90 <= lat <= 90,
            # -180 <= lon <= 180) rejects pathological integer-
            # overflow shapes. Pairs failing either guard are
            # silently dropped (the rest of the event's address /
            # metadata is preserved); the writer-side
            # ``allow_nan=False`` pin in
            # ``src/utils/cache.py:write_cache`` is the
            # defence-in-depth second layer.
            if (
                math.isfinite(lat)
                and math.isfinite(lon)
                and -90.0 <= lat <= 90.0
                and -180.0 <= lon <= 180.0
            ):
                location["coordinates"] = {"lat": lat, "lon": lon}
    return location


def _format_description(properties: dict[str, Any], start: datetime | None, end: datetime | None) -> str:
    info = _first_match(properties, INFO_KEYS)
    segments: list[str] = []
    if info:
        # Lead with the public-transport sentence so the ÖPNV impact
        # survives the feed's description truncation.
        segments.append(oepnv_lead(info))
    if start:
        segments.append(f"Beginn: {start.strftime('%d.%m.%Y %H:%M')} Uhr")
    if end:
        segments.append(f"Geplant bis: {end.strftime('%d.%m.%Y %H:%M')} Uhr")
    context = _build_context(properties)
    if context.get("measure"):
        segments.append(f"Maßnahme: {context['measure']}")
    if context.get("district"):
        segments.append(f"Bezirk: {context['district']}")
    return " \n".join(segments) if segments else "Baustelle ohne weitere Angaben"


# Die Stadt Wien kappt ``BEZEICHNUNG`` bei 100 Zeichen. Wir kürzen nicht selbst
# — der Abbruch steht so im WFS und landete bisher wörtlich im deutschen Feed:
#
#     Landstraßer Hauptstraße von … und Apostelgasse bis Sch      (100 Zeichen)
#     Vordere Zollamtsstraße von … bis Unbenannte Verkehrsfl      (100 Zeichen)
#     Kennedybrücke zwischen … auf Seite "Otto Wagner Hofpavillon  (99 Zeichen)
#
# Auf einem Info-Display, das Titel aus Entfernung und ohne Interaktion zeigt
# (s. AGENTS.md, „Priorität der Ausgaben"), sieht das nach einem Fehler von uns
# aus. Wiederherstellen können wir den fehlenden Text nicht; kenntlich machen
# schon.
#
# Erkannt wird an zwei UNABHÄNGIGEN Signalen, nicht an der Länge allein:
#
# 1. ``len >= 100`` — die harte Obergrenze ist erreicht.
# 2. Ein unpaariges ``"`` — ein vollständiger Titel hat keines. Das fängt den
#    99-Zeichen-Fall: Upstream kappt bei 100 und entfernt danach Leerraum, ein
#    gekappter Titel kann also auch bei 99 landen. Über die Länge allein wäre
#    das nicht von einem echten 99-Zeichen-Titel zu unterscheiden — über das
#    offene Anführungszeichen schon.
_UPSTREAM_TITLE_CAP = 100


def _mark_upstream_truncation(title: str) -> str:
    """Make an upstream-truncated title read as shortened, not as broken.

    Adds an ellipsis and drops an unpaired quotation mark. Der Text selbst
    bleibt unangetastet: Ein abgeschnittenes Wort („… bis Sch…") sieht zwar
    unschön aus, ist aber die Wahrheit über das, was die Quelle liefert —
    es wegzukürzen würde Information vernichten, die sich nicht
    zurückholen lässt, und zwar auf Verdacht.

    NICHT für die GUID verwenden: Die leitet sich weiterhin vom ROHTITEL ab
    (s. :func:`_feature_to_event`), sonst verschöbe diese Kosmetik die
    Identität der Baustelle und setzte ihr ``first_seen`` zurück.

    Was sich doch zurückholen lässt, holt die Feed-Seite: ein Wort, das die
    Beschreibung desselben Items eindeutig vervollständigt („… bis Rad…" →
    „… bis Radetzkybrücke"), ergänzt ``_repair_baustellen_title`` in
    ``src/build_feed.py`` beim Bauen des Feeds; der Cache bleibt die
    unveränderte Quelle.
    """
    if not title:
        return title
    has_unpaired_quote = title.count('"') % 2 == 1
    if len(title) < _UPSTREAM_TITLE_CAP and not has_unpaired_quote:
        return title

    repaired = title
    if has_unpaired_quote:
        # Das letzte ``"`` ist im Kappungsfall das öffnende, dessen Partner
        # abgeschnitten wurde. Ein Schließendes zu ERGÄNZEN wäre erfunden.
        cut = repaired.rfind('"')
        repaired = repaired[:cut] + repaired[cut + 1:]
    repaired = repaired.rstrip()
    if repaired.endswith("…"):
        return repaired
    return f"{repaired}…"


def _feature_to_event(feature: dict[str, Any]) -> ConstructionEvent | None:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    if not isinstance(properties, dict):
        return None
    raw_title = _first_match(properties, TITLE_KEYS)
    if not raw_title:
        street = _first_match(properties, STREET_KEYS)
        if street:
            raw_title = f"Baustelle {street}"
        else:
            return None
    start, end = _parse_range(properties)
    start = _normalize_datetime(start)
    end = _normalize_datetime(end)
    # Die GUID bleibt am gelieferten Datum: Sie trägt ``first_seen`` und die
    # Übersetzung, und die Korrektur des Beginns soll keine Baustelle neu
    # erscheinen lassen.
    guid_start = start
    start = _first_day(start, end, properties)
    description = _format_description(properties, start, end)
    location = _build_location(properties, geometry)
    context = _build_context(properties)
    # The Baustellen GUID must stay STABLE for the same physical construction
    # site across WFS refreshes: it keys ``first_seen``, and a changing GUID
    # makes the site look brand-new on every change, resetting first_seen so it
    # perpetually dominates the first_seen-sorted feed. ``OBJECTID`` / ``ID`` are
    # ArcGIS query-time row identifiers that are NOT stable across the upstream's
    # re-indexing — observed churning 3x in two days for the *same* site
    # (identical title + start + end, three different OBJECTIDs). Derive the
    # identity from a stable open-data id when the layer offers one, otherwise
    # from the stable title; never from the volatile OBJECTID/ID row number.
    # ROHTITEL, nicht der reparierte: Die Kosmetik darf die Identität der
    # Baustelle nicht verschieben (und damit first_seen zurücksetzen).
    identifier = properties.get("OGD_ID") or raw_title
    guid = make_guid(
        "baustellen",
        str(identifier),
        guid_start.isoformat() if guid_start else "",
        end.isoformat() if end else "",
    )
    pub_date = start or end or datetime.now(tz=VIENNA_TZ)
    return ConstructionEvent(
        guid=guid,
        title=_mark_upstream_truncation(raw_title),
        description=description,
        starts_at=start,
        ends_at=end,
        pub_date=pub_date,
        context=context or None,
        location=location or None,
    )


# Feature fields that change without the site changing: ArcGIS row numbers
# (see the GUID note in ``_feature_to_event``) and the WFS response header.
_RAW_VOLATILE_PROPERTIES = frozenset({"OBJECTID"})


def _raw_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """One WFS layer as kept in ``data/raw/baustellen/`` (``src/utils/raw_capture.py``).

    Every feature's properties as delivered, minus the row number that the
    upstream re-indexes, and of its geometry only the type and the first
    position (the only part the cache uses, ``_first_lonlat``): a full
    line geometry would dominate the file without telling anything about
    the notice. The response header (``timeStamp``, ``numberMatched``, …)
    and the volatile feature ``id`` are left out.
    """
    features: list[dict[str, Any]] = []
    for feature in _iter_features(payload):
        properties = feature.get("properties")
        record: dict[str, Any] = {
            "properties": {
                k: v for k, v in properties.items() if k not in _RAW_VOLATILE_PROPERTIES
            }
            if isinstance(properties, dict)
            else properties,
        }
        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            record["geometry"] = {
                "type": geometry.get("type"),
                "first_position": _first_lonlat(geometry.get("coordinates")),
            }
        features.append(record)
    features.sort(key=lambda f: (_raw_sort_key(f), raw_capture.content_key(f)))
    return {"features": features}


def _raw_sort_key(feature: dict[str, Any]) -> str:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return ""
    for key in ("OGD_ID", *TITLE_KEYS, *STREET_KEYS):
        value = properties.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _collect_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for feature in _iter_features(payload):
        event = _feature_to_event(feature)
        if not event:
            properties = feature.get("properties")
            raw_capture.note_drop(
                "baustellen",
                "ohne Titel und Straße",
                properties.get("OGD_ID") if isinstance(properties, dict) else None,
            )
            continue
        events.append(serialize_for_cache(event.to_item()))
    return events
