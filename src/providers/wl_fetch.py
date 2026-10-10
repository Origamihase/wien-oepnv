"""Fetching and assembling events from the Wiener Linien API.
# No ThreadPoolExecutor used here (Thread Pool Resource Leaks checked)
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, UTC
from typing import Any, cast
from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

import requests
from dateutil import parser as dtparser

from ..utils.files import loads_finite
from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe
from ..utils import raw_capture, source_shape
from ..utils.ids import make_guid
from ..utils.logging import sanitize_log_arg
from ..utils.stations import canonical_name, display_name
from ..utils.text import DROP_ACCESS, facility_drop_label, is_station_access_only
from ..feed.config import ENDS_AT_GRACE_MINUTES

from .wl_lines import (
    LINE_CODE_RE,
    _detect_line_pairs_from_text,
    _ensure_line_prefix,
    _line_display_from_pairs,
    _line_tokens_from_pairs,
    _make_line_pairs_from_related,
    _merge_line_pairs,
)
from .wl_plausibility import note_corrections, plausible_end, plausible_start, reset_corrections
from . import wl_resolved
from .wl_text import (
    FACILITY_ONLY,
    KW_EXCLUDE,
    KW_RESTRICTION,
    _is_facility_only,
    _tidy_title_wl,
    _title_core,
    _topic_key_from_title,
)


def _facility_drop_reason(title: str) -> str | None:
    """Drop reason for a title about station facilities only, else ``None``.

    A lift or escalator (``_is_facility_only``), since 2026-10-09 the
    folding ramp of a train (own label since 2026-10-10), and a
    closed station access (``is_station_access_only``: "Aufgangssperre",
    "Sperre Ausgang") have no place in the feed: the trains still stop.
    """
    if _is_facility_only(title):
        return facility_drop_label(FACILITY_ONLY.findall(title))
    if is_station_access_only(title):
        return DROP_ACCESS
    return None


# Basis-URL aus Secret/ENV, Fallback: OGD-Endpoint
_WL_DEFAULT_BASE = "https://www.wienerlinien.at/ogd_realtime"

# Security: only accept env overrides that point at the official Wiener Linien
# OGD host. Without this, ``WL_RSS_URL=https://evil.com`` would (a) inject
# attacker-controlled JSON into the cached feed items and (b) put
# ``https://evil.com`` into every WL item's ``<link>`` element, weaponising the
# public RSS feed as a phishing redirector. ``validate_http_url()`` only
# checks SSRF/DNS-rebinding properties, not host identity.
#
# 2026-05-10 (HTTPS-only Provider URL Drift): the validator additionally
# pins the scheme to ``https``. ``validate_http_url`` accepts both ``http``
# and ``https``; without this pin, an env override such as
# ``WL_RSS_URL=http://www.wienerlinien.at/ogd_realtime`` would be accepted,
# every ``_fetch_traffic_infos`` / ``_fetch_news`` call would issue a
# plaintext request, and an MITM could substitute arbitrary alerts that
# flow verbatim into the public ``docs/feed.xml`` artefact. Mirrors the
# canonical ``validate_public_feed_url`` HTTPS-only pin
# (``src/utils/http.py``) and the VOR / OEBB sibling validators.
_WL_TRUSTED_HOSTS = frozenset({"www.wienerlinien.at"})


def _validated_wl_base(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — see header above.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _WL_TRUSTED_HOSTS:
        return None
    return safe


_WL_BASE_ENV = os.getenv("WL_RSS_URL", "").strip()
_WL_BASE_OVERRIDE = _validated_wl_base(_WL_BASE_ENV) if _WL_BASE_ENV else None
if _WL_BASE_ENV and _WL_BASE_OVERRIDE is None:
    logging.getLogger(__name__).warning(
        "WL_RSS_URL %r ist kein bekannter Wiener-Linien-Host; verwende Standard.",
        _WL_BASE_ENV,
    )
WL_BASE = (_WL_BASE_OVERRIDE or _WL_DEFAULT_BASE).rstrip("/")

log = logging.getLogger(__name__)

# Security: ``MAX_WL_FETCH_TIMEOUT`` is the Slowloris-defence ceiling for the
# public ``fetch_events`` API. The ``timeout`` parameter flows through
# ``_fetch_traffic_infos`` / ``_fetch_news`` -> ``_get_json`` ->
# ``fetch_content_safe`` as both the connect and read budget for every Wiener
# Linien OGD request. The current call site in ``build_feed.py`` uses
# ``effective_timeout`` (already capped at ``feed_config.MAX_PROVIDER_TIMEOUT``)
# and ``scripts/update_wl_cache.py`` uses the 20-second default, so the
# production blast radius is currently zero. But ``fetch_events`` is exported
# as a public API (``__all__``) and a future caller passing an env-controlled
# or user-controlled value (e.g. a hypothetical ``WL_FETCH_TIMEOUT`` env var
# or a CLI flag wired into a maintenance script) would otherwise inherit the
# unbounded shape — ``timeout=99999`` (intentional misconfig, leaked CI env,
# compromised secret store) lets a sluggish or attacker-controlled upstream
# peer hold the worker for ~28 hours per fetch, stalling the cron pipeline.
# Capping inside the function (defense-in-depth) means every caller — current
# and future — inherits the ceiling without having to remember to add it.
# 25 seconds matches ``feed_config.MAX_PROVIDER_TIMEOUT`` (the orchestrator-
# level Slowloris ceiling) so no orchestrator-capped value is ever rejected,
# while the 20-second default (a conservative WL-OGD response budget) is
# preserved unchanged. TIGHTEN-only contract mirrors ``MAX_OEBB_FETCH_TIMEOUT``
# (``src/providers/oebb.py``) — same parameter-boundary defense-in-depth
# pattern documented in the 2026-05-07 Slowloris-Cap Drift Round 4 journal
# entry, applied to the WL sibling that round explicitly named as still-open.
MAX_WL_FETCH_TIMEOUT = 25

# Precompiled regex patterns
_ALPHA_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")
_HALT_SUFFIX_RE = re.compile(r"\(\d+\s+Halt(?:e)?\)$")


# ---------------- HTTP-Session mit Retry ----------------

WL_USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"
WL_SESSION_HEADERS = {"Accept": "application/json"}


# ---------------- Zeit & Utils ----------------

def _iso(s: object) -> datetime | None:
    """Parst ISO (inkl. 'Z' / TZ ohne Doppelpunkt) robust zu aware datetime.

    Accepts ``object`` rather than ``str | None`` because every call site
    passes a raw ``dict.get(...)`` value (typed ``Any``) straight from the
    upstream JSON — see ``_best_ts`` (``time.start`` / ``updated`` / …) and
    the two item loops in ``fetch_events``. A *truthy non-string* value (a
    numeric epoch like ``1700000000``, a list, a dict) would otherwise reach
    ``s.replace(...)`` below and raise ``AttributeError`` — which the
    ``(ValueError, OverflowError)`` guard around ``isoparse`` does NOT catch.
    That exception then propagates out of the unguarded ``fetch_events`` item
    loop and disables the entire WL cache refresh for the cycle
    (``update_wl_cache.py`` swallows it via a broad ``except Exception``).
    Type-guarding here upholds the documented "one bad timestamp must not
    abort the fetch" contract below.
    """

    if not isinstance(s, str) or not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = dtparser.isoparse(s)
    except (ValueError, OverflowError) as exc:
        # Fail soft: a single malformed upstream timestamp must NOT
        # propagate out of fetch_events. The fetch_events item loops
        # have no per-item guard, so an unhandled ValueError aborts the
        # whole fetch; update_wl_cache.py then swallows it via its broad
        # ``except Exception`` and silently keeps the stale cache — one
        # bad date field disables the entire WL refresh for that cycle.
        # Mirrors the defensive parsing in the sibling timestamp parsers
        # ``oebb._parse_dt_rfc2822`` and ``wl_text.extract_date_from_title``.
        # ``isoparse``'s ValueError embeds a fragment of the upstream
        # input, so the bound name is sanitised before logging.
        log.debug(
            "WL-Zeitstempel nicht parsebar (%s) – ignoriere Feld.",
            sanitize_log_arg(str(exc)),
        )
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return cast('datetime | None', dt)


# A title that only lists lines says nothing the line prefix does not. The
# demonstration notice of 2026-10-01 was titled "D, 1, 2, 71, 1A, 3A" and
# stood in the feed as "D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A"; its
# description opened with "<h2>Demonstration</h2>".
_LINE_LIST_SEPARATORS_RE = re.compile(r"[\s,;/&+]+|\bund\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)


def _title_or_heading(title_raw: str, desc: str) -> str:
    """WL's tidied title, or the description's heading when the title only lists lines.

    ``_tidy_title_wl`` strips leading/trailing dashes/colons, so a source
    title of only punctuation ("---") tidies to "" — then
    ``_ensure_line_prefix`` would render just the line codes ("U1/U2") with
    no description. Such a title falls back to the heading, else to a
    generic label. The heading keeps its first word: ``_tidy_title_wl``
    would strip "Gleisbauarbeiten" from "Gleisbauarbeiten Märzstraße" as a
    label, and there it is the cause.
    """
    title = _tidy_title_wl(title_raw)
    if title and _LINE_LIST_SEPARATORS_RE.sub("", LINE_CODE_RE.sub("", title)):
        return title
    match = _HEADING_RE.search(desc)
    # Unescape before the tag strip, so entity-encoded tags go too.
    text = re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))) if match else ""
    heading = re.sub(r"[<>«»‹›]+", "", " ".join(text.split())).strip(" -–—:/")
    return heading or title or "Meldung"


def _coerce_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` if it is a dict, otherwise an empty dict.

    Zero Trust: ``_extract_wl_items`` filters non-dict elements at the list
    boundary, but per-item lookups like ``ti.get("attributes")`` and
    ``ti.get("time")`` still return ``Any``. The previous ``... or {}`` shape
    only collapses *falsy* values (``None``, ``0``, ``""``); a misbehaving /
    compromised upstream peer could ship a truthy non-dict (``[1, 2]``,
    ``"abc"``, ``42``, ``True``) which then raises ``AttributeError`` on the
    very next ``.get(...)`` and propagates out of ``fetch_events``, silently
    disabling the WL cache refresh (``update_wl_cache.py`` swallows it via
    ``except Exception:``). Same drift shape as the documented ``or [] /
    or {}`` rounds for the outer collection.
    """
    return value if isinstance(value, dict) else {}


def _best_ts(obj: dict[str, Any]) -> datetime | None:
    """The start of a WL item: ``time.start``, else when WL published it.

    ``time.end`` is no candidate. As the start of an item without
    ``time.start`` it made ``_is_active`` see a disruption that had not
    begun yet, and the item stayed out of the feed until it ended
    (``time.end`` 31.12. → dropped until 31.12.). Without any of these the
    start stays open and the time line reads "Bis …".
    """
    t = _coerce_dict(obj.get("time"))
    attrs = _coerce_dict(obj.get("attributes"))
    for cand in (
        _iso(t.get("start")),
        _iso(obj.get("updated")),
        _iso(obj.get("timestamp")),
        _iso(attrs.get("lastUpdate")),
        _iso(attrs.get("created")),
    ):
        if cand:
            return cand
    return None


# Status values (``status`` / ``state`` on the POI or its ``attributes``)
# that mark a disruption finished / inactive — such items are dropped.
# Matched on WORD BOUNDARIES, not bare substrings: the German *active*
# statuses ``laufende`` (ongoing) and ``ausstehende`` (pending) — and the
# noun ``Wochenende`` (weekend) — all contain the substring ``ende`` and were
# wrongly dropped by the prior ``"ende" in blob`` membership test, silently
# discarding valid live disruptions. All keywords are ASCII, so ``\b`` is safe.
# ``resolved`` is what WL actually sends: a finished incident stays in
# ``trafficInfoList`` for one more fetch with that status, then disappears.
# Kept as active, it showed outdated text on the TV for up to 30 minutes
# (2026-10-05 11:01: "Die Linie U6 fährt derzeit nicht zwischen Westbahnhof
# und Längenfeldgasse" while the U6 already ran again; 12 cases from
# 2026-10-04 to 2026-10-05) and, merged with its active ``-F01`` follow-up,
# hid the follow-up's current text. Its display tickers carry no status;
# ``wl_resolved`` sends them after it.
_INACTIVE_STATUS_RE = re.compile(
    r"\b(?:finished|inactive|inaktiv|done|closed|nicht aktiv|ended|ende|"
    r"abgeschlossen|beendet|geschlossen|resolved)\b"
)


def _is_inactive_status(*values: object) -> bool:
    """True when any status/state *value* marks the item finished/inactive."""
    blob = " ".join(str(v or "") for v in values).lower()
    return _INACTIVE_STATUS_RE.search(blob) is not None


def _is_finished(info: Any) -> bool:
    """True when the WL message *info* is finished by its status fields."""
    info = _coerce_dict(info)
    attrs = _coerce_dict(info.get("attributes"))
    return _is_inactive_status(info.get("status"), attrs.get("status"), attrs.get("state"))


def _status_drop_reason(info: Any, stale: set[str]) -> str | None:
    """Why the traffic info *info* leaves at once, ``None`` when it stays.

    A finished message goes, and so do the display tickers of a finished
    incident (*stale*, see ``wl_resolved``), which WL sends without a status.
    """
    if _is_finished(info):
        return "Status inaktiv"
    if isinstance(info, dict) and wl_resolved.ticker_key(info) in stale:
        return "Kurzmeldung einer erledigten Störung"
    return None


def _wl_identity(
    prefix: str,
    line_pairs: list[tuple[str, str]],
    real_start: datetime | None,
    topic_key: str,
) -> str:
    """Build a stable, collision-resistant ``_identity`` for a WL item.

    The key is line-set + start-day + ``topic_key``. ``_dedupe_items``
    (``build_feed.py``) keys on ``_identity`` FIRST and never reaches the
    finer ``guid``, so whatever this function conflates is dropped from the
    feed without a trace.

    ``topic_key`` used to be folded in only when the line set or the start
    date was missing, on the assumption that lines + day identify a
    disruption on their own. The live data disproves it — the Wiener Linien
    routinely publish several unrelated disruptions for one line on one day
    (2026-09-12, ``feed lint``: 4 of 83 items dropped):

        wl|störung|L=44|D=2026-09-11
            44: Veranstaltung Züge halten Rosensteingasse …
            44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz
            44: Fahrtbehinderung Veranstaltung
        wl|hinweis|L=49A,50B|D=2026-08-25
            49A/50B: Mondweg
            49A/50B: Hüttergasse        <- anderer Ort, verworfen

    Folding ``topic_key`` in unconditionally costs no stability, because it
    is not a new signal at this layer: the bucket key in :func:`fetch_events`
    and the per-item ``guid`` are BOTH already built from
    ``(category, topic_key, line set)``. So

    * true duplicates never reach this comparison — they were merged into one
      bucket upstream, by exactly that triple;
    * ``first_seen`` does not churn: :func:`_state_key_for_item` keys it on
      the ``guid``, which already moves whenever ``topic_key`` moves.

    What the change does drop is the legacy ``_identity``-keyed ``first_seen``
    fallback in ``_lookup_state`` for pre-guid entries. Measured on the live
    state: of 53 cached WL items, 48 resolve via ``guid``, 5 are new and 0
    depend on that fallback.
    """
    id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
    id_day = (
        real_start.date().isoformat()
        if isinstance(real_start, datetime)
        else "None"
    )
    return f"wl|{prefix}|L={id_lines}|D={id_day}|TK={topic_key}"


def _is_active(start: datetime | None, end: datetime | None, now: datetime) -> bool:
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=ENDS_AT_GRACE_MINUTES)):
        return False
    return True


def _effective_end(
    desc_raw: str, end: datetime | None, start: datetime | None
) -> datetime | None:
    """WL's ``time.end``, or the end its "Zeitraum:" names instead of an 11:11 one.

    See :func:`wl_plausibility.plausible_end`.
    """
    return plausible_end(desc_raw, end, start)


def _effective_start(
    title_raw: str,
    desc_raw: str,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> datetime | None:
    """When the measure of a WL item begins, for ``starts_at``.

    The sources of the item (``time.start``/``time.end``, the title date,
    the "Zeitraum:" section) are weighed by
    :func:`wl_plausibility.plausible_start`; every contradiction it settles
    is logged and collected for ``data/wl_plausibility_anomalies.json``.
    """
    effective, corrections = plausible_start(title_raw, desc_raw, start, end, now)
    note_corrections(title_raw, corrections)
    return effective


def _intervals_overlap(
    start_a: datetime | None,
    end_a: datetime | None,
    start_b: datetime | None,
    end_b: datetime | None,
) -> bool:
    """Return True if the time intervals [start_a, end_a] and [start_b, end_b] overlap."""

    s_a = start_a or datetime.min.replace(tzinfo=UTC)
    s_b = start_b or datetime.min.replace(tzinfo=UTC)
    e_a = end_a or datetime.max.replace(tzinfo=UTC)
    e_b = end_b or datetime.max.replace(tzinfo=UTC)

    return s_a <= e_b and s_b <= e_a

def _as_list(val: Any) -> list[Any]:
    if val is None:
        return []
    return list(val) if isinstance(val, list | tuple | set) else [val]


# ---------------- Stop-Namen extrahieren ----------------

def _stop_names_from_related(rel_stops: list[Any]) -> list[str]:
    dedup: dict[str, str] = {}
    for s in rel_stops:
        raw: str | None = None
        if isinstance(s, dict):
            for key in ("name", "stopName", "title"):
                val = s.get(key)
                if val and _ALPHA_RE.search(str(val)):
                    raw = str(val).strip()
                    break
        elif isinstance(s, str):
            if _ALPHA_RE.search(s):
                raw = s.strip()
        if not raw:
            continue
        canonical = canonical_name(raw)
        if canonical:
            final = display_name(canonical)
        else:
            # ``\s+`` (NOT ``\s{2,}``): the prior shape only collapsed runs
            # of >=2 whitespace chars, so a single embedded ``\n`` or ``\t``
            # in a relatedStops name survived into the RSS ``Haltestelle:``
            # description line verbatim, breaking the single-line render
            # downstream. Matches the canonical shape of the sibling
            # ``_normalize_whitespace`` helper below.
            final = re.sub(r"\s+", " ", raw).strip()
        if not final:
            continue
        key = final.casefold()
        dedup.setdefault(key, final)
    return sorted(dedup.values(), key=lambda x: x.casefold())


# ---------------- Kontext für Titel ----------------

def _normalize_whitespace(value: str) -> str:
    # Collapse any whitespace run (incl. isolated newlines and tabs) to a
    # single space so titles and labels stay single-line for RSS/Atom.
    return re.sub(r"\s+", " ", value or "").strip()


def _split_extra(extra: str) -> tuple[str, str] | None:
    if not extra or ":" not in extra:
        return None
    head, tail = extra.split(":", 1)
    head = head.strip()
    tail = _normalize_whitespace(tail)
    if not head or not tail:
        return None
    return head, tail


def _context_values_from_stop_names(
    stop_names: Iterable[str], base_title: str
) -> list[str]:
    base_cf = base_title.casefold()
    seen: set[str] = set()
    values: list[str] = []
    for name in sorted(stop_names, key=lambda x: x.casefold()):
        clean = _normalize_whitespace(str(name))
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        if key in base_cf:
            continue
        values.append(clean)
    return values


def _context_values_from_extras(
    extras: Sequence[str], base_title: str
) -> tuple[list[str], list[str]]:
    base_cf = base_title.casefold()
    values: list[str] = []
    used: list[str] = []
    seen: set[str] = set()
    for extra in extras:
        parsed = _split_extra(extra)
        if not parsed:
            continue
        label, value = parsed
        if label.casefold() not in {"station", "location"}:
            continue
        key = value.casefold()
        if not value or key in seen or key in base_cf:
            continue
        seen.add(key)
        values.append(value)
        used.append(extra)
    return values, used


def _title_quality_key(title: str, title_core: str) -> tuple[int, int, int]:
    """Score titles so that informative variants win over short generics."""

    normalized_title = _normalize_whitespace(title)
    core = _normalize_whitespace(title_core)
    tokens = [tok for tok in core.split() if tok]
    informative_tokens = [tok for tok in tokens if len(tok) >= 4]
    return (
        len(informative_tokens),
        len(core),
        -len(normalized_title),
    )


def _description_info_score(
    desc: str,
    *,
    title: str,
    stop_names: Iterable[str],
    extras: Sequence[str],
) -> tuple[int, int, int, int]:
    """Return a tuple describing how informative a description is."""

    normalized = _normalize_whitespace(desc)
    if not normalized:
        return (0, 0, 0, 0)

    desc_cf = normalized.casefold()
    title_norm = _normalize_whitespace(title).casefold()
    non_title = 0 if desc_cf and desc_cf == title_norm else 1

    info_hits = 0
    seen: set[str] = set()

    for name in stop_names:
        clean = _normalize_whitespace(str(name))
        if len(clean) < 3:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    for extra in extras:
        parsed = _split_extra(extra)
        value = parsed[1] if parsed else _normalize_whitespace(str(extra))
        if len(value) < 3:
            continue
        key = value.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    length = len(normalized)
    word_count = len(re.findall(r"\w+", normalized, flags=re.UNICODE))
    return (non_title, info_hits, length, word_count)


def _format_context(values: Sequence[str], limit: int = 2) -> tuple[str, int]:
    if not values:
        return "", 0
    trimmed = list(values[:limit])
    if not trimmed:
        return "", 0
    context = ", ".join(trimmed)
    if len(values) > limit:
        context += " …"
    return context, len(trimmed)


def _build_context_suffix(
    bucket: dict[str, Any], base_title: str, lines_disp: Sequence[str]
) -> tuple[str | None, list[str]]:
    if lines_disp:
        return None, []

    stop_context = _context_values_from_stop_names(
        bucket.get("stop_names", []), base_title
    )
    if stop_context:
        context, _ = _format_context(stop_context)
        if context:
            return context, []

    extras_context, used_extras = _context_values_from_extras(
        bucket.get("extras", []), base_title
    )
    if extras_context:
        context, used_count = _format_context(extras_context)
        if context:
            return context, used_extras[:used_count]

    return None, []


# ---------------- API Calls ----------------

def _get_json(
    path: str,
    params: list[tuple[Any, ...]] | None = None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"

    def _fetch(s: requests.Session) -> dict[str, Any]:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                content = fetch_content_safe(
                    s,
                    url,
                    params=params or None,
                    timeout=timeout,
                    allowed_content_types=("application/json",),
                )
                # Security: ``loads_finite`` pins parse_constant +
                # parse_float hooks that reject NaN / Infinity / -Infinity
                # literals AND scientific-notation overflow (``1e1000`` →
                # +inf). Closes the network-tainted sibling of the
                # writer-pin family (Round 1485 / 1487 / 1488) and the
                # on-disk reader-pin family (Round 1503): a compromised
                # Wiener Linien upstream / MITM / DNS-hijack serving
                # crafted JSON would otherwise propagate ``float('nan')``
                # / ``float('inf')`` into the live-events dedup pipeline.
                data = loads_finite(content)
                if not isinstance(data, dict):
                    log.warning(
                        "Unerwartetes Payload-Format von %s: %s erwartet, aber %s erhalten (Zero Trust)",
                        sanitize_log_arg(url),
                        "dict",
                        type(data).__name__,
                    )
                    return {}
                return data
            except (ValueError, json.JSONDecodeError, RecursionError) as exc:
                # Resilience: include ``RecursionError`` so a malicious
                # upstream serving deeply-nested JSON cannot crash the
                # build process. ``json.loads`` on a deeply-nested array
                # / object exceeds Python's recursion limit and raises
                # ``RecursionError`` (NOT a subclass of ``JSONDecodeError``).
                log.warning(
                    "Antwort von %s ungültig oder kein JSON: %s",
                    sanitize_log_arg(url),
                    sanitize_log_arg(exc),
                )
                return {}
            except requests.RequestException as exc:
                if attempt < max_retries:
                    log.warning(
                        "Sporadischer Netzwerkfehler bei %s (Versuch %d/%d): %s",
                        sanitize_log_arg(url),
                        attempt,
                        max_retries,
                        sanitize_log_arg(exc),
                    )
                    time.sleep(2)
                    continue
                else:
                    log.error(
                        "Verbindung zu %s schlug nach %d Versuchen endgültig fehl: %s",
                        sanitize_log_arg(url),
                        max_retries,
                        sanitize_log_arg(exc),
                    )
                    return {}
        return {}

    if session is not None:
        return _fetch(session)

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as s:
        s.headers.update(WL_SESSION_HEADERS)
        return _fetch(s)


def _extract_wl_items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Extract a list-of-dicts collection from a WL API response.

    Zero Trust: ``_get_json`` validates the top-level shape is a dict, but
    the ``data["data"]`` value and the inner ``data["data"][key]`` value
    extracted from it are still ``Any``. The previous one-liner
    ``(data.get("data", {}) or {}).get(key, []) or []`` collapses *falsy*
    JSON shapes (``None``, ``0``, ``""``, ``[]``, ``{}``) to a safe empty
    container, but lets *truthy non-Mapping* / *truthy non-list* shapes
    through. A misbehaving / compromised upstream peer (or a tampered
    proxy response) could ship ``{"data": [1, 2]}``, ``{"data": "abc"}``,
    ``{"data": True}``, or ``{"data": {"trafficInfos": "abc"}}``; the
    resulting ``.get(key, [])`` then raises ``AttributeError`` (lists,
    strings, bools have no ``.get``) or the iteration in ``fetch_events``
    raises ``AttributeError`` / ``TypeError`` on each non-dict element.
    Both failure modes propagate out of ``fetch_events`` and disable the
    cache refresh entirely (``update_wl_cache.py`` falls into the
    ``except Exception:`` defensive branch). Mirror the
    ``isinstance(payload, list)`` shape guard already landed for the
    sibling Baustellen / VOR loaders so the documented empty-list
    fallback runs instead, and additionally drop non-dict elements so
    downstream ``.get(...)`` calls in ``fetch_events`` are safe.
    """
    inner = data.get("data")
    if not isinstance(inner, dict):
        return []
    items = inner.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _raw_snapshot(data: dict[str, Any], key: str) -> dict[str, Any]:
    """The WL response as kept in ``data/raw/wl/`` (``src/utils/raw_capture.py``).

    Everything WL sent, minus the ``serverTime`` that changes on every
    call, with the item list in a stable order.
    """
    snapshot = dict(data)
    message = snapshot.get("message")
    if isinstance(message, dict):
        snapshot["message"] = {k: v for k, v in message.items() if k != "serverTime"}
    inner = snapshot.get("data")
    if isinstance(inner, dict) and isinstance(inner.get(key), list):
        snapshot["data"] = {
            **inner,
            key: raw_capture.sorted_records(inner[key], "name", "title"),
        }
    return snapshot


# ---------------- Ausfall eines Teils der Quelle ----------------
#
# WL liefert in zwei Listen: ``trafficInfoList`` (Störungen) und
# ``newsList`` (Hinweise). Fiel eine davon aus, ging der Abruf früher mit
# der anderen allein durch: Am echten Stand vom 2026-10-04 16:01 UTC
# verschwanden ohne Störungsliste alle fünf laufenden Störungen aus den
# zehn Plätzen des deutschen Feeds, ohne Hinweisliste vier Hinweise — mit
# Exit-Code 0, also ohne Warnung und mit grünem Health check. Dasselbe bei
# einer Antwort, der ein Feld fehlt (``src/utils/source_shape.py``).
#
# Jetzt gilt eine Liste nur, wenn sie da ist und ihre Felder trägt, und eine
# leere nur, wenn auch die letzte gute Antwort (fast) leer war. Sonst
# kommt sie aus ihrer letzten guten Antwort (``data/raw/wl/``), die andere
# bleibt frisch; der Abruf meldet das (``fallback_parts``, Exit-Code 3 im
# Cache-Updater). Fehlt auch die letzte gute Antwort oder fallen beide
# Listen aus, scheitert der Abruf, und der Cache bleibt wie er ist — wie
# bisher bei einem vollständigen Ausfall.

_WL_REQUIRED = {
    "title": (source_shape.has("title"), source_shape.MIN_RECORDS),
    "description": (source_shape.has("description"), source_shape.MIN_RECORDS),
    "time": (source_shape.has("time"), source_shape.MIN_RECORDS),
    # Nicht jede Störung nennt eine Linie (69 von 80 am 2026-10-04).
    "relatedLines": (source_shape.has("relatedLines"), 10),
}

_fallback_parts: list[str] = []


class SourceIncompleteError(RuntimeError):
    """A part of the WL source gave no usable answer and has no last good one."""


def fallback_parts() -> list[str]:
    """The WL lists the last ``fetch_events`` took from their last good answer."""
    return list(_fallback_parts)


def _usable_items(data: Any, key: str, name: str) -> list[dict[str, Any]] | None:
    """The items of a WL answer, or ``None`` when the answer is not usable."""
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if not isinstance(inner, dict) or not isinstance(inner.get(key), list):
        return None
    items = _extract_wl_items(data, key)
    missing = source_shape.missing_fields(items, _WL_REQUIRED)
    if missing:
        log.warning(
            "WL %s: Feld(er) %s fehlen in allen %d Einträgen – Antwort unbrauchbar.",
            name,
            ", ".join(missing),
            len(items),
        )
        return None
    return items


def _list_items(name: str, key: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """The items of list *name*: fresh when usable, else from its last good answer.

    An empty list counts as no answer when the last good one held at least
    ``source_shape.MIN_RECORDS`` items: both lists carry long-running
    notices and were never empty, so an empty one is a server in trouble.
    """
    items = _usable_items(data, key, name)
    if items:
        raw_capture.write_snapshot("wl", name, _raw_snapshot(data, key))
        return items
    last = _usable_items(raw_capture.read_snapshot("wl", name), key, name)
    if items is not None and (last is None or len(last) < source_shape.MIN_RECORDS):
        raw_capture.write_snapshot("wl", name, _raw_snapshot(data, key))
        return items
    if last is None:
        raise SourceIncompleteError(f"WL {name}: keine brauchbare Antwort")
    log.warning(
        "WL %s: %s – letzte gute Antwort (%d Einträge) verwendet.",
        name,
        "leere Liste" if items is not None else "keine brauchbare Antwort",
        len(last),
    )
    _fallback_parts.append(name)
    return last


def _fetch_traffic_infos(
    timeout: int = 20, session: requests.Session | None = None
) -> Iterable[dict[str, Any]]:
    # explizit KEINE Facility-Feeds
    params = [("name", "stoerunglang"), ("name", "stoerungkurz")]
    data = _get_json("trafficInfoList", params=params, timeout=timeout, session=session)
    return _list_items("trafficInfoList", "trafficInfos", data)


def _fetch_news(
    timeout: int = 20, session: requests.Session | None = None
) -> Iterable[dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout, session=session)
    return _list_items("newsList", "pois", data)


# ---------------- Anzeigetafel-Doppel („stoerungkurz“) ----------------

# ``_fetch_traffic_infos`` fragt bewusst ZWEI WL-Feeds in einem Aufruf ab:
# ``stoerunglang`` (der ausformulierte Meldungstext) und ``stoerungkurz``
# (die Kurztexte der Anzeigetafeln). Für dieselbe Störung liefern beide
# einen Eintrag — die Kurzform je Ast sogar einen eigenen. Am 2026-09-17
# stand die Linie 49 deshalb dreifach im Feed:
#
#     49: Gleisschaden                                ← stoerunglang
#     49: Gleisschaden Betrieb ab Hütteldorfer Straße  ← stoerungkurz
#     49: Gleisschaden Betrieb ab Urban-Loritz-Platz   ← stoerungkurz
#
# Die beiden Kurzformen erschienen dabei OHNE Text. Ihre Beschreibung
# („Gleisschaden\nBetrieb ab Hütteldorfer Straße >“) wiederholt nur den
# eigenen Titel, und ``_summary_duplicates_title`` in ``build_feed`` leert
# sie folgerichtig — sichtbar blieb eine Schlagzeile über einem leeren
# Rumpf. Zwei der zehn Plätze des deutschen Feeds trugen damit null
# Information, während der Langtext danebenstand und alles sagte: „Kein
# Betrieb zwischen Hütteldorfer Straße U und Urban-Loritz-Platz. Die Züge
# fahren ab Hütteldorfer Straße bis Joachimsthalerplatz. …“
#
# Das Bucketing über ``topic_key`` fasst sie nicht: Ohne Treffer in
# ``TITLE_TOPIC_TOKENS`` fällt der Schlüssel auf den ganzen Titelkern
# zurück, und der unterscheidet sich je Ast. Jedes neue Ursachenwort
# („Gleisschaden“, „Oberleitungsschaden“, „Weichenstörung“, …) einzeln
# nachzupflegen wäre die dritte Runde desselben Spiels — die beiden
# vorigen sind bei ``TITLE_TOPIC_TOKENS`` dokumentiert.
#
# Die Regel hier braucht kein Ursachenwort. Sie stellt zweimal dieselbe
# Frage: Sagt dieser Text etwas, das jener nicht schon sagt?
#
#   1. Die Beschreibung der Meldung fügt ihrem EIGENEN Titel nichts hinzu
#      → sie ist eine reine Schlagzeile.
#   2. Ihr Titel steht bereits vollständig in der Beschreibung einer
#      anderen Meldung derselben Linien, derselben Kategorie, mit
#      überlappendem Zeitraum → jene sagt alles, was diese sagt.
#
# Nur wenn BEIDES gilt, wandert die Schlagzeile in die andere Meldung:
# Haltestellen und Extras werden übernommen, der Zeitraum geweitet, Titel
# und Text der ausführlichen Meldung bleiben. Verworfen wird nichts, was
# nicht nachweislich woanders steht.
#
# Gegenproben an den Live-Daten (Cache 2026-09-17, 37 Störungen):
#
#   * ``49A/50B: Mondweg`` und ``49A/50B: Hüttergasse`` — zwei Straßen,
#     ein Linienpaar. Beide tragen eigenen Text und scheitern schon an
#     (1). Genau der Fall, an dem das frühere pauschale
#     ``_identity``-Dedupe scheiterte (s. ``TITLE_TOPIC_TOKENS``).
#   * ``12A: Betrieb ab Johnstraße U`` — Schlagzeile ohne Langtext
#     daneben, scheitert an (2). Sie ist die einzige Information zu ihrer
#     Linie und bleibt.
#   * Über den ganzen Cache greift die Regel bei genau einer Linie: 49.
#     34 der 37 Störungen sind Schlagzeilen, aber nur dort steht eine
#     ausführliche Meldung daneben, die sie abdeckt.
#
# Am 2026-09-19 zeigte sich die Grenze dieser Fassung. Eine Demonstration
# am Ring: WL schickte EINE ausführliche Meldung für sieben Linien
# (``1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026``, Kategorie
# ``Hinweis``, mit Maßnahme je Linie) und je Linie eine Kurzmeldung
# (``1: Demonstration Betrieb ab Hintere Zollamtsstraße``, Kategorie
# ``Störung``, darunter WLs Textbaustein „Nach einer Fahrtbehinderung
# kommt es zu unterschiedlichen Intervallen."). Drei Gatter, drei Nein:
# die Linienmengen waren nicht gleich, die Kategorien nicht gleich, und
# der Textbaustein galt als eigener Inhalt. Ergebnis im Feed: zehn von
# zehn Plätzen für Kurzmeldungen eines Ereignisses, die Langmeldung auf
# Platz 12, alles andere verdrängt. Seither:
#
#   1. Die Linien der Schlagzeile müssen in denen der Langmeldung
#      ENTHALTEN sein, nicht gleich. Was die Langmeldung zu genau dieser
#      Linie sagt, deckt die Schlagzeile ab; der Wortvergleich bleibt.
#   2. Eine ``Störung``-Schlagzeile darf in einen ``Hinweis`` wandern —
#      nur in diese Richtung (``_categories_compatible``).
#   3. Der Textbaustein zählt als nichts (``_is_headline_only``).
#
# Gemessen am Cache des 2026-09-19 (47 Kurzmeldungen): 9 falten, vier in
# die Demonstration, fünf in ``25/26/27: Gleisbauarbeiten``. Keine davon
# sagt etwas, das ihre Langmeldung nicht sagt — das prüft weiterhin der
# Wortvergleich, jetzt mit aufgelösten HTML-Entities, weil der Langtext
# als HTML im Bucket liegt (``Zollamtsstra&szlig;e``).

_WORD_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _gate_text(*parts: str) -> str:
    """The text the keyword gates (``KW_EXCLUDE``, ``KW_RESTRICTION``) read.

    WL sends the news descriptions as HTML with the umlauts as entities
    ("wird die Linie 18 kurz gef&uuml;hrt", "Einschr&auml;nkung"). Read raw,
    no keyword with an umlaut could match in a description, only in the
    plain-text title; "18: LCC-Herbstmarathon am 11.10.2026" was dropped
    for that reason (2026-10-04). Tags become spaces so "<h2>Sperre</h2>"
    stays a word of its own.
    """
    return " ".join(html.unescape(_HTML_TAG_RE.sub(" ", " ".join(parts))).split())


def _content_tokens(text: str) -> frozenset[str]:
    """Kleingeschriebene Wortmenge von *text*, Satzzeichen entfernt.

    Trägt die Umbrüche und Pfeile der Anzeigetafel-Texte mit ab
    (``"Gleisschaden\\nBetrieb ab Hütteldorfer Straße >"``) und macht
    ``Urban-Loritz-Platz`` mit ``Urban Loritz Platz`` vergleichbar — WL
    schreibt denselben Ort in Titel und Fließtext verschieden. HTML-Tags
    und -Entities werden vorher aufgelöst: Die ausführliche Meldung liegt
    als HTML im Bucket, und ohne Auflösung fände sich ``zollamtsstraße``
    aus einem Kurztitel im Langtext ``Zollamtsstra&szlig;e`` nie wieder.
    """
    if not text:
        return frozenset()
    plain = html.unescape(_HTML_TAG_RE.sub(" ", text))
    return frozenset(
        tok for tok in _WORD_SPLIT_RE.sub(" ", plain).casefold().split() if tok
    )


def _covered_by(tokens: frozenset[str], text: str) -> bool:
    """True, wenn jedes Wort aus *tokens* schon in *text* vorkommt.

    Eine leere Wortmenge ergibt False: Fehlender Text ist keine
    Redundanz, sondern fehlende Information. Eine Meldung ohne Titel oder
    ohne Beschreibung darf darüber nicht stillschweigend verschwinden.
    """
    return bool(tokens) and tokens <= _content_tokens(text)


def _says_nothing_new(text: str, *, beyond: str) -> bool:
    """True, wenn *text* kein Wort enthält, das nicht schon in *beyond* steht."""
    return _covered_by(_content_tokens(text), beyond)


# WLs Textbaustein unter Anzeigetafel-Kurzmeldungen. Er stand am
# 2026-09-19 unter 7 von 47 Kurzmeldungen wortgleich, nennt keine Linie,
# keinen Ort und keine Maßnahme — die Information ist der Titel. Das
# vorangestellte ``Linie`` deckt das ``Linie 2:``-Präfix ab, das WL dem
# Baustein voranstellt; die Nummer dahinter steht ohnehin im Titel.
_WL_TICKER_BOILERPLATE = (
    "Linie Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen"
)


def _is_headline_only(bucket: dict[str, Any]) -> bool:
    """Bedingung (1): Die Beschreibung sagt nichts über den Titel hinaus.

    WLs Textbaustein (``_WL_TICKER_BOILERPLATE``) zählt dabei als nichts,
    ebenso die eigenen Liniennummern: Die Beschreibung trägt sie als
    ``Linie 1:``-Präfix, der Bucket-Titel je nach Quelle mit oder ohne
    Präfix — welche Linien gemeint sind, steht in ``lines_pairs``.
    """
    lines = " ".join(_line_tokens_from_pairs(bucket.get("lines_pairs", [])))
    return _says_nothing_new(
        bucket.get("desc_base", ""),
        beyond=f"{bucket.get('title', '')} {lines} {_WL_TICKER_BOILERPLATE}",
    )


def _categories_compatible(src_category: str, cand_category: str) -> bool:
    """Darf eine Schlagzeile der Kategorie *src* in *cand* wandern?

    Gleiche Kategorie immer. Dazu die eine Richtung, die die Daten
    verlangen: WL führt die ausformulierte Meldung zu einem Ereignis als
    ``Hinweis`` (Vorankündigung aus ``_fetch_news``) und die Kurztexte der
    Anzeigetafeln am Tag selbst als ``Störung``. Die Kategorie erreicht
    das Display nie — der RSS-Eintrag trägt kein ``<category>`` —, sie
    bricht nur Gleichstände in der Sortierung nach ``first_seen``. Der
    umgekehrte Weg bleibt zu: Ein ``Hinweis`` ist eine Ankündigung, und
    eine ``Störung``-Langmeldung sagt nichts über ihn aus.
    """
    return src_category == cand_category or (
        src_category == "Störung" and cand_category == "Hinweis"
    )


def _ticker_fold_target(
    src: dict[str, Any], buckets: dict[str, dict[str, Any]], *, skip: str
) -> str | None:
    """Schlüssel der Meldung, die alles sagt, was die Schlagzeile *src* sagt.

    ``None``, wenn es keine gibt. Kandidaten müssen mindestens die Linien
    der Schlagzeile tragen (``_line_tokens_from_pairs``, Obermenge), eine
    verträgliche Kategorie haben (``_categories_compatible``), sich
    zeitlich überlappen und selbst mehr als eine Schlagzeile sein — sonst
    würden zwei inhaltsleere Kurzmeldungen einander „abdecken“ und eine
    davon grundlos verschwinden. Bei mehreren Treffern gewinnt die wortreichste
    Beschreibung; der Schlüssel bricht den Gleichstand, damit dieselbe
    Eingabe immer dasselbe Ergebnis liefert.
    """
    lines = frozenset(_line_tokens_from_pairs(src["lines_pairs"]))
    if not lines:
        return None
    # Die Liniennummer trägt der Titel als Präfix („49: Gleisschaden …“),
    # der Zieltext muss sie nicht wiederholen — welche Linien gemeint sind,
    # ist über den Linien-Vergleich unten bereits abschließend geklärt.
    # Bliebe sie im Vergleich, hinge die Regel daran, ob WL den Langtext
    # zufällig mit „Linie 49: …“ eröffnet; tut er es nicht, bliebe die
    # Doppelmeldung stehen.
    title_tokens = _content_tokens(src.get("title", "")) - {
        tok.casefold() for tok in lines
    }
    matches: list[tuple[int, str]] = []
    for key, cand in buckets.items():
        if key == skip or not _categories_compatible(
            src["category"], cand["category"]
        ):
            continue
        if not lines <= frozenset(_line_tokens_from_pairs(cand["lines_pairs"])):
            continue
        if _is_headline_only(cand):
            continue
        if not _intervals_overlap(
            src.get("starts_at"),
            src.get("ends_at"),
            cand.get("starts_at"),
            cand.get("ends_at"),
        ):
            continue
        desc = cand.get("desc_base", "")
        if not _covered_by(title_tokens, desc):
            continue
        matches.append((len(_content_tokens(desc)), key))
    if not matches:
        return None
    return max(matches)[1]


# WL numbers an incident ``I20261004-0019`` and the notices that follow it
# ``I20261004-0019-F01``, ``-F02``: one per line once the incident is over
# ("6/18: Schadhaftes Fahrzeug" became "6: …" and "18: …" on 2026-10-04).
# The number stays while the messages, their lines and so their GUIDs change;
# the feed follows an item from build to build by it
# (``build_feed._carry_item_identity``). Display tickers (``R1345-143``)
# have none.
_INCIDENT_NAME_RE = re.compile(r"^(I\d{8}-\d{4})(?:-F\d+)?$")


def _incident_ids(info: dict[str, Any]) -> set[str]:
    """``{"I20261004-0019"}`` for *info* and its follow-ups, empty for a ticker."""
    match = _INCIDENT_NAME_RE.match(str(info.get("name") or "").strip())
    return {match.group(1)} if match else set()


def _absorb_headline(target: dict[str, Any], src: dict[str, Any]) -> None:
    """Übernimm Haltestellen, Extras und Zeitraum von *src* nach *target*.

    Titel, Beschreibung und ``starts_at`` bleiben unangetastet: *target*
    ist die ausführliche Meldung und damit die maßgebliche. ``ends_at``
    wird nur geweitet, wenn BEIDE Enden bekannt sind — ein offenes Ende
    der Kurzmeldung darf ein bekanntes Ende der ausführlichen nicht
    aufweichen, und ein bekanntes darf ein offenes nicht verengen.
    """
    target["stop_names"].update(src["stop_names"])
    target.setdefault("wl_ids", set()).update(src.get("wl_ids") or ())
    for extra in src["extras"]:
        if extra not in target["extras"]:
            target["extras"].append(extra)
    src_pub, target_pub = src.get("pubDate"), target.get("pubDate")
    if src_pub and (not target_pub or src_pub < target_pub):
        target["pubDate"] = src_pub
    src_end, target_end = src.get("ends_at"), target.get("ends_at")
    if src_end is not None and target_end is not None:
        target["ends_at"] = max(target_end, src_end)


def _fold_display_tickers(buckets: dict[str, dict[str, Any]]) -> None:
    """Führe reine Schlagzeilen in die ausführliche Meldung derselben Störung.

    Verändert *buckets* an Ort und Stelle. Ein Ziel ist nie selbst
    Kandidat — ``_ticker_fold_target`` verlangt, dass es mehr als eine
    Schlagzeile ist —, kann also während des Laufs nicht wegfallen; welche
    Meldungen übrig bleiben, hängt daher nicht von der Reihenfolge ab. Das
    ``sorted`` ordnet allein die Logzeile, damit zwei Läufe über dieselben
    Daten dieselbe Meldung schreiben.
    """
    headlines = [key for key, b in sorted(buckets.items()) if _is_headline_only(b)]
    folded: list[str] = []
    for key in headlines:
        src = buckets.get(key)
        if src is None:  # pragma: no cover - Ziele sind nie Kandidaten
            continue
        target_key = _ticker_fold_target(src, buckets, skip=key)
        if target_key is None:
            continue
        _absorb_headline(buckets[target_key], src)
        del buckets[key]
        folded.append(str(src.get("title", "")))
    if folded:
        log.info(
            "WL: %d Anzeigetafel-Kurzmeldung(en) in die ausführliche Meldung "
            "übernommen: %s",
            len(folded),
            sanitize_log_arg(" | ".join(folded)),
        )


# ---------------- Sammel- und Teilmeldungen (E, F) ----------------
#
# Zwei ältere Regeln räumen nach der Bündelung noch einmal auf: E entfernt
# eine Meldung für mehrere Linien, wenn jede ihrer Linien eine eigene Meldung
# hat; F entfernt eine Meldung, deren Linien in einer anderen Meldung
# derselben Kategorie stecken (2025-12-24, Anlass „Silvesterlauf“ neben
# „Silvesterpfad“). Beide fragten nur nach Linien, Kategorie und Zeitraum,
# nie nach dem Inhalt. Die Filterprüfung vom 2026-10-03 fand in der
# WL-Cache-Historie seit April 108 Meldungen, die verschwanden, während eine
# solche „Obermenge“ lief, und danach unverändert wiederkamen, obwohl sie
# etwas anderes sagten:
#
#   * „2A: Bauarbeiten Renngasse“ zehn Tage lang, solange die Regenbogenparade
#     („1/1A/2/2A/31/3A/59A/71/74A/D: Regenbogenparade 2026“) angekündigt war;
#   * „77A: Umleitung wegen Veranstaltungen“ drei Wochen neben
#     „77A/80A: Ende der Gleisbauarbeiten“;
#   * „N8: Nußdorfer Straße U“ (Haltestellenverlegung) neben der Verlegung
#     „59A/N8: Dörfelstraße“;
#   * „11A: Gleisbauarbeiten Stadion U“ neben „11A/18: Veranstaltung am
#     12.09.2026“.
#
# Eine Meldung, die etwas anderes sagt, ist keine Doppelung. Seither fällt
# eine Meldung nur noch, wenn die andere sie inhaltlich abdeckt: Jedes Wort
# ihres Titels (ohne die eigenen Liniennummern) steht schon dort — die Frage,
# die auch ``_fold_display_tickers`` stellt —, oder, nur bei F, sie nennt das
# Thema der anderen (``_same_topic``). Das Zweite hält die
# Anzeigetafel-Kurzmeldungen großer Baustellen draußen, die F bisher zu Recht
# entfernte: Ohne diese Bedingung kämen „5: Betrieb ab Franz-Josefs-Bahnhof“,
# „12: …“ und „37: Betrieb ab Nußdorfer Straße“ neben
# „5/12/37/38/40/41/42: Gleisbauarbeiten“ jede Nacht neu nach vorn.
# „Silvesterlauf“ und „Silvesterpfad“ fasst heute ``deduplicate_fuzzy`` im
# Feed-Bau zusammen (``tests/test_feed_merge.py``).
#
# Eine Anzeigetafel-Kurzmeldung deckt nie eine ausführliche Störungsmeldung
# ab (Prüfung der verworfenen Meldungen, 2026-10-07). Ihr Titel ist die
# Ursache, und die steht auch im Titel der ausführlichen; nach Wörtern
# „deckten“ deshalb zwei Kurzmeldungen „10: Fahrtbehinderung wegen
# Polizeieinsatz“ und „60: Polizeieinsatz Betrieb ab Anschützgasse“ die
# Meldung „10, 60: Polizeieinsatz“ ab, und E entfernte sie samt
# „Linie 10: Betrieb nur zwischen Dornbach und Linzer Straße …
# Voraussichtliche Dauer: 09:50 Uhr“ (Rohdaten 2026-10-05 09:31 MESZ; der
# Feed zeigte „10“ und „60“ auf zwei Plätzen). Seit 04.10. traf das fünf
# Störungen (10/60, D/71, 2/12, 13A/14A, 11/O) und täglich die
# Baustellenmeldungen 25/26/27 und 46/49/52. Was Kurzmeldungen neben ihrer
# Störung zeigen, entscheidet der Feed-Bau (``_merge_wl_ticker_clusters``).


_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)


def _lead_line(desc: str) -> str:
    """Worum es in *desc* geht: die ``<h2>``-Überschrift oder die erste Zeile.

    Bei einer Anzeigetafel-Kurzmeldung steht dort die Ursache
    (``"Gleisbauarbeiten\nBetrieb ab Franz-Josefs-Bahnhof"``), bei einer
    ausführlichen Meldung die Überschrift.
    """
    match = _H2_RE.search(desc or "")
    if match:
        return match.group(1)
    return (desc or "").split("\n", 1)[0]


_STOP_NOTICE_RE = re.compile(
    r"^\s*Haltestellen(?:verlegung|auflassung)\b.*?\bHaltestelle:\s*(.{1,80}?)\s+(?:Von|Nach|Dauer|Ersatzlos)\b",
    re.IGNORECASE | re.DOTALL,
)


def _stop_of_notice(description: str) -> str | None:
    """The stop a WL relocation or closure notice is about, or ``None``.

    WL writes these notices in one form ("Haltestellenverlegung der Linie 3A
    in Richtung Oper, Karlsplatz / Haltestelle: Schellinggasse / Von: …").
    """
    match = _STOP_NOTICE_RE.search(_gate_text(description[:4000]))
    return " ".join(match.group(1).casefold().split()) if match else None


def _covers_stop_notice(item: dict[str, Any], others: Sequence[dict[str, Any]]) -> bool:
    """False if *item* is a stop notice that none of *others* is about.

    The title of a relocation or closure is just its stop ("3A: Oper,
    Karlsplatz"), and other notices name that stop as a direction ("in
    Richtung Oper, Karlsplatz"): by word count alone (E, F) the relocation
    of Schellinggasse removed the one of Oper, Karlsplatz, and "12A:
    Geibelgasse" the one of "12A, N8: Längenfeldgasse U" (raw data
    2026-10-04). Only a notice about the same stop covers a stop notice.
    """
    stop = item.get("_stop")
    if not stop:
        return True
    return any(other.get("_stop") == stop for other in others)


def _is_display_ticker(item: dict[str, Any]) -> bool:
    """True für eine Störung nur aus Anzeigetafel-Kurzmeldungen (keine WL-Nummer)."""
    return item.get("category") == "Störung" and not item.get("_wl_ids")


def _may_cover(cover: dict[str, Any], item: dict[str, Any]) -> bool:
    """False, wenn *cover* eine Kurzmeldung und *item* eine ausführliche Meldung ist."""
    return not (item.get("_wl_ids") and _is_display_ticker(cover))


def _says_nothing_beyond(item: dict[str, Any], others: Sequence[dict[str, Any]]) -> bool:
    """True, wenn der Titel von *item* ganz in den Texten von *others* steht."""
    text = " ".join(str(other.get("_text", "")) for other in others)
    return _covered_by(item.get("_title_tokens") or frozenset(), text)


def _same_topic(item: dict[str, Any], other: dict[str, Any]) -> bool:
    """True, wenn *item* das Thema von *other* nennt.

    Thema sind die Wörter im Titel von *other* ohne Liniennummern, Zahlen
    und Wörter unter fünf Buchstaben (``Gleisbauarbeiten``,
    ``Regenbogenparade``); gesucht wird im Titel und in der ersten Zeile
    bzw. Überschrift von *item* (:func:`_lead_line`).
    """
    topic = {
        tok for tok in other.get("_title_tokens") or frozenset()
        if len(tok) >= 5 and tok.isalpha()
    }
    return bool(topic & (item.get("_lead_tokens") or frozenset()))


def _drop_covered_aggregates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """E) Meldung für mehrere Linien entfernen, wenn die Einzelmeldungen sie abdecken.

    Abdecken heißt: Jede ihrer Linien hat eine Einzelmeldung derselben
    Kategorie mit überlappendem Zeitraum, und diese Einzelmeldungen sagen
    zusammen alles, was ihr Titel sagt. Ohne den Kategorie-Schlüssel löschte
    ein Hinweis je Linie eine echte Störung; ohne die Zeitprüfung löschten
    zeitlich getrennte Einzelmeldungen ein Aggregat für einen dritten
    Zeitraum; ohne den Wortvergleich löschte „1: Rettungseinsatz“ neben
    „2: Falschparker“ die Meldung „1/2: Demonstration“. Eine
    Anzeigetafel-Kurzmeldung zählt für eine ausführliche Störung nicht als
    Einzelmeldung (:func:`_may_cover`).
    """
    singles: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) == 1:
            singles.setdefault((it.get("category"), next(iter(ls))), []).append(it)

    kept: list[dict[str, Any]] = []
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) >= 2:
            covering = [
                [
                    single
                    for single in singles.get((it.get("category"), ln), [])
                    if _may_cover(single, it)
                    and _intervals_overlap(
                        it.get("starts_at"), it.get("ends_at"),
                        single.get("starts_at"), single.get("ends_at"),
                    )
                ]
                for ln in ls
            ]
            flat = [single for group in covering for single in group]
            if all(covering) and _says_nothing_beyond(it, flat) and _covers_stop_notice(it, flat):
                continue
        kept.append(it)
    return kept


def _drop_covered_subsets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """F) Meldung entfernen, die eine Meldung für mehr Linien schon ganz enthält.

    Die andere Meldung trägt eine echte Obermenge der Linien, dieselbe
    Kategorie und einen überlappenden Zeitraum, und entweder enthält ihr
    Text jedes Wort aus dem Titel der kleineren (:func:`_says_nothing_beyond`)
    oder die kleinere nennt ihr Thema (:func:`_same_topic`). Das Zweite
    trifft die Anzeigetafel-Kurzmeldungen einer Baustelle
    („37: Betrieb ab Nußdorfer Straße“ mit „Gleisbauarbeiten“ in der ersten
    Zeile neben „5/12/37/38/40/41/42: Gleisbauarbeiten“): Sie sagen mit
    anderen Worten, was die Baustellenmeldung ausführlich sagt. Eine
    Kurzmeldung entfernt keine ausführliche Störung (:func:`_may_cover`).
    """
    removed: set[int] = set()
    for i, item_a in enumerate(items):
        lines_a = item_a.get("_lines_set") or set()
        if not lines_a:
            continue
        for j, item_b in enumerate(items):
            if i == j or j in removed:
                continue
            lines_b = item_b.get("_lines_set") or set()
            if not (lines_a < lines_b and item_a.get("category") == item_b.get("category")):
                continue
            if not _may_cover(item_b, item_a):
                continue
            if not _intervals_overlap(
                item_a.get("starts_at"), item_a.get("ends_at"),
                item_b.get("starts_at"), item_b.get("ends_at"),
            ):
                continue
            if (
                _says_nothing_beyond(item_a, [item_b]) or _same_topic(item_a, item_b)
            ) and _covers_stop_notice(item_a, [item_b]):
                removed.add(i)
                break
    return [it for i, it in enumerate(items) if i not in removed]


# ---------------- Public API ----------------

def fetch_events(timeout: int = 20) -> list[dict[str, Any]]:
    # Security: clamp ``timeout`` to ``MAX_WL_FETCH_TIMEOUT`` to defeat the
    # Slowloris vector documented at the constant declaration above. Without
    # the cap a caller passing ``timeout=99999`` would let a sluggish or
    # attacker-controlled upstream peer stall the cron for ~28 hours per fetch.
    # ``min(...)`` instead of ``if ...:`` to avoid bumping the McCabe complexity
    # of this already-baselined function (``.c901-baseline.txt``: ``fetch_events
    # 51``). Same TIGHTEN-only contract as the ``if`` form: legitimate values
    # below the cap pass through unchanged.
    timeout = min(timeout, MAX_WL_FETCH_TIMEOUT)
    now = datetime.now(UTC)
    reset_corrections()
    raw_capture.reset_drops("wl")
    _fallback_parts.clear()
    raw: list[dict[str, Any]] = []

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as session:
        session.headers.update(WL_SESSION_HEADERS)
        # A) TrafficInfos (Störungen)
        infos = list(_fetch_traffic_infos(timeout=timeout, session=session))
        stale = wl_resolved.stale_tickers(infos, _is_finished)
        for ti in infos:
            attrs = _coerce_dict(ti.get("attributes"))
            drop_reason = _status_drop_reason(ti, stale)
            if drop_reason:
                raw_capture.note_drop("wl", drop_reason, ti.get("title") or ti.get("name"))
                continue

            title_raw = str(ti.get("title") or ti.get("name") or "Meldung").strip()
            # Neither an empty nor a lines-only title reaches the feed (see
            # ``_title_or_heading``).
            desc_raw = str(ti.get("description") or "").strip()
            title = _title_or_heading(title_raw, desc_raw)
            # Do NOT strip HTML here, we need to preserve links (Task 3)
            desc = desc_raw
            # Facility check is TITLE-driven (mirrors the ÖBB sibling
            # _is_facility_or_weather_only, which inspects only the title): a
            # facility word in the free-text DESCRIPTION is a side-mention and
            # must NOT drop a genuine line disruption (e.g. "U4: Streckensperre"
            # whose description also notes an out-of-service lift). Only a
            # facility-only TITLE drops the item.
            facility = _facility_drop_reason(title_raw)
            if facility:
                raw_capture.note_drop("wl", facility, title_raw)
                continue

            tinfo = _coerce_dict(ti.get("time"))
            start = _iso(tinfo.get("start")) or _best_ts(ti)
            end = _effective_end(desc_raw, _iso(tinfo.get("end")), start)

            real_start = _effective_start(title_raw, desc_raw, start, end, now)

            # We check activity based on the API start time (publication/validity start),
            # NOT the event start time extracted from the title.
            # This ensures advance notices (Vorankündigungen) are shown.
            if not _is_active(start, end, now):
                raw_capture.note_drop("wl", "außerhalb des Zeitraums", title_raw)
                continue

            blob_for_relevance = _gate_text(title_raw, desc_raw)
            if KW_EXCLUDE.search(blob_for_relevance) and not KW_RESTRICTION.search(
                blob_for_relevance
            ):
                raw_capture.note_drop("wl", "Ausschluss-Stichwort ohne Einschränkung", title_raw)
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                # Fallback: aus Titeltext (inkl. „Rufbus Nxx“, aber ohne Datum/Zeit/Adresse)
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            for k in ("status", "state", "station", "location", "reason", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            # stabile Identity für first_seen
            topic_key = _topic_key_from_title(title_raw)
            identity = _wl_identity("störung", line_pairs, real_start, topic_key)

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Störung",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": topic_key,
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,  # Publication/Creation date remains original
                    "starts_at": real_start, # Effective start date (for calendar)
                    "ends_at": end,
                    "_identity": identity,
                    "wl_ids": _incident_ids(ti),
                }
            )

        # B) News/Hinweise
        for poi in _fetch_news(timeout=timeout, session=session):
            attrs = _coerce_dict(poi.get("attributes"))
            if _is_inactive_status(
                poi.get("status"), attrs.get("status"), attrs.get("state")
            ):
                raw_capture.note_drop("wl", "Status inaktiv", poi.get("title") or poi.get("name"))
                continue

            # Mirror the TrafficInfo branch's title fallback so a POI
            # with empty ``title`` but a populated ``name`` (real WL
            # News payload shape) doesn't collapse to the literal
            # placeholder "Hinweis".
            title_raw = str(
                poi.get("title") or poi.get("name") or "Hinweis"
            ).strip()
            # Neither an empty nor a lines-only title reaches the feed (see
            # ``_title_or_heading``).
            desc_raw = str(poi.get("description") or "").strip()
            title = _title_or_heading(title_raw, desc_raw)
            # Do NOT strip HTML here, we need to preserve links (Task 3)
            desc = desc_raw
            # Title-driven facility check (see the trafficInfo branch above):
            # a facility word in the description / subtitle is only a
            # side-mention and must not drop a genuine line disruption.
            facility = _facility_drop_reason(title_raw)
            if facility:
                raw_capture.note_drop("wl", facility, title_raw)
                continue

            tinfo = _coerce_dict(poi.get("time"))
            start = _iso(tinfo.get("start")) or _best_ts(poi)
            end = _effective_end(desc_raw, _iso(tinfo.get("end")), start)

            real_start = _effective_start(title_raw, desc_raw, start, end, now)

            if not _is_active(start, end, now):
                raw_capture.note_drop("wl", "außerhalb des Zeitraums", title_raw)
                continue

            text_for_filter = _gate_text(
                title_raw,
                str(poi.get("subtitle") or ""),
                desc_raw,
                str(attrs.get("status") or ""),
                str(attrs.get("state") or ""),
            )
            if not KW_RESTRICTION.search(text_for_filter):
                raw_capture.note_drop("wl", "kein Einschränkungs-Stichwort", title_raw)
                continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            if poi.get("subtitle"):
                extras.append(str(poi["subtitle"]).strip())
            for k in ("station", "location", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            topic_key = _topic_key_from_title(title_raw)
            identity = _wl_identity("hinweis", line_pairs, real_start, topic_key)

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Hinweis",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": topic_key,
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,
                    "starts_at": real_start,
                    "ends_at": end,
                    "_identity": identity,
                }
            )

    # C) Bündelung: LINIEN-SET + TOPIC
    buckets: dict[str, dict[str, Any]] = {}
    for ev in raw:
        line_toks_sorted = ",".join(sorted(_line_tokens_from_pairs(ev["lines_pairs"])))
        key = make_guid(
            "wl",
            ev["category"],
            ev["topic_key"],
            line_toks_sorted,
        )
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": ev["source"],
                "category": ev["category"],
                "title": ev["title"],
                "title_core": ev.get("title_core", ""),
                "topic_key": ev["topic_key"],
                "desc_base": ev["desc"],
                "extras": list(ev["extras"]),
                "lines_pairs": list(ev["lines_pairs"]),  # geordnete Paare
                "stop_names": set(ev["stop_names"]),
                "pubDate": ev["pubDate"],
                "starts_at": ev["starts_at"],
                "ends_at": ev["ends_at"],
                "_identity": ev["_identity"],  # stabil weiterreichen
                "wl_ids": set(ev.get("wl_ids") or ()),
            }
        else:
            current_title = b["title"]
            current_core = b.get("title_core", "")
            current_desc = b.get("desc_base", "")

            base_score = _description_info_score(
                current_desc,
                title=current_title,
                stop_names=b["stop_names"],
                extras=b["extras"],
            )
            candidate_score = _description_info_score(
                ev.get("desc", ""),
                title=ev["title"],
                stop_names=ev["stop_names"],
                extras=ev["extras"],
            )

            if _title_quality_key(ev["title"], ev.get("title_core", "")) > _title_quality_key(
                current_title, current_core
            ):
                b["title"] = ev["title"]
                b["title_core"] = ev.get("title_core", "")

            if candidate_score > base_score:
                b["desc_base"] = ev.get("desc", "")

            b["lines_pairs"] = _merge_line_pairs(b["lines_pairs"], ev["lines_pairs"])
            b["stop_names"].update(ev["stop_names"])
            b["wl_ids"].update(ev.get("wl_ids") or ())

            # Use the earliest pubDate for the bucket (to show when first detected/announced)
            if ev["pubDate"] and (
                not b["pubDate"] or ev["pubDate"] < b["pubDate"]
            ):
                b["pubDate"] = ev["pubDate"]

            # A notice (Hinweis) spans its phases and starts at the earliest
            # of them, like the end below and the fuzzy merge
            # (``_merge_validity``). The latest start turned
            # "11: Gleisbauarbeiten" (01.09.–15.10.) plus "… ab 20.10.2026"
            # into works that only begin on 20.10. while the first phase
            # was running. Disruptions keep the latest start: WL reuses old
            # display tickers for a new incident, and their stale start
            # would date it back (docs/architecture.md, 27.09.). An unknown
            # start never erases a known one.
            es, bs = ev["starts_at"], b["starts_at"]
            if es and (
                not bs or (es < bs if b["category"] == "Hinweis" else es > bs)
            ):
                b["starts_at"] = es

            be, ee = b["ends_at"], ev["ends_at"]
            b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    if len(_fallback_parts) >= 2:
        # Beide Listen aus der letzten guten Antwort: Das ist ein Ausfall der
        # ganzen Quelle, der Cache bleibt stehen (Begründung oben).
        raise SourceIncompleteError("WL: beide Listen ohne brauchbare Antwort")
    if not _fallback_parts:
        # Die Verwürfe einer alten Antwort sagen nichts über diesen Abruf.
        raw_capture.write_drops("wl")

    # Anzeigetafel-Kurzmeldungen in die ausführliche Meldung derselben
    # Störung übernehmen (Begründung am Helfer).
    _fold_display_tickers(buckets)

    # D) Finale Items mit Linien-Präfix im Titel
    items: list[dict[str, Any]] = []
    for b in buckets.values():
        lines_disp = _line_display_from_pairs(b["lines_pairs"])
        lines_tok = set(_line_tokens_from_pairs(b["lines_pairs"]))

        base_title = b["title"]
        context_suffix, _extras_for_context = _build_context_suffix(
            b, base_title, lines_disp
        )
        title_with_lines = _ensure_line_prefix(base_title, lines_disp)
        if context_suffix:
            title_with_lines = (
                f"{title_with_lines} – {context_suffix}" if title_with_lines else context_suffix
            )

        # Anzahl Halte ins Titelende
        halt_cnt = len(b["stop_names"])
        if halt_cnt > 0 and not _HALT_SUFFIX_RE.search(title_with_lines):
            title_with_lines += f" ({halt_cnt} Halt{'e' if halt_cnt != 1 else ''})"

        title_final = re.sub(r"[<>«»‹›]+", "", title_with_lines).strip()

        # Beschreibung aufbauen (ohne „Linien: …“ in extras)
        desc = b["desc_base"]

        if b["stop_names"]:
            stops_formatted = ", ".join(sorted(b["stop_names"]))
            desc += f" | Haltestelle: {stops_formatted}"
        elif b["extras"]:
            locations = [
                x for x in b["extras"]
                if x.lower().startswith(("station:", "location:")) or ":" not in x
            ]
            if locations:
                locations_formatted = " / ".join(locations)
                desc += f" | {locations_formatted}"

        # Keine Extras oder Haltestellen mehr anhängen (Task: Strict 2-line Layout)
        # Keine aggressive HTML-Entfernung mehr (Task: Fix h2Artifacts)
        desc = re.sub(r"\s{2,}", " ", desc).strip()

        guid = make_guid(
            "wl",
            b["category"],
            b["topic_key"],
            ",".join(sorted(lines_tok)),
        )
        items.append(
            {
                "source": b["source"],
                "category": b["category"],
                "title": title_final,  # plain text
                "description": desc,  # plain text
                "link": f"{WL_BASE}",
                "guid": guid,
                "pubDate": b["pubDate"],  # None erlaubt
                "starts_at": b["starts_at"],
                "ends_at": b["ends_at"],
                "_identity": b["_identity"],  # stabil für first_seen
                **({"_wl_ids": sorted(b["wl_ids"])} if b.get("wl_ids") else {}),
                "_lines_set": lines_tok,  # für Sammel-vs.-Einzel
                # Für E) und F): was die Meldung über ihre Linien hinaus sagt,
                # und alles, was sie sagt.
                "_title_tokens": _content_tokens(b["title"])
                - {tok.casefold() for tok in lines_tok},
                "_text": f"{title_final} {desc}",
                "_lead_tokens": _content_tokens(f"{b['title']} {_lead_line(desc)}"),
                "_stop": _stop_of_notice(desc),
            }
        )

    # E) Sammel-vs.-Einzel und F) Subset-Bereinigung (Begründung an den Helfern)
    filtered = _drop_covered_subsets(_drop_covered_aggregates(items))

    # Aufräumen interner Felder + Sortierung
    for it in filtered:
        it.pop("_lines_set", None)
        it.pop("_title_tokens", None)
        it.pop("_text", None)
        it.pop("_lead_tokens", None)
        it.pop("_stop", None)

    filtered.sort(
        key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, x["guid"])
    )
    log.info("WL: %d Items nach Filter/Dedupe", len(filtered))
    return filtered


__all__ = ["SourceIncompleteError", "fallback_parts", "fetch_events"]
