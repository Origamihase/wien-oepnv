from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import secrets
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    ThreadPoolExecutor,
    TimeoutError,
    wait,
)
from datetime import datetime, timedelta, UTC
import requests
from dateutil import parser
from email.utils import format_datetime
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any, cast, NamedTuple
from collections.abc import Sequence
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from .feed_types import FeedItem
from .feed import config as feed_config
from .feed.merge import deduplicate_fuzzy
from .feed.logging import configure_logging
from .feed.providers import (
    iter_providers,
    load_provider_plugins,
    provider_statuses,
    register_provider,
    resolve_provider_name,
)
from .feed.reporting import (
    DuplicateSummary,
    FeedHealthMetrics,
    RunReport,
    clean_message,
    write_feed_health_report,
    write_feed_health_json,
)

from .utils.cache import (
    cache_modified_at,
    read_cache as _core_read_cache,
    register_cache_alert_hook,
)
from .utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    atomic_write,
)
from .utils.http import validate_http_url
from .utils.locking import file_lock
from .utils.logging import sanitize_log_arg
from .utils.stats import append_disruption_row, extract_location_name
from .utils.text import html_to_text, truncate_html


__all__ = ["RunReport", "ThreadPoolExecutor", "feed_config"]


# Register namespaces globally for thread-safe XML generation
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("ext", "https://wien-oepnv.example/schema")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")

log = logging.getLogger("build_feed")


# Expose validate_path for tests that patch it or use it
validate_path = feed_config.validate_path

_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def refresh_from_env() -> None:
    """Refresh configuration values and reload provider plugins."""
    feed_config.refresh_from_env()
    load_provider_plugins(force=True)


read_cache = _core_read_cache


# German prepositions/connectors that *require* a following object —
# when a WL title ends with one of these alone (no object), the WL
# source data was truncated mid-sentence and the meldung is useless.
# Real cache item ``41E: Ersatzbus 41E hält gegenüber`` is the
# canonical example — the user reads "stops opposite [nothing]".
_INCOMPLETE_TITLE_TAIL_RE = re.compile(
    r"\b(?:bei|gegen[üu]ber|an|in|vor|nach|zu|über|ueber|am|im|zur|zum)\s*$",
    re.IGNORECASE,
)

# WL Störung items must carry a line prefix (``U6:``, ``41E:``,
# ``9/40/41/42:``); without it the user can't tell which line is
# affected and the meldung is useless. Real cache items
# ``Verkehrsunfall Betrieb ab Nordbrücke`` and ``Fahrtbehinderung
# wegen Verkehrsunfall`` carry ``_identity='wl|störung|L=|D=...'``
# (empty line set) and a title without a leading line marker.
_WL_LINE_PREFIX_RE = re.compile(r"^[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\s*:\s+\S")


def _post_filter_wl(items: list[Any]) -> list[Any]:
    """Defence-in-depth: normalise / drop bad WL items loaded from cache.

    The WL cache is only refreshed periodically, so:

    1. Title-formatting fixes (e.g. newline collapse per Bug 12A) need
       to be re-applied at cache-read time.
    2. WL itself sometimes serves data that's truncated mid-sentence —
       ``Ersatzbus 41E hält gegenüber`` (with no location after
       ``gegenüber``) is meaningless. Such items are dropped.
    3. WL Störung items occasionally arrive without any
       ``relatedLines`` AND without a line code in the title — for
       example ``Verkehrsunfall Betrieb ab Nordbrücke``. The line
       prefix is the key signal for transit-line attribution; without
       it the user can't tell which line is affected, so we drop
       these too.
    """
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = item.get("title")
        if isinstance(title, str) and title:
            cleaned = re.sub(r"\s+", " ", title).strip()
            if cleaned != title:
                item = dict(item)
                item["title"] = cleaned
            # Drop items whose visible title ends with a preposition
            # that demands an object — the WL source is clearly
            # incomplete and the user gets no useful information.
            title_body = cleaned.split(":", 1)[-1].strip() if ":" in cleaned else cleaned
            if _INCOMPLETE_TITLE_TAIL_RE.search(title_body):
                continue
            # Drop WL Störung items without a line prefix — WL didn't
            # provide a line code and the user can't disambiguate the
            # affected line from the title alone.
            category = item.get("category")
            if category == "Störung" and not _WL_LINE_PREFIX_RE.match(cleaned):
                continue
        out.append(item)
    return out


def read_cache_wl() -> list[Any]:
    return _post_filter_wl(list(read_cache("wl") or []))


def _post_filter_oebb(items: list[Any]) -> list[Any]:
    """Re-apply the ÖBB relevance filter and re-derive titles for cached items.

    The cache is only refreshed by `update_oebb_cache.py`, so a filter
    update doesn't reach the feed until the next cache refresh. Without
    this defence-in-depth re-check the feed can carry items that the
    *current* spec considers irrelevant (e.g. Wien↔Distant routes that
    slipped through an older filter version).

    We also re-derive the title via ``_apply_route_title`` so a title-
    formatting improvement (such as the multi-route chain collapse or
    the affected-line-prefix tightening) reaches the feed even when the
    cache still holds an older rendering. The original title is left in
    place if the re-derived version is empty or identical.

    Items without a title are passed through unchanged so test fixtures
    and other generic dictionaries aren't accidentally dropped.
    """
    from .providers.oebb import (  # local import: avoids circular at module load
        _apply_route_title,
        _is_relevant,
    )

    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        if not title and not description:
            # Stub / metadata item — leave it alone.
            out.append(item)
            continue
        if not _is_relevant(title, description):
            continue
        rederived = _apply_route_title(title, description)
        if rederived and rederived != title:
            item = dict(item)
            item["title"] = rederived
        out.append(item)
    return out


def read_cache_oebb() -> list[Any]:
    return _post_filter_oebb(list(read_cache("oebb") or []))


def read_cache_baustellen() -> list[Any]:
    return list(read_cache("baustellen") or [])


# Stammstrecke feed events are now derived from the CSV ledger
# ``data/stats/stammstrecke_<YYYY>.csv`` rather than a JSON cache —
# see :mod:`src.feed.stammstrecke` for the operational contract
# (1-hour feed window, 6-hour episode lookback, 9-minute threshold).
# Replaced the JSON cache 2026-05-09 (PR follow-up to #1397) so the
# README dashboard and the RSS feed share a single source of truth.
def read_cache_stammstrecke() -> list[Any]:
    """Compute Stammstrecke feed events from the CSV ledger.

    Delegates to :func:`src.feed.stammstrecke.compute_stammstrecke_events`.
    A missing / unreadable / empty ledger naturally yields zero
    events; the feed build then omits the Stammstrecke entry rather
    than failing.
    """

    from .feed import stammstrecke as stammstrecke_events

    return cast(list[Any], stammstrecke_events.compute_stammstrecke_events())


DEFAULT_PROVIDERS: tuple[tuple[str, Any], ...] = (
    ("WL_ENABLE", read_cache_wl),
    ("OEBB_ENABLE", read_cache_oebb),
    ("BAUSTELLEN_ENABLE", read_cache_baustellen),
    # VOR is intentionally absent — VOR API access is scoped to the
    # S-Bahn-Stammstrecke delay monitor only (operator policy
    # 2026-05-11). Stammstrecke feed entries derive from the CSV
    # ledger via :mod:`src.feed.stammstrecke`, not from a VOR
    # disruption cache.
    ("STAMMSTRECKE_ENABLE", read_cache_stammstrecke),
)

PROVIDERS: list[tuple[str, Any]] = list(DEFAULT_PROVIDERS)

_PROVIDERS_INITIALIZED = False

# Ensure plugins are loaded exactly once per process startup AFTER env vars are configured
# and BEFORE the main provider registration loop.
load_provider_plugins()

def reset_module_state() -> None:
    """Test helper to reset the module-level initialization state."""
    global _PROVIDERS_INITIALIZED
    global PROVIDERS
    _PROVIDERS_INITIALIZED = False
    PROVIDERS.clear()
    PROVIDERS.extend(DEFAULT_PROVIDERS)

def init_providers() -> None:
    global _PROVIDERS_INITIALIZED
    if _PROVIDERS_INITIALIZED:
        return
    for env_name, loader in PROVIDERS:
        register_provider(env_name, loader, cache_key=resolve_provider_name(loader, env_name))
    _PROVIDERS_INITIALIZED = True


def _provider_display_name(fetch: Any, env: str | None = None) -> str:
    """Resolve the display name for a provider based on its loader or env var."""
    return resolve_provider_name(fetch, env)


def _detect_stale_caches(report: RunReport, now: datetime) -> list[str]:
    """Record warnings for provider caches older than the configured threshold."""

    if feed_config.CACHE_MAX_AGE_HOURS <= 0:
        return []

    threshold = timedelta(hours=feed_config.CACHE_MAX_AGE_HOURS)
    stale_messages: list[str] = []

    for spec in iter_providers():
        loader = spec.loader
        cache_name = getattr(loader, "_provider_cache_name", None)
        if not cache_name:
            continue

        modified_at = cache_modified_at(str(cache_name))
        if modified_at is None:
            continue

        if modified_at.tzinfo is None:
            modified_at = modified_at.replace(tzinfo=UTC)

        age = now - modified_at
        if age <= threshold:
            continue

        hours = age.total_seconds() / 3600
        message = (
            f"Cache {cache_name}: zuletzt vor {hours:.1f}h aktualisiert "
            f"(Schwelle {feed_config.CACHE_MAX_AGE_HOURS}h)"
        )
        report.add_warning(message)
        stale_messages.append(message)

    return stale_messages


def _provider_statuses() -> list[tuple[str, bool]]:
    """Return a list of (name, enabled) tuples for all registered providers."""
    return provider_statuses()


def _log_startup_summary(statuses: list[tuple[str, bool]]) -> None:
    """Log the active configuration and enabled providers at startup."""
    enabled = sorted(name for name, is_enabled in statuses if is_enabled)
    disabled = sorted(name for name, is_enabled in statuses if not is_enabled)

    enabled_display = ", ".join(enabled) if enabled else "keine"
    log.info(
        "Starte Feed-Bau: %s aktiv (Timeout global=%ss, MaxItems=%d, Worker=%s)",
        enabled_display,
        feed_config.PROVIDER_TIMEOUT,
        feed_config.MAX_ITEMS,
        feed_config.PROVIDER_MAX_WORKERS or "auto",
    )
    if disabled:
        log.info("Deaktivierte Provider: %s", ", ".join(disabled))


def _validate_configuration(statuses: list[tuple[str, bool]]) -> None:
    """Check the runtime configuration for common issues (e.g. no providers active)."""
    enabled_count = sum(1 for _, is_enabled in statuses if is_enabled)
    if not statuses:
        log.warning("Keine Provider registriert – es werden keine Items gesammelt.")
    elif enabled_count == 0:
        log.error(
            "Alle Provider deaktiviert – Feed bleibt leer, bitte Konfiguration prüfen."
        )

    if feed_config.MAX_ITEMS == 0:
        log.warning("MAX_ITEMS ist 0 – der Feed wird ohne Einträge erzeugt.")
    if feed_config.FEED_TTL == 0:
        log.warning(
            "FEED_TTL ist 0 – Clients werten den Feed unmittelbar als abgelaufen."
        )
    if feed_config.PROVIDER_TIMEOUT == 0 and enabled_count:
        log.warning(
            "PROVIDER_TIMEOUT ist 0 – Netzwerkprovider haben keine Zeit für Antworten."
        )
    if feed_config.MAX_ITEM_AGE_DAYS > feed_config.ABSOLUTE_MAX_AGE_DAYS:
        log.warning(
            "MAX_ITEM_AGE_DAYS (%s) übersteigt ABSOLUTE_MAX_AGE_DAYS (%s) – ältere Items "
            "werden dennoch durch den absoluten Grenzwert verworfen.",
            feed_config.MAX_ITEM_AGE_DAYS,
            feed_config.ABSOLUTE_MAX_AGE_DAYS,
        )

# ---------------- Provider tuning ----------------

def _provider_env_slug(name: str) -> str:
    """Convert a provider name into a slug suitable for environment variables."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").upper()).strip("_")
    return slug or "PROVIDER"


def _read_optional_non_negative_int(env_name: str) -> int | None:
    """Read a non-negative integer from an environment variable."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except (TypeError, ValueError) as exc:
        # Security (Clear-Text-Logging Drift, src/utils/* round): the bound
        # ``exc`` text is rendered raw via ``%s`` — sanitise to defeat a
        # hostile env-injected value flowing through ``int()``'s error
        # message into the structured log line.
        #
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` was previously formatted via ``%r``; that conversion
        # leaves all 256 Variation Selectors (U+FE00-U+FE0F +
        # U+E0100-U+E01EF) in ``record.args`` and ``getMessage()``
        # verbatim. Route through ``sanitize_log_arg`` to strip the
        # canonical ``_INVISIBLE_DANGEROUS_RE`` union BEFORE the value
        # reaches caplog / non-SafeFormatter handlers.
        log.warning(
            "Ungültiger Wert für %s=%s – ignoriere Override (%s: %s)",
            env_name,
            sanitize_log_arg(raw),
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None
    if value < 0:
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``raw`` is the operator-controlled per-provider env override
        # (``PROVIDER_<SLUG>_MAX_AGE_DAYS`` etc.). Pre-fix the WARNING
        # interpolated it via ``%r`` — Python's repr() escapes most
        # attack bytes but lets all 256 Variation Selectors
        # (U+FE00-U+FE0F + U+E0100-U+E01EF) through verbatim into
        # ``record.args[1]`` and ``record.getMessage()``. Route through
        # ``sanitize_log_arg`` so the canonical
        # ``_INVISIBLE_DANGEROUS_RE`` strips them BEFORE the value
        # lands in caplog / non-SafeFormatter handlers (mirrors the
        # sibling defence on line 374 that already routes ``str(exc)``
        # through the canonical helper).
        log.warning(
            "Negativer Wert für %s=%s – ignoriere Override",
            env_name,
            sanitize_log_arg(raw),
        )
        return None
    return value


def _resolve_provider_override(candidates: list[str]) -> int | None:
    """Helper to resolve the first valid integer from a list of env var candidates."""
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        value = _read_optional_non_negative_int(candidate)
        if value is not None:
            return value
    return None


def _provider_timeout_override(
    fetch: Any, env: str | None, provider_name: str
) -> int | None:
    """Determine if a specific timeout override exists for this provider."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_timeout_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(provider_name)
    candidates.append(f"PROVIDER_TIMEOUT_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_TIMEOUT")
        candidates.append(f"PROVIDER_TIMEOUT_{base}")

    value = _resolve_provider_override(candidates)
    if value is None:
        return None
    # Security: clamp the per-provider override at the same Slowloris-defence
    # ceiling as the global PROVIDER_TIMEOUT. Without this cap an env override
    # such as PROVIDER_TIMEOUT_VOR=99999 would bypass feed_config.PROVIDER_TIMEOUT
    # (which is itself capped at MAX_PROVIDER_TIMEOUT) and let a sluggish or
    # attacker-controlled upstream peer stall a fetch for ~28 hours.
    return min(value, feed_config.MAX_PROVIDER_TIMEOUT)


def _provider_concurrency_key(fetch: Any, provider_name: str) -> str:
    """Return the key used to group providers for concurrency limits."""
    key = getattr(fetch, "_provider_concurrency_key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    return provider_name


def _provider_worker_limit(
    fetch: Any, env: str | None, provider_name: str, concurrency_key: str
) -> int | None:
    """Determine the maximum number of concurrent workers for this provider group."""
    candidates: list[str] = []
    custom_env = getattr(fetch, "_provider_max_workers_env", None)
    if isinstance(custom_env, str) and custom_env.strip():
        candidates.append(custom_env.strip())

    slug = _provider_env_slug(concurrency_key)
    candidates.append(f"PROVIDER_MAX_WORKERS_{slug}")

    if env:
        base = env.removesuffix("_ENABLE")
        candidates.append(f"{base}_MAX_WORKERS")

    return _resolve_provider_override(candidates)


def _fetch_supports_timeout(fetch: Any) -> bool:
    """Check if the fetch callable accepts a 'timeout' argument."""
    try:
        signature = inspect.signature(fetch)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "timeout":
            return True
    return False


def _call_fetch_with_timeout(
    fetch: Any, timeout: int | float | None, supports_timeout: bool
) -> Any:
    """Invoke the fetch callable, passing the timeout if supported."""
    if supports_timeout:
        try:
            return fetch(timeout=None if timeout is None else timeout)
        except TypeError:
            return fetch()
    return fetch()

# ---------------- Helpers ----------------

def _to_utc(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC.

    Strictly requires timezone-aware datetimes.
    """
    if dt.tzinfo is None:
        raise ValueError("Naive datetimes are banned. Must be timezone-aware.")

    if dt.tzinfo is UTC:
        return dt

    return dt.astimezone(UTC)

def _fmt_rfc2822(dt: datetime) -> str:
    """Format datetime as RFC-2822 string with Vienna offset."""
    if dt.tzinfo is None:
        raise ValueError("Naive datetimes are banned in _fmt_rfc2822.")

    try:
        # Convert to Vienna time for output
        local_dt = _to_utc(dt).astimezone(_VIENNA_TZ)
        return format_datetime(local_dt)
    except Exception:
        log.exception(
            "Konnte Datum %r nicht per format_datetime formatieren – nutze strftime-Fallback.",
            dt,
        )
        try:
            local_dt = dt.astimezone(_VIENNA_TZ)
        except Exception:
            local_dt = dt.astimezone(UTC)

        return local_dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def format_local_times(
    start: datetime | None, end: datetime | None
) -> str:
    """Format a time range (start, end) into a localized string (e.g. 'Seit 01.01.2023')."""
    start_local: datetime | None = None
    end_local: datetime | None = None

    if isinstance(start, datetime):
        start_local = _to_utc(start).astimezone(_VIENNA_TZ)
    if isinstance(end, datetime):
        end_local = _to_utc(end).astimezone(_VIENNA_TZ)

    if start_local and end_local and (end_local - start_local).days > 180:
        log.warning("Enddatum liegt mehr als 180 Tage nach Startdatum. Setze Enddatum auf None.")
        end_local = None

    today = datetime.now(_VIENNA_TZ)

    if start_local:
        if end_local:
            if end_local < start_local:
                log.warning("Enddatum liegt vor Startdatum")
                end_local = None
            elif start_local.date() == end_local.date():
                return f"Am {start_local:%d.%m.%Y}"
            else:
                return f"{start_local:%d.%m.%Y} – {end_local:%d.%m.%Y}"
        if start_local.date() > today.date():
            return f"Ab {start_local:%d.%m.%Y}"
        return f"Seit {start_local:%d.%m.%Y}"
    if end_local:
        return f"Bis {end_local:%d.%m.%Y}"
    return ""

# Entfernt XML-unerlaubte Kontrollzeichen (außer \t, \n, \r) PLUS the
# canonical BiDi / zero-width / Unicode line-terminator family that the
# project-wide invisible-character floor strips
# (:data:`src.utils.logging._INVISIBLE_DANGEROUS_RE` /
# :data:`src.utils.text._MARKDOWN_NORMALISE_UNSAFE_RE` /
# :data:`src.utils.stats._CSV_CONTROL_CHARS_RE`).
#
# This regex is the LAST sanitiser before every feed item title /
# description / time-line lands inside the public RSS XML at
# ``docs/feed.xml`` (served from
# ``https://origamihase.github.io/wien-oepnv/feed.xml``). Pre-2026-05-10
# the class covered only ASCII C0 (ex-TAB/LF/CR) + DEL — narrower than
# the canonical Trojan-Source / line-terminator union that the
# BiDi-Mark Drift family (Rounds 2-5 in ``.jules/sentinel.md``)
# consolidated as the project-wide floor. The drift opened a
# *Trojan-Source RSS* primitive on the public feed:
#
#  * **U+202E (RLO)** — a planted upstream title with a U+202E
#    payload survives all three
#    pre-fix defences (``_CONTROL_RE.sub("")`` does not match RLO,
#    ``_WHITESPACE_RE.sub(" ")`` only collapses Unicode whitespace
#    [RLO is not whitespace per :func:`str.isspace`], ``_cdata_content``
#    only escapes ``]]>`` / ElementTree XML escape only handles
#    ``<>&"``). The bytes land verbatim inside ``<title>`` of
#    ``docs/feed.xml`` and Unicode-aware feed readers (Feedly,
#    NetNewsWire, Inoreader, Vivaldi RSS) render the post-RLO segment
#    reversed visually. CVE-2021-42574 shape on a public artefact.
#  * **U+200B-U+200D (ZWSP/ZWNJ/ZWJ) / U+FEFF (BOM)** — invisible byte
#    insertions that hash to a different identity than the same title
#    without them. A hostile upstream can churn the dedup window
#    indefinitely with visually-identical "fresh" items.
#  * **U+2028 / U+2029 (LINE / PARAGRAPH SEPARATOR)** — some Unicode-
#    aware readers honour these as line breaks, splitting one title
#    into multiple visual lines (Feedly mobile honours U+2028 exactly
#    as ``\n``).
#  * **U+0085 (NEL)** + **U+0080-U+0084, U+0086-U+009F (C1 controls)**
#    — record terminators in several SIEM splitters and Markdown
#    consumers downstream from the published feed.
#
# The widening is **additive** — every character the pre-fix regex
# matched still matches post-fix (verified by
# ``test_control_re_preserves_existing_coverage``). TAB (``\x09``),
# LF (``\x0A``), CR (``\x0D``), and SPACE (``\x20``) remain unmatched
# (RSS allows them and the downstream ``_WHITESPACE_RE`` collapse
# normalises them). The Unicode escape form keeps Bandit B613 happy
# and mirrors the regex shape established by the 2026-05-10 CSV writer
# round in :data:`src.utils.stats._CSV_CONTROL_CHARS_RE`.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical _INVISIBLE_DANGEROUS_RE union to cover
# the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). Tag bytes survive pre-fix into the
# public RSS XML at docs/feed.xml - ElementTree XML serialisation
# does NOT escape supplementary-plane code points (they are valid
# Unicode characters, not XML metacharacters). The widening at this
# LAST-sanitiser-boundary stops the invisible smuggling primitive
# before it reaches every subscriber's RSS reader.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to cover
# U+180E (MONGOLIAN VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER,
# FUNCTION APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR,
# INVISIBLE PLUS). Pre-fix the public RSS feed at docs/feed.xml could
# carry these zero-width Cf primitives inside <title>/<description>/
# <link>; they survive into every subscriber's reader as
# steganography / prompt-injection smuggling primitives. The
# U+2060..U+2069 expansion folds in the existing BiDi-isolate band;
# reserved U+2065 has no defined meaning so the additive strip is
# safe. Inventory invariant pinned by
# ``tests/test_sentinel_zero_width_invisible_drift.py``.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` to cover the
# remaining 13 Unicode Cf-class bands (44 code points): U+00AD SOFT
# HYPHEN, U+0600..U+0605 Arabic prefix marks, U+06DD, U+070F,
# U+0890..U+0891, U+08E2, U+206A..U+206F deprecated BiDi controls
# (folds the existing U+2060..U+2069 band into U+2060..U+206F),
# U+FFF9..U+FFFB INTERLINEAR ANNOTATION, U+110BD/U+110CD KAITHI,
# U+13430..U+13438 EGYPTIAN HIEROGLYPH, U+1BCA0..U+1BCA3 SHORTHAND
# FORMAT, and U+1D173..U+1D17A MUSICAL SYMBOL formatting. Pre-fix
# Cf bytes survived into the public RSS feed at docs/feed.xml -
# ElementTree XML serialisation does not escape supplementary-plane
# code points (they are valid Unicode characters, not XML
# metacharacters). SOFT HYPHEN especially renders zero-width
# unconditionally so a planted "Verspaetung<U+00AD>U6 evil"
# title reaches every subscriber's RSS reader visually identical
# to the legitimate text but byte-distinct downstream.
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff"
    r"\ufe00-\ufe0f\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)

# Prefix pattern for line identifiers like "U1/U2: "
_LINE_TOKEN_RE = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3})$")

_LINE_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z0-9]+\s*(?:/\s*[A-Za-z0-9]+){0,20})\s*:\s*"
)

_ELLIPSIS = " …"
_SENTENCE_END_RE = re.compile(r"[.!?…](?=\s|$)")

# _WHITESPACE_RE captures all whitespace including newlines (\n).
# _WHITESPACE_CLEANUP_RE only matches horizontal whitespace (spaces, tabs, etc.) to preserve intended line breaks.
_WHITESPACE_RE = re.compile(r"\s+")
_WHITESPACE_CLEANUP_RE = re.compile(r"[ \t\r\f\v]+")

def _sanitize_text(s: str) -> str:
    return _CONTROL_RE.sub("", s or "")

def _parse_lines_from_title(title: str) -> list[str]:
    m = _LINE_PREFIX_RE.match(title or "")
    if not m:
        return []

    tokens: list[str] = []
    for raw in m.group(1).split("/"):
        token = raw.strip()
        if not token:
            continue
        normalized = token.upper()
        if _LINE_TOKEN_RE.match(normalized):
            tokens.append(normalized)
    return tokens

def _ymd_or_none(dt: datetime | None) -> str:
    if isinstance(dt, datetime):
        return _to_utc(dt).date().isoformat()
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    """Parse ISO8601 timestamps (incl. ``Z`` suffix and compact offsets).

    Treats naive timestamps as Vienna local time.
    """

    if isinstance(value, datetime):
        if value.tzinfo:
            return value
        return value.replace(tzinfo=_VIENNA_TZ)

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None

        try:
            parsed = parser.isoparse(candidate)
            if parsed.tzinfo is None:
                # Assume Vienna time for naive strings (e.g. from legacy cache)
                parsed = parsed.replace(tzinfo=_VIENNA_TZ)
            return cast('datetime | None', parsed)
        except (ValueError, parser.ParserError) as exc:
            # Security (Clear-Text-Logging Drift): ``parser.ParserError``
            # embeds the original input string in its message —
            # upstream-controlled bytes flow into the log line via
            # ``str(exc)``.  ``%r`` already escapes ``value`` itself.
            log.debug(
                "Datetime-Parsing fehlgeschlagen für %r (%s)",
                value,
                sanitize_log_arg(str(exc)),
            )

    return None


def _coerce_datetime_field(it: dict[str, Any], field: str) -> datetime | None:
    value = it.get(field)
    if value is None:
        return None

    parsed = _parse_datetime(value)
    if parsed is None:
        if isinstance(value, str):
            log.warning("%s Parsefehler: %r", field, value)
        it[field] = None
        return None

    it[field] = parsed
    return parsed


def _normalize_item_datetimes(
    items: list[Any],
    fields: tuple[str, ...] = ("pubDate", "starts_at", "ends_at"),
) -> list[Any]:
    for item in items:
        if not isinstance(item, dict):
            continue
        for field_name in fields:
            _coerce_datetime_field(item, field_name)
    return items


# ---------------- State (first_seen) ----------------


# Security: defense-in-depth byte-size cap on the on-disk state file.
# The file is a small JSON object (one entry per first_seen item, each
# ~80 bytes) — production state.json is ~40 KiB. The depth-bomb catch
# tuple ``except (FileNotFoundError, json.JSONDecodeError)`` covers the
# deeply-nested attack via ``RecursionError`` (re-raises into the broad
# ``except Exception`` below), but a wide-but-flat attack such as
# ``[0]*50_000_000`` slips past both handlers entirely:
#  * ``json.load`` does NOT raise ``RecursionError`` on a flat list
#    regardless of length.
#  * ``handle.read()`` (called internally by ``json.load(handle)``)
#    buffers the whole file before parsing — a 1 GiB file allocates a
#    1 GiB string plus ~5 GiB worth of object overhead.
#  * ``MemoryError`` is a ``BaseException`` subclass — it is NOT caught
#    by any ``except Exception`` handler, so the orchestrator crashes
#    BEFORE any provider runs and the feed-build cron leaves partial
#    state with no recovery path.
# Threat model mirrors ``MAX_CACHE_FILE_BYTES`` in ``src/utils/cache.py``:
# compromised CI runner / corrupted previous run / partial flush + power
# loss plants a multi-MiB-to-multi-GiB file at ``data/first_seen.json``.
# 50 MiB is ~1200x the production state file and bounds the worst-case
# parse cost well below any cron runner's standard 1 GiB cgroup limit.
MAX_STATE_FILE_BYTES = 50 * 1024 * 1024


def _load_state() -> dict[str, dict[str, Any]]:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    try:
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=False):
                # Security: byte-size cap (see MAX_STATE_FILE_BYTES)
                # defeats the wide-but-flat size-bomb attack that the
                # depth-bomb / json.JSONDecodeError catch below does NOT
                # cover. Open first, then ``os.fstat`` — closes the
                # TOCTOU between ``stat`` and ``open`` that lets a
                # parallel ``_save_state`` (atomic_write rename) or a
                # symlink swap bypass the cap.
                # ``read(MAX_STATE_FILE_BYTES + 1)`` defends against
                # zero-st_size special files.
                # Security (Path-Log Sibling Drift, sibling of PR #1456):
                # ``feed_config.STATE_FILE`` is operator-controlled via
                # the ``STATE_PATH`` environment variable. Pre-fix the
                # WARNING log lines below interpolated ``path`` verbatim
                # via the bare ``%s`` format spec; a hostile path name
                # (Trojan-Source RLO, zero-width, 8-bit C1, Tag block,
                # Variation Selectors, newline log-forgery, ANSI ESC)
                # flowed into the operator log + the public
                # ``docs/feed_health.json`` artefact + the GitHub-Issue
                # auto-submission. Post-fix the SHA-256 fingerprint
                # (truncated to 12 hex chars, Trojan-Source-clean,
                # CodeQL-recognised barrier) replaces the path bytes
                # at the interpolation boundary. Mirrors the canonical
                # fix shape pinned in :func:`src.utils.files.read_capped_json`.
                path_fingerprint = hashlib.sha256(
                    str(path).encode("utf-8", errors="replace")
                ).hexdigest()[:12]
                with path.open("rb") as f:
                    if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] ist zu groß "
                            "(> %d Bytes); starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
                    if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                        log.warning(
                            "State-Datei [path-sha256=%s] überschreitet "
                            "%d Bytes beim Lesen; starte mit leerem State.",
                            path_fingerprint, MAX_STATE_FILE_BYTES,
                        )
                        return {}
                    # Security (reader-side non-finite literal defence,
                    # symmetric to the Round 1488 writer-side
                    # ``allow_nan=False`` pin at ``_save_state``). A
                    # planted ``NaN`` / ``Infinity`` / ``-Infinity`` /
                    # ``1e1000`` in ``data/first_seen.json`` (compromised
                    # CI runner, parallel orchestrator atomic state swap,
                    # partial flush + power loss, hostile PR landing a
                    # tampered fixture) would otherwise propagate silently
                    # as ``float('nan')`` / ``float('inf')`` through the
                    # state dict — every ``first_seen`` comparison against
                    # ``retention_cutoff`` (``fs_utc < retention_cutoff``)
                    # silently misbehaves on NaN (``False`` for every
                    # comparison) AND the round-trip back to
                    # ``_save_state`` hits the ``allow_nan=False`` pin
                    # and crashes the cron mid-write. The hooks raise
                    # ``json.JSONDecodeError`` which the ``except
                    # (FileNotFoundError, json.JSONDecodeError)`` handler
                    # below catches — fall through to the empty-state
                    # recovery path, consistent with every other
                    # corrupt-state recovery in this loader.
                    data = json.loads(
                        raw_bytes,
                        parse_constant=_reject_non_finite_constant,
                        parse_float=_reject_non_finite_float,
                    )
        data = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # may surface third-party / custom-subclass exceptions whose
        # ``__str__`` carries control bytes.  Sanitise.
        log.warning(
            "State laden fehlgeschlagen (%s) – starte leer.",
            sanitize_log_arg(str(e)),
        )
        return {}

    retention_cutoff: datetime | None = None
    if feed_config.STATE_RETENTION_DAYS > 0:
        now_utc = _to_utc(datetime.now(UTC))
        retention_cutoff = now_utc - timedelta(days=feed_config.STATE_RETENTION_DAYS)

    out: dict[str, dict[str, Any]] = {}
    for ident, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            raw_first_seen = entry.get("first_seen", "")
            fs_dt = datetime.fromisoformat(str(raw_first_seen))
            if fs_dt.tzinfo is None:
                fs_dt = fs_dt.replace(tzinfo=UTC)
            fs_utc = _to_utc(fs_dt)
        except Exception:
            log.warning(
                "State-Eintrag %s hat unparsebares first_seen: %r", ident, entry.get("first_seen")
            )
            fs_dt = datetime.now(UTC)
            fs_utc = _to_utc(fs_dt)

        if retention_cutoff and fs_utc < retention_cutoff:
            log.debug(
                "State-Eintrag %s älter als %s Tage – entferne Eintrag.",
                ident,
                feed_config.STATE_RETENTION_DAYS,
            )
            continue

        new_entry = dict(entry)
        new_entry["first_seen"] = fs_utc.isoformat()
        out[ident] = new_entry
    return out


def _read_state_capped(path: Path) -> dict[str, dict[str, Any]]:
    """Read existing state under the byte-size cap, returning ``{}`` on
    any failure mode (missing/oversized/invalid).

    Security: open-then-fstat closes the TOCTOU between the cap check
    and ``open()`` that lets a parallel writer (atomic_write rename)
    or a symlink swap bypass the cap mid-merge. ``read(MAX + 1)``
    defends against zero-st_size special files (FIFOs, ``/dev/zero``).
    """
    # Security (Path-Log Sibling Drift, sibling of PR #1456): see
    # ``_load_state`` for the rationale of fingerprinting the path
    # bytes rather than interpolating them verbatim into the WARNING
    # log lines below.
    path_fingerprint = hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    try:
        with path.open("rb") as f:
            if os.fstat(f.fileno()).st_size > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] ist zu groß "
                    "(> %d Bytes); überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            raw_bytes = f.read(MAX_STATE_FILE_BYTES + 1)
            if len(raw_bytes) > MAX_STATE_FILE_BYTES:
                log.warning(
                    "State-Datei [path-sha256=%s] überschreitet "
                    "%d Bytes; überschreibe State.",
                    path_fingerprint, MAX_STATE_FILE_BYTES,
                )
                return {}
            # Security: mirror the parse_constant + parse_float hooks
            # pinned at ``_load_state`` (Round 1503). The sibling reader
            # ``_read_state_capped`` reads the SAME ``data/first_seen.json``
            # state file on the save path (called from ``_save_state`` to
            # merge with existing state) and faces the identical
            # threat model — a planted NaN / Infinity / 1e1000 literal
            # propagates as ``float('nan')`` / ``float('inf')`` through
            # the merge logic and round-trip-crashes the writer pin
            # (Round 1485) at the next ``allow_nan=False`` serialisation.
            existing = json.loads(
                raw_bytes,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
            return existing if isinstance(existing, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # Security (Clear-Text-Logging Drift): broad ``Exception`` catch
        # — sanitise the bound name before WARNING-level emission.
        log.warning(
            "State-Merge fehlgeschlagen (Lesefehler: %s) – überschreibe State.",
            sanitize_log_arg(str(exc)),
        )
        return {}


def _save_state(state: dict[str, dict[str, Any]], deletions: set[str] | None = None) -> None:
    path = validate_path(feed_config.STATE_FILE, "STATE_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Separate Lock-Datei vermeidet Permission-Fehler unter Windows, wenn
    # atomic_write die Zieldatei austauscht. Die Lock-Datei wird bewusst NICHT
    # nach Gebrauch entfernt: andernfalls entstünde eine Race, in der Prozess A
    # die Datei nach dem Lock-Release unlinkt, während die parallel laufenden
    # Prozesse B und C den Pfad bereits geöffnet haben und auf separaten Inodes
    # locken — wodurch flock sich gegenseitig nicht mehr sieht. Eine persistente
    # Lock-Datei (~0 Bytes) kostet nichts und ist zwischen Läufen wiederverwendbar.
    lock_path = path.with_suffix(".lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with file_lock(lock_file, exclusive=True):
                # Safe merge: read existing state to avoid overwriting parallel updates
                # Security: open-then-fstat closes the TOCTOU between
                # the size cap and ``open``. ``read(MAX + 1)`` defends
                # against zero-st_size special files. See _load_state.
                merged_state = _read_state_capped(path)

                merged_state.update(state)
                if deletions:
                    for k in deletions:
                        merged_state.pop(k, None)

                with atomic_write(
                    path, mode="w", encoding="utf-8", permissions=0o600
                ) as f:
                    # Security (Trojan-Source / BiDi-Mark Drift Round 10):
                    # ``ensure_ascii=True`` escapes every non-ASCII code
                    # point as a literal ``\uXXXX`` sequence. The state
                    # dict's KEYS carry feed-item identities computed by
                    # ``_identity_for_item`` — the WL/non-OEBB fallback
                    # branches (this file: the ``T={item['title']}``
                    # interpolations) embed the raw provider title
                    # verbatim. A planted upstream title carrying
                    # U+202E (RIGHT-TO-LEFT OVERRIDE) / zero-width /
                    # Unicode line-separator / 8-bit C1 bytes would
                    # otherwise reach ``data/first_seen.json`` — a file
                    # committed to ``main`` by ``build-feed.yml`` — as
                    # raw UTF-8, triggering BiDi reversal in any
                    # ``cat`` / ``less`` / GitHub web UI / IDE viewer.
                    # Mirrors the canonical fix shape pinned in PR #1434
                    # for ``_write_quarantine_file``. Forensic intent is
                    # preserved (``json.loads`` recovers the original
                    # bytes from the literal escape sequence).
                    #
                    # Security (Coordinate finite/range drift, committed-
                    # writer defence-in-depth): ``allow_nan=False`` mirrors
                    # the canonical writer-side pin established in Round
                    # 1485 at :func:`src.places.merge.write_stations` and
                    # extended in Round 1487 to the sibling stations and
                    # cache-events writers. ``merged_state`` is a
                    # ``dict[str, Any]`` round-tripped via ``json.loads``
                    # (Python's default lenient mode parses ``NaN`` /
                    # ``Infinity`` literals as ``float('nan')`` /
                    # ``float('inf')``); a planted non-standard literal
                    # in a previous-run ``data/first_seen.json`` survives
                    # the round-trip and re-writes verbatim without the
                    # pin. ``ensure_ascii=True`` already blocks Trojan-
                    # Source primitives; ``allow_nan=False`` closes the
                    # sibling RFC-8259 non-conformance drift.
                    json.dump(merged_state, f, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
    except (OSError, TimeoutError) as exc:
        # Security: ``file_lock(..., exclusive=True)`` re-raises on
        # acquisition failure (timeout under contention, fcntl ENOLCK,
        # NFS hiccup, …). Skipping the state save here is the
        # fail-closed-but-recoverable choice: losing one update means
        # newly-arrived items get a stale ``first_seen`` next run, but
        # *overwriting* a parallel writer's update would corrupt the
        # cross-run dedup invariant for *every* item they tracked. The
        # next successful run reconciles via the merge step above.
        # Security (Clear-Text-Logging Drift): the bound exception (an
        # OSError or TimeoutError from the lock helper) may surface a
        # custom ``__str__`` carrying control bytes — sanitise.
        # Security (Path-Log Sibling Drift, sibling of PR #1456): see
        # ``_load_state`` for the rationale of fingerprinting the path
        # bytes rather than interpolating them verbatim.
        path_fingerprint = hashlib.sha256(
            str(path).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        log.warning(
            "State-Datei [path-sha256=%s] konnte nicht gesperrt werden "
            "(%s) – Update wird übersprungen, nächster Lauf merged frisch.",
            path_fingerprint,
            sanitize_log_arg(str(exc)),
        )


def _identity_for_item(item: FeedItem) -> str:
    """
    Stabile Identität unabhängig von Titel-Kosmetik.
      - Wenn Provider _identity liefert: diese bevorzugen.
      - ÖBB: GUID/Link (vom RSS stabil).
      - WL/sonstige: Quelle|Kategorie|Linienpräfix + Start-YYYY-MM-DD.
    """
    if item.get("_identity"):
        return str(item["_identity"])

    if "_calculated_identity" in item:
        return item["_calculated_identity"]

    title = item.get("title") or ""
    sa = item.get("starts_at")
    ea = item.get("ends_at")
    sa_str = _to_utc(sa).isoformat() if isinstance(sa, datetime) else "None"
    ea_str = _to_utc(ea).isoformat() if isinstance(ea, datetime) else "None"
    fuzzy_raw = f"{title}|{sa_str}|{ea_str}"
    fuzzy_hash = hashlib.sha256(fuzzy_raw.encode("utf-8")).hexdigest()

    result: str
    source = (item.get("source") or "").lower()
    category = (item.get("category") or "").lower()
    if "öbb" in source or "oebb" in source:
        guid_or_link = item.get("guid") or item.get("link")
        if guid_or_link:
            result = f"oebb|{guid_or_link}"
        else:
            result = f"oebb|F={fuzzy_hash}"
    else:
        lines = _parse_lines_from_title(title)
        lines_part = "L=" + "/".join(lines) if lines else "L="
        start_day = _ymd_or_none(sa)
        base = f"{source}|{category}|{lines_part}|D={start_day}"
        if source and category:
            if not lines:
                if item.get("title"):
                    result = f"{base}|T={item['title']}|F={fuzzy_hash}"
                else:
                    raw = json.dumps(item, sort_keys=True, default=str)
                    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    result = f"{base}|H={hashed}|F={fuzzy_hash}"
            else:
                result = f"{base}|F={fuzzy_hash}"
        # Fallback: Ohne Quelle/Kategorie Titel oder vollständigen Hash anhängen
        elif item.get("title"):
            result = f"{base}|T={item['title']}|F={fuzzy_hash}"
        else:
            raw = json.dumps(item, sort_keys=True, default=str)
            hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            result = f"{base}|H={hashed}|F={fuzzy_hash}"

    item["_calculated_identity"] = result
    return result

# ---------------- Pipeline ----------------


class _ProviderBuckets(NamedTuple):
    cache_fetchers: list[Any]
    network_fetchers: list[Any]
    provider_names: dict[Any, str]
    provider_envs: dict[Any, str | None]


def _categorize_providers(report: RunReport) -> _ProviderBuckets:
    """Walk PROVIDERS + plugin entrypoints and split enabled fetchers into
    cache-backed (sync) and network-backed (async) buckets, registering each
    with the report so disabled providers still appear in the health output.
    """
    cache_fetchers: list[Any] = []
    network_fetchers: list[Any] = []
    provider_names: dict[Any, str] = {}
    provider_envs: dict[Any, str | None] = {}

    provider_entries = list(PROVIDERS)
    providers_overridden = list(PROVIDERS) != list(DEFAULT_PROVIDERS)
    if provider_entries:
        if not providers_overridden:
            known_envs = {env for env, _ in provider_entries}
            for spec in iter_providers():
                if spec.env_var not in known_envs:
                    provider_entries.append((spec.env_var, spec.loader))
    else:
        provider_entries = [(spec.env_var, spec.loader) for spec in iter_providers()]

    for env, fetch in provider_entries:
        provider_name = _provider_display_name(fetch, env)
        enabled = bool(feed_config.get_bool_env(env, True))
        fetch_type = "cache" if getattr(fetch, "_provider_cache_name", None) else "network"
        report.register_provider(provider_name, enabled, fetch_type)
        if not enabled:
            continue
        provider_names[fetch] = provider_name
        provider_envs[fetch] = env
        if getattr(fetch, "_provider_cache_name", None):
            cache_fetchers.append(fetch)
        else:
            network_fetchers.append(fetch)

    return _ProviderBuckets(cache_fetchers, network_fetchers, provider_names, provider_envs)


def _run_cache_fetchers(
    cache_fetchers: list[Any],
    provider_names: dict[Any, str],
    items: list[FeedItem],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Sequentially invoke each cache-backed fetcher and feed its result through
    ``merge_result``. Cache fetchers are sync because they read from disk; no
    timeout / executor machinery applies."""
    for fetch in cache_fetchers:
        name = getattr(fetch, "__name__", str(fetch))
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        report.provider_started(provider_name)
        result: list[FeedItem] | None = None
        try:
            result = fetch()
        except Exception as exc:
            log.exception("%s fetch fehlgeschlagen: %s", name, exc)
            report.provider_error(provider_name, f"Fetch fehlgeschlagen: {exc}")
            continue
        if result is not None:
            merge_result(fetch, result, provider_name)


def _build_run_fetch(
    fetch: Any,
    effective_timeout: int | float,
    supports_timeout: bool,
    semaphore: BoundedSemaphore | None,
    provider_name: str,
) -> Any:
    """Wrap a network fetcher with semaphore-aware timeout accounting. The
    returned closure is what gets submitted to the ThreadPoolExecutor; it
    enforces both the per-call timeout and (when a concurrency limit applies)
    a semaphore acquisition timeout that subtracts wait time from the budget,
    preventing thread starvation under provider deadlock.
    """
    def _run_fetch() -> Any:
        timeout_arg = effective_timeout if effective_timeout >= 0 else None

        if semaphore is None:
            return _call_fetch_with_timeout(fetch, timeout_arg, supports_timeout)

        # Prevent thread starvation by enforcing timeout on semaphore acquisition.
        # If the provider is deadlocked/overloaded, we don't block the executor
        # worker forever.
        start_wait = perf_counter()

        # If effective_timeout < 0, fallback to global timeout + buffer to avoid infinite block
        sem_timeout = effective_timeout if effective_timeout >= 0 else (feed_config.PROVIDER_TIMEOUT + 5.0)

        acquired = semaphore.acquire(timeout=sem_timeout)
        if not acquired:
            raise TimeoutError(f"Semaphore acquisition timed out after {sem_timeout}s")

        # Subtract wait time from timeout
        try:
            elapsed = perf_counter() - start_wait
            remaining_timeout = timeout_arg - elapsed if timeout_arg is not None else None

            if remaining_timeout is not None and remaining_timeout <= 0:
                raise TimeoutError(
                    f"Semaphore acquisition took {elapsed:.2f}s, no realistic time left for fetch (threshold: <= 0s)"
                )

            return _call_fetch_with_timeout(fetch, remaining_timeout, supports_timeout)
        finally:
            semaphore.release()

    return _run_fetch


def _submit_network_fetches(
    executor: ThreadPoolExecutor,
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
) -> tuple[
    dict[Any, tuple[Any, str, int]],
    dict[Any, float | None],
    set[Any],
]:
    """Submit each network fetcher to the executor with its timeout/semaphore
    config, returning (futures-meta, deadlines, pending-set)."""
    futures: dict[Any, tuple[Any, str, int]] = {}
    deadlines: dict[Any, float | None] = {}
    pending: set[Any] = set()
    semaphores: dict[str, BoundedSemaphore] = {}

    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        timeout_override = _provider_timeout_override(fetch, env_name, provider_name)
        effective_timeout = (
            timeout_override if timeout_override is not None else feed_config.PROVIDER_TIMEOUT
        )
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        semaphore: BoundedSemaphore | None = None
        if worker_limit is not None and worker_limit > 0:
            semaphore = semaphores.get(concurrency_key)
            if semaphore is None:
                semaphore = BoundedSemaphore(worker_limit)
                semaphores[concurrency_key] = semaphore
        if timeout_override is not None:
            log.debug(
                "Provider %s nutzt Timeout-Override von %ss",
                provider_name,
                timeout_override,
            )
        if worker_limit is not None and worker_limit > 0:
            log.debug(
                "Provider %s begrenzt Worker auf %s (Schlüssel %s)",
                provider_name,
                worker_limit,
                concurrency_key,
            )
        supports_timeout = _fetch_supports_timeout(fetch)

        if effective_timeout == 0:
            report.provider_started(provider_name)
            name = getattr(fetch, "__name__", str(fetch))
            log.error("%s fetch Timeout nach 0s", name)
            report.provider_error(provider_name, "Timeout nach 0s")
            continue

        run_fetch = _build_run_fetch(
            fetch, effective_timeout, supports_timeout, semaphore, provider_name
        )

        report.provider_started(provider_name)
        future = executor.submit(run_fetch)
        futures[future] = (fetch, provider_name, effective_timeout)
        pending.add(future)
        start_time = perf_counter()
        if effective_timeout > 0:
            deadlines[future] = start_time + effective_timeout
        else:
            deadlines[future] = None

    return futures, deadlines, pending


def _evict_expired_futures(
    pending: set[Any],
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    cancelled_futures: set[Any],
    report: RunReport,
    now: float,
) -> None:
    """Per Apex Phase 1: poll deadlines on every loop turn so the busy-spin
    against real-time `perf_counter()` is bounded by the smallest remaining
    timeout. Mutates ``pending`` and ``cancelled_futures`` in place.
    """
    expired = [
        future for future in list(pending)
        if (deadline := deadlines.get(future)) is not None and now >= deadline
    ]
    for future in expired:
        pending.discard(future)
        fetch, provider_name, timeout_value = futures[future]
        name = getattr(fetch, "__name__", str(fetch))
        log.error("%s fetch Timeout nach %ss", name, timeout_value)
        report.provider_error(
            provider_name,
            f"Timeout nach {timeout_value}s",
        )
        future.cancel()
        cancelled_futures.add(future)


def _drain_completed_futures(
    futures: dict[Any, tuple[Any, str, int]],
    deadlines: dict[Any, float | None],
    pending: set[Any],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Apex-Phase-1 deadline-eviction loop: alternate eviction sweep + bounded
    `wait()` until ``pending`` drains. Result handling distinguishes Timeout,
    CancelledError, and generic Exception so the report has accurate per-
    provider error categories.
    """
    cancelled_futures: set[Any] = set()
    while pending:
        now = perf_counter()
        _evict_expired_futures(pending, futures, deadlines, cancelled_futures, report, now)

        if not pending:
            break

        wait_timeout: float | None = None
        remaining = [
            deadline - now
            for fut in pending
            if (deadline := deadlines.get(fut)) is not None
        ]
        if remaining:
            wait_timeout = max(min(remaining), 0.1)

        done, _ = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
        if not done:
            continue

        for future in done:
            pending.discard(future)
            if future in cancelled_futures:
                continue
            fetch, provider_name, _timeout_value = futures[future]
            name = getattr(fetch, "__name__", str(fetch))
            try:
                result = future.result()
            except (TimeoutError, requests.exceptions.Timeout) as exc:
                # Security (Clear-Text-Logging Drift): the timeout
                # exception text may include the upstream URL (sanitised
                # by ``request_safe`` already) but a non-request_safe
                # path (e.g. fetch() composing its own timeout) could
                # still surface raw upstream bytes — sanitise.
                sanitised = sanitize_log_arg(str(exc))
                log.error("%s fetch Timeout: %s", name, sanitised)
                report.provider_error(provider_name, f"Timeout: {sanitised}")
            except CancelledError:
                report.provider_error(provider_name, "Fetch abgebrochen")
            except Exception as exc:
                # Security (Clear-Text-Logging Drift): broad ``Exception``
                # — sanitise both log emission and the operator-facing
                # report-error surface.  ``log.exception`` interpolates
                # the bound exc via ``%s`` for the *message string*; the
                # traceback itself is rendered by the logging framework
                # via formatException (a repr-escaping path) and is
                # therefore safe.
                sanitised = sanitize_log_arg(str(exc))
                log.exception("%s fetch fehlgeschlagen: %s", name, sanitised)
                report.provider_error(
                    provider_name, f"Fetch fehlgeschlagen: {sanitised}"
                )
            else:
                merge_result(fetch, result, provider_name)


def _run_network_fetchers(
    network_fetchers: list[Any],
    provider_names: dict[Any, str],
    provider_envs: dict[Any, str | None],
    report: RunReport,
    merge_result: Any,
) -> None:
    """Run all network fetchers concurrently in a ThreadPoolExecutor with
    deadline-eviction-style timeout enforcement (Apex Phase 1). Pending
    futures are cancelled on early exit to free worker threads immediately.
    """
    desired_workers = len(network_fetchers)
    if feed_config.PROVIDER_MAX_WORKERS > 0:
        if desired_workers > feed_config.PROVIDER_MAX_WORKERS:
            log.debug(
                "Begrenze Provider-Threads von %s auf %s",
                desired_workers,
                feed_config.PROVIDER_MAX_WORKERS,
            )
        desired_workers = min(desired_workers, feed_config.PROVIDER_MAX_WORKERS)

    pending: set[Any] = set()
    with ThreadPoolExecutor(max_workers=max(1, desired_workers)) as executor:
        try:
            futures, deadlines, pending = _submit_network_fetches(
                executor, network_fetchers, provider_names, provider_envs, report
            )
            _drain_completed_futures(futures, deadlines, pending, report, merge_result)
        finally:
            # Cancel remaining futures if we exit early or with exceptions.
            # executor.shutdown(wait=False, cancel_futures=True) is automatically called
            # by the context manager's __exit__, but we explicitly cancel pending futures
            # to free resources immediately.
            for future in pending:
                future.cancel()


def _collect_items(report: RunReport | None = None) -> list[FeedItem]:
    """Run all enabled providers and merge their items into a single list.

    This is the central data-collection orchestrator of the build. It:

    1. Initialises the provider registry (lazy-loads plugins on first call).
    2. Categorises providers via :func:`_categorize_providers` into:
       - **cache fetchers** — loaders whose ``_provider_cache_name``
         attribute marks them as disk-bound (read from a local cache);
         run sequentially since the bottleneck is disk I/O, not network.
       - **network fetchers** — real upstream HTTP fetches; run
         concurrently in a :class:`ThreadPoolExecutor`.
    3. Wires up a :func:`register_cache_alert_hook` so that warnings
       emitted by the cache layer (e.g. "cache for VOR is 6h stale")
       attach to the run report instead of just logging.
    4. Runs cache fetchers via :func:`_run_cache_fetchers` (synchronous).
    5. Runs network fetchers via :func:`_run_network_fetchers`, which
       wraps each loader in a per-future timeout, evicts stragglers via
       a deadline-eviction loop (Apex Phase 1), and merges results
       through :func:`_merge_result`.
    6. Provides per-provider exception isolation — a crash in one
       loader becomes an error entry on the run report, not an abort.
       This is the project's primary bulkhead: a hostile or unhealthy
       upstream cannot bring down the other providers' data.

    The function intentionally accepts a ``RunReport`` to keep the
    health-reporting side-effect explicit. Tests inject a fresh report
    per call; production paths receive one from
    :func:`_invoke_collect_items`.

    Args:
        report: Optional :class:`RunReport` to record provider
            successes, errors, and cache alerts into. If ``None``, a
            fresh report is constructed from
            :func:`provider_statuses` so the function is safe to call
            standalone (tests, ad-hoc invocations).

    Returns:
        A list of :class:`FeedItem` dictionaries, concatenated from
        every successful provider in the order they completed. The
        result is **not** deduplicated; callers (typically
        :func:`main`) run :func:`_dedupe_items` and
        :func:`deduplicate_fuzzy` afterwards.

    See Also:
        - ``docs/architecture.md`` §1 for the full sequence diagram.
        - ``.jules/surgeon.md`` for the seven-phase extraction history.
        - ``.jules/apex.md`` for the deadline-eviction-loop performance
          fix this orchestrator depends on.
    """
    init_providers()
    if report is None:
        report = RunReport(provider_statuses())
    items: list[FeedItem] = []

    cache_alerts: defaultdict[str, list[str]] = defaultdict(list)
    seen_cache_alerts: set[tuple[str, str]] = set()
    alert_lock = Lock()

    def _cache_alert_handler(provider_key: str, message: str) -> None:
        normalized_key = str(provider_key or "").strip()
        normalized_message = clean_message(message)
        if not normalized_key or not normalized_message:
            return
        with alert_lock:
            cache_alerts[normalized_key].append(normalized_message)
            if report is not None:
                key = (normalized_key, normalized_message)
                if key not in seen_cache_alerts:
                    seen_cache_alerts.add(key)
                    report.add_warning(f"Cache {normalized_key}: {normalized_message}")

    unregister_cache_alert = register_cache_alert_hook(_cache_alert_handler)
    try:
        buckets = _categorize_providers(report)

        if not buckets.cache_fetchers and not buckets.network_fetchers:
            return []

        def _merge_result(fetch: Any, result: Any, provider_name: str) -> None:
            name = getattr(fetch, "__name__", str(fetch))
            if not isinstance(result, list):
                log.error("%s fetch gab keine Liste zurück: %r", name, result)
                report.provider_error(provider_name, "Ungültige Antwort (keine Liste)")
                return
            # Cast raw dicts to FeedItem for typing compliance after normalization
            _normalize_item_datetimes(result)
            typed_result = result
            items.extend(typed_result)
            count = len(result)
            if count == 0:
                log.warning(
                    "Cache für Provider '%s' leer – generiere Feed ohne aktuelle Daten.",
                    provider_name,
                )
                detail = "Keine aktuellen Daten"
                cache_name = getattr(fetch, "_provider_cache_name", None)
                if cache_name is not None:
                    alerts = cache_alerts.get(str(cache_name), [])
                    if alerts:
                        unique_alerts = list(dict.fromkeys(alerts))
                        detail = "; ".join(unique_alerts)
                report.provider_success(
                    provider_name,
                    items=count,
                    status="empty",
                    detail=detail,
                )
                if detail:
                    report.add_warning(f"Provider {provider_name}: {detail}")
            else:
                report.provider_success(provider_name, items=count)

        _run_cache_fetchers(
            buckets.cache_fetchers, buckets.provider_names, items, report, _merge_result
        )

        if buckets.network_fetchers:
            _run_network_fetchers(
                buckets.network_fetchers,
                buckets.provider_names,
                buckets.provider_envs,
                report,
                _merge_result,
            )

        return items
    finally:
        unregister_cache_alert()


def _invoke_collect_items(report: RunReport) -> list[FeedItem]:
    return _collect_items(report=report)


def _drop_old_items(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
) -> tuple[list[FeedItem], set[str]]:
    """Entferne Items, die zu alt sind oder bereits beendet wurden.

    Neben ``pubDate``/``starts_at`` wird – falls vorhanden – ``first_seen`` aus dem
    geladenen State als Altersreferenz verwendet. Das betrifft Items ohne
    Datumsangaben, die andernfalls ewig im Feed verbleiben würden.
    """

    out: list[FeedItem] = []
    dropped: set[str] = set()
    now_utc = _to_utc(now)
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]

        ident = _identity_for_item(it)
        state_entry = state.get(ident) if isinstance(state, dict) else None

        ends_at = it.get("ends_at")
        if isinstance(ends_at, datetime):
            if _to_utc(ends_at) < now_utc - timedelta(minutes=feed_config.ENDS_AT_GRACE_MINUTES):
                dropped.add(ident)
                continue

        dt = it.get("pubDate") or it.get("starts_at")
        age_days: float | None = None
        if isinstance(dt, datetime):
            age_days = (now_utc - _to_utc(dt)).total_seconds() / 86400.0
        elif isinstance(state_entry, dict):
            raw_first_seen = state_entry.get("first_seen")
            if raw_first_seen is not None:
                try:
                    first_seen_dt = datetime.fromisoformat(str(raw_first_seen))
                except Exception:
                    log.warning(
                        "first_seen Parsefehler: %r – ignoriere für %s",
                        raw_first_seen,
                        ident,
                    )
                else:
                    if first_seen_dt.tzinfo is None:
                        first_seen_dt = first_seen_dt.replace(tzinfo=UTC)
                    age_days = (now_utc - _to_utc(first_seen_dt)).total_seconds() / 86400.0

        if age_days is not None:
            if age_days > feed_config.ABSOLUTE_MAX_AGE_DAYS:
                dropped.add(ident)
                continue
            if age_days > feed_config.MAX_ITEM_AGE_DAYS:
                if not isinstance(ends_at, datetime):
                    dropped.add(ident)
                    continue

        out.append(it)
    return out, dropped


def _dedupe_key_for_item(
    it: FeedItem, *, warn_on_missing: bool = True
) -> tuple[str, bool]:
    """Return the deduplication key used for ``it`` and indicate fallback usage."""
    # Use explicit _identity if present
    if it.get("_identity"):
        return str(it.get("_identity")), False

    guid = it.get("guid")
    source = (it.get("source") or "").lower()

    if "öbb" in source or "oebb" in source:
        guid_or_link = guid or it.get("link")
        if guid_or_link:
            return f"oebb|{guid_or_link}", False

    if guid:
        return str(guid), False

    key = _identity_for_item(it)

    if warn_on_missing:
        log.warning(
            "Item ohne guid/_identity – Fallback-Schlüssel (_identity_for_item) %s",
            key,
        )
    return key, True


def _summarize_duplicates(items: Sequence[FeedItem]) -> list[DuplicateSummary]:
    groups: dict[str, list[FeedItem]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        key, _ = _dedupe_key_for_item(it, warn_on_missing=False)
        groups.setdefault(key, []).append(it)

    summaries: list[DuplicateSummary] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        # Security (Trojan-Source canonical-floor scrub at the boundary):
        # ``key`` (derived from each item's ``guid`` / ``_identity`` /
        # ``link``) and each ``title`` are upstream-controlled — a
        # compromised provider / MITM / DNS hijack / poisoned cache
        # fallback can plant BiDi marks, Tag-block bytes, variation
        # selectors, or C0/C1 controls inside them. ``_sanitize_text``
        # strips the canonical-floor primitive family pinned by
        # ``_CONTROL_RE`` (the byte-exact mirror of
        # ``_INVISIBLE_DANGEROUS_RE``) so every downstream sink of
        # ``DuplicateSummary`` — ``docs/feed-health.md`` inline code
        # spans (via ``safe_markdown_codespan`` in
        # ``render_feed_health_markdown``), ``docs/feed-health.json``
        # (via ``_CONTROL_CHARS_RE`` scrub in
        # ``build_feed_health_payload``), and the operator-facing
        # lint-report stdout (line below) — inherits the canonical
        # floor in one place. The grouping above runs on the RAW key
        # so two items carrying the same poisoned guid still group
        # together; only the OUTPUT key handed to ``DuplicateSummary``
        # is scrubbed.
        sanitised_key = _sanitize_text(key)
        titles = tuple(
            _sanitize_text(str(entry.get("title") or "")) for entry in group[:3]
        )
        summaries.append(
            DuplicateSummary(
                dedupe_key=sanitised_key, count=len(group), titles=titles
            )
        )
    summaries.sort(key=lambda summary: summary.count, reverse=True)
    return summaries


def _count_new_items(
    items: Sequence[FeedItem],
    state: dict[str, dict[str, Any]],
) -> int:
    existing = set(state.keys()) if isinstance(state, dict) else set()
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        ident = _identity_for_item(it)
        if ident not in existing:
            count += 1
    return count


def _dedupe_items(items: list[FeedItem]) -> list[FeedItem]:
    """
    Deduplicate items by identity/guid.

    When duplicates are found (same deduplication key), the "better" item is kept.
    Selection criteria for the "better" item:
    1. Later end date (indicates more up-to-date info on duration).
    2. More recent publication/start date.
    3. Longer description (indicates more detail).

    Args:
        items: List of item dictionaries.

    Returns:
        A list of unique item dictionaries.
    """

    def _recency_value(it: FeedItem) -> datetime:
        """Return a comparable timestamp describing how recent ``it`` is."""
        if "_calculated_recency" in it:
            return it["_calculated_recency"]

        candidates: list[datetime] = []
        for field_name in ("pubDate", "first_seen", "starts_at"):
            value = it.get(field_name)
            if isinstance(value, datetime):
                candidates.append(_to_utc(value))
            else:
                parsed = _parse_datetime(value)
                if isinstance(parsed, datetime):
                    candidates.append(_to_utc(parsed))

        if candidates:
            res = max(candidates)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_recency"] = res
        return res

    def _end_value(it: FeedItem) -> datetime:
        if "_calculated_end" in it:
            return it["_calculated_end"]

        ends = it.get("ends_at")
        if isinstance(ends, datetime):
            res = _to_utc(ends)
        else:
            res = datetime.min.replace(tzinfo=UTC)

        it["_calculated_end"] = res
        return res

    def _better(a: FeedItem, b: FeedItem) -> bool:
        """Return True if ``a`` is better than ``b`` according to recency and content."""

        a_end = _end_value(a)
        b_end = _end_value(b)
        if a_end > b_end:
            return True
        if a_end < b_end:
            return False

        # Bei gleichem Enddatum: Zuerst Aktualität, dann Länge
        if _recency_value(a) > _recency_value(b):
            return True
        if _recency_value(a) < _recency_value(b):
            return False

        a_len = len(a.get("description") or "")
        b_len = len(b.get("description") or "")
        return a_len > b_len

    seen: dict[str, int] = {}
    out: list[FeedItem] = []
    for it in items:
        key, _ = _dedupe_key_for_item(it)
        if key in seen:
            idx = seen[key]
            if _better(it, out[idx]):
                out[idx] = it
        else:
            seen[key] = len(out)
            out.append(it)
    return out

def _sort_key(item: FeedItem) -> tuple[int, float, str]:
    pd = item.get("pubDate")
    # Fix TypeError: Ensure guid is always a string, even if explicitly None
    guid_val = item.get("guid")
    if guid_val:
        guid_str = str(guid_val)
    else:
        guid_str = _identity_for_item(item)

    if isinstance(pd, datetime):
        return (0, -_to_utc(pd).timestamp(), guid_str)
    return (1, 0.0, guid_str)


def _build_canonical_link(candidate: Any, ident: str) -> str:
    """Return a canonical link for ``ident`` with a stable fallback anchor."""

    if isinstance(candidate, str):
        normalized = candidate.strip()
        if normalized:
            return normalized

    slug_source = ident or ""
    slug = quote(slug_source, safe="")
    base = (feed_config.FEED_LINK or "").strip()
    if not base:
        return f"#meldung-{slug}" if slug else ""

    anchor_prefix = "meldung"
    base = base.rstrip("/")
    if slug:
        return f"{base}#{anchor_prefix}-{slug}"
    return base


def _resolve_item_link(candidate: Any, ident: str) -> str:
    """Return the per-item ``<link>`` value, enforcing HTTPS-only.

    Security: ``validate_http_url`` accepts both ``http`` and ``https``.
    Without an HTTPS-only pin at this boundary a future upstream
    regression (legitimate or attacker-injected) that returned
    ``http://`` URLs would publish plaintext ``<link>`` elements into
    ``docs/feed.xml``. Every subscriber's RSS reader follows the
    ``<link>`` click via a fresh request and many do NOT consult the
    HSTS preload list before the click — a plaintext URL is therefore
    a documented TLS-strip primitive on the subscriber base.

    Three-step resolution mirrors the canonical pattern used by
    ``validate_public_feed_url`` (which the ``feed_config.FEED_LINK``
    fallback is itself validated by):

    1. Build canonical link via ``_build_canonical_link``.
    2. Pass through ``validate_http_url`` (SSRF / syntax / scheme).
    3. Reject ``http://`` results — fall back to the HTTPS-pinned
       ``feed_config.FEED_LINK`` so the published feed never carries
       ``<link>http://...</link>``.

    Mirrors the HTTPS-only pin from the 2026-05-09 *Public Feed URL
    Allow-List Drift* round and the 2026-05-10 *HTTPS-only Provider
    URL Drift* rounds (PRs #1415 / #1416) for every other publishing
    surface.
    """
    raw_link = _build_canonical_link(candidate, ident)
    sanitized = validate_http_url(raw_link, check_dns=False) if raw_link else ""
    if sanitized and not sanitized.lower().startswith("https://"):
        log.warning(
            "Item %s carries plaintext http:// link; falling back to "
            "feed link to prevent TLS-strip on subscribers.",
            ident,
        )
        sanitized = ""
    if raw_link and not sanitized:
        log.warning(
            "Item %s has potentially unsafe/invalid link %r; falling back to feed link.",
            ident,
            raw_link,
        )
    return sanitized or feed_config.FEED_LINK


def _cdata_content(s: str) -> str:
    """Prepare a string for inclusion in a CDATA block, handling ']]>'."""
    return s.replace("]]>", "]]]]><![CDATA[>")


class FormattedContent(NamedTuple):
    guid: str
    link: str
    title_cdata: str
    desc_text_truncated: str
    desc_cdata: str
    raw_desc: str
    title_out: str
    desc_html: str


def _update_item_state(it: FeedItem, now: datetime, state: dict[str, dict[str, Any]]) -> tuple[str, datetime]:
    ident = _identity_for_item(it)
    st = state.get(ident)
    # Fallback: check guid as secondary key
    if not st and it.get("guid") and it["guid"] != ident:
        st = state.get(str(it["guid"]))
    is_strictly_new = not st
    if not st:
        st = {"first_seen": _to_utc(now).isoformat()}
    state[ident] = st

    try:
        fs_dt = datetime.fromisoformat(st["first_seen"])
    except Exception:
        log.warning("first_seen Parsefehler: %r – fallback to now", st.get("first_seen"))
        fs_dt = _to_utc(now)
        st["first_seen"] = fs_dt.isoformat()

    # Stats: log the very first observation of a disruption identity into
    # ``data/stats/stoerungen_YYYY.csv``. We deliberately key on the
    # *strict* state-cache miss above (i.e. neither ``ident`` nor ``guid``
    # had a prior entry) so reruns of the same incident — a long-lived
    # ÖBB Streckeninformation that survives many feed builds — only
    # record a single occurrence in the dashboard. Best-effort: any
    # I/O failure inside ``append_disruption_row`` is logged and
    # swallowed so the feed build itself never blocks on stats I/O.
    if is_strictly_new:
        try:
            append_disruption_row(
                timestamp=_to_utc(now),
                provider=str(it.get("source") or "unbekannt"),
                location_name=extract_location_name(cast(dict[str, Any], it)),
            )
        except Exception as exc:  # pragma: no cover - defensive; writer is no-throw
            log.warning(
                "Disruption-Stats konnten nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )
    return ident, fs_dt


def _format_item_content(
    it: FeedItem, ident: str, starts_at: datetime | None, ends_at: datetime | None
) -> FormattedContent:
    raw_title = it.get("title") or "Mitteilung"
    raw_desc  = it.get("description") or ""
    link = _resolve_item_link(it.get("link"), ident)

    raw_guid = it.get("guid") or ident
    # Security: route the upstream-supplied guid through ``_sanitize_text``
    # (canonical ``_CONTROL_RE`` strip — C0/C1 controls + DEL + BiDi format
    # controls + zero-width chars + line separators + BOM) before it lands
    # in the published RSS XML's ``<guid>`` element. The pre-fix
    # ``str.strip()``-only path let upstream-controlled BiDi marks (CVE-
    # 2021-42574 Trojan-Source primitive), zero-width characters, and
    # line/paragraph separators flow into ``docs/feed.xml`` verbatim —
    # XML serialisation escapes ``<>&`` but does NOT strip Unicode BiDi
    # / zero-width / control chars. Mirrors the canonical sanitisation
    # already applied to the title (line ~1812: ``title_out =
    # _sanitize_text(title_out)``) and summary (line ~1782:
    # ``summary = _sanitize_text(summary).strip()``) — closes the LAST
    # per-item RSS sink that still routed through ``str.strip()``-only.
    guid = _sanitize_text(str(raw_guid)).strip() if raw_guid is not None else ident
    if not guid:
        guid = ident

    # Task: Strict 2-line Layout (Summary + Timeframe)
    # Line 1: Concise plain text summary (no HTML artifacts)
    summary = html_to_text(raw_desc, collapse_newlines=True)
    summary = _sanitize_text(summary).strip()

    # ÖBB-spezifische Datumspräfixe (z.B. "17.09.2026 - 19.11.2026 • ") entfernen
    summary = re.sub(r"^\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}\s*•\s*", "", summary)
    summary = re.sub(r"^\d{2}\.\d{2}\.\d{4}\s*•\s*", "", summary)

    # Bulletpoints auflösen, um einen fließenden Satz zu bilden
    summary = summary.replace(" • ", " ").replace("•", " ")
    summary = _WHITESPACE_CLEANUP_RE.sub(" ", summary).strip()

    # Strip a leading category word that just repeats the title body.
    # Real WL Hinweis items render with an H2 like ``Gleisbauarbeiten``
    # which becomes the title body and *also* the first word of the
    # description. The user sees::
    #
    #   T: "9/40/41/42: Gleisbauarbeiten"
    #   D: "Gleisbauarbeiten Wegen umfangreicher Gleisbauarbeiten …"
    #
    # which reads as a duplicated word at the start of the description.
    # We only strip when the duplicated leading word is one of the
    # well-known WL/ÖBB construction-category nouns; this avoids
    # collapsing e.g. ``Ersatzverkehr zwischen X und Y`` (which is a
    # complete clause, not a category prefix) when the title also says
    # ``Ersatzverkehr``.
    _CATEGORY_PREFIX_WORDS = {
        "bauarbeiten",
        "gleisbauarbeiten",
        "straßenbauarbeiten",
        "strassenbauarbeiten",
        "rohrleitungsarbeiten",
        "kranarbeiten",
        "veranstaltung",
    }
    title_match = re.match(r"^[A-Za-z0-9/]+:\s*(\S.*)$", raw_title or "")
    title_body = title_match.group(1).strip() if title_match else (raw_title or "").strip()
    if title_body and summary:
        first_title_word = title_body.split()[0]
        first_summary_word = summary.split()[0] if summary else ""
        if (
            first_title_word
            and first_summary_word
            and first_title_word.casefold() == first_summary_word.casefold()
            and first_title_word.casefold() in _CATEGORY_PREFIX_WORDS
        ):
            leading = re.match(
                rf"^{re.escape(first_summary_word)}\s*[:.,;–—-]?\s+",
                summary,
                re.IGNORECASE,
            )
            if leading:
                summary = summary[leading.end():].strip()
        elif (
            first_summary_word
            and first_summary_word.casefold() in _CATEGORY_PREFIX_WORDS
            and first_title_word
            and first_title_word.casefold() != first_summary_word.casefold()
        ):
            # Second pattern: description starts with a category word
            # (``Bauarbeiten`` / ``Gleisbauarbeiten``) but the title body
            # does NOT start with the same category — instead the title
            # body's first word matches the SECOND word of the
            # description::
            #
            #   T: "62A: Busse halten Breitenfurter Straße 236-238"
            #   D: "Bauarbeiten Busse halten Breitenfurter Straße 236-238"
            #
            # The description prepends a category H2 in front of what's
            # otherwise identical to the title body. Strip the leading
            # category word in that case as well.
            words = summary.split(maxsplit=2)
            if len(words) >= 2 and words[1].casefold() == first_title_word.casefold():
                leading = re.match(
                    rf"^{re.escape(first_summary_word)}\s*[:.,;–—-]?\s+",
                    summary,
                    re.IGNORECASE,
                )
                if leading:
                    summary = summary[leading.end():].strip()

    # Extrahiere maximal die ersten zwei Sätze.
    # Boundary regex: a real sentence end is a period after at least
    # FOUR letters (not a digit — German dates use ``17. Februar``,
    # not a 1-3 letter abbreviation either — German loves ``Gerasdorf
    # b. Wien``, ``Wien Hbf.``, ``Karlsplatz U.``, ``Bahnhst bzw.``
    # — all of these would otherwise be misread as sentence ends),
    # followed by whitespace and an uppercase German letter. This
    # avoids four false splits seen in the live cache:
    #   * ``17. Februar 2026`` (date; digit before period → keep)
    #   * ``Gerasdorf b. Wien`` (single-letter abbrev → keep)
    #   * ``Karlsplatz U. (Bereich)`` (single-letter U-Bahn marker → keep)
    #   * ``Bahnhst bzw. Gerasdorf`` (3-letter abbrev → keep)
    # while still cutting at genuine sentence boundaries like
    # ``Richtungen. Grund: …``, ``möglich. Reisende …`` and
    # ``ausgefallen. Wir bitten …``.
    sentences = [
        s.strip()
        for s in re.split(
            r'(?<=[A-Za-zÄÖÜäöüß]{4}[.!?])\s+(?=[A-ZÄÖÜ])', summary
        )
        if s.strip()
    ]
    if sentences:
        short_summary = sentences[0]
        # Append the second sentence whenever the combined length still
        # fits below the 180-char hard limit applied below. Without this
        # WL items like ``Linie 62: … Karlsplatz U. Grund: Rettungseinsatz.``
        # silently lost the cause clause whenever the first sentence
        # exceeded the older 60-char threshold (the abbreviation period
        # after ``Karlsplatz U`` artificially terminates sentence 1, so
        # sentence 2 carrying the actual disruption reason was dropped).
        if len(sentences) > 1:
            candidate = f"{short_summary} {sentences[1]}"
            if len(candidate) <= 180:
                short_summary = candidate
        summary = short_summary

    # Harte Begrenzung für den TV-Screen (max. 180 Zeichen)
    if len(summary) > 180:
        truncated = summary[:175].rsplit(' ', 1)[0]
        # Strip trailing artifacts left behind by the partial-word drop:
        # • short German abbreviation tokens ("bzw.", "ca.", "z.B.",
        #   "u.a.", "ggf.") — visually "Word bzw. …" looks like a
        #   glitch instead of an intentional ellipsis.
        # • short letter-only line markers ("IC", "RJX", "REX", "S",
        #   "U") that the rsplit isolated after dropping the partial
        #   number — "IC 1110, IC 1113, IC …" should become
        #   "IC 1110, IC 1113 …".
        # • interleaved stray punctuation (",", ";", ")", "-") that
        #   would otherwise hold the tail open across iterations,
        #   e.g. "Uhr -" leaves "Uhr" exposed only after the dash is
        #   stripped.
        _PUNCT_STRIP = ' ,;:-)/'
        # Known short German unit tokens that, on their own, look like a
        # mid-stream cut ("Uhr" without its number, "min", "km", …). We
        # treat them like line markers and drop them even when the case
        # rule below would otherwise classify them as content words.
        _UNIT_TOKENS = {"Uhr", "min", "sec", "h", "km", "kg", "m", "cm", "s", "ms"}
        # Iteration cap large enough to unwind chained "IC 1110, IC 1113,
        # IC 1115, …" patterns without over-stripping into real content
        # words. The break-on-content-word rule below is what ultimately
        # terminates the loop.
        for _ in range(8):
            truncated = truncated.rstrip(_PUNCT_STRIP)
            last_space = truncated.rfind(' ')
            if last_space <= 0:
                break
            tail = truncated[last_space + 1:]
            tail_stripped = tail.rstrip('.')
            ends_with_period = tail.endswith('.')
            should_drop = False
            if not tail_stripped:
                should_drop = True
            elif len(tail) > 5:
                should_drop = False
            elif ends_with_period and tail_stripped.isalpha():
                # German abbreviations ("bzw.", "ca.", "z.B.", "ggf.").
                should_drop = True
            elif ends_with_period and tail_stripped.isdigit():
                # German date ordinals ("3.", "10.", "31.").
                should_drop = True
            elif tail_stripped.isdigit():
                # Standalone numbers in a list ("IC 1110, IC 1113, …").
                should_drop = True
            elif tail_stripped.isalpha() and tail_stripped.isupper():
                # All-uppercase line markers ("IC", "REX", "RJX", "EC").
                should_drop = True
            elif tail in _UNIT_TOKENS or tail_stripped in _UNIT_TOKENS:
                # Known unit tokens ("Uhr", "min", …) that look isolated
                # without their number partner.
                should_drop = True
            # Real German content words (mixed-case, ≥4 chars, or in
            # neither the marker nor unit set) terminate the loop.
            if should_drop:
                truncated = truncated[:last_space]
            else:
                break
        # Drop a trailing unbalanced opening paren: real ÖBB clauses
        # like "(jeweils 08:45 Uhr - 14:45 Uhr)" frequently land just
        # past the opening "(" so the truncated form ended with
        # "(jeweils 08:45 …" / "(22:00 …" — a dangling paren reads
        # like a stray glyph before the ellipsis.
        if truncated.count("(") > truncated.count(")"):
            last_open = truncated.rfind("(")
            if last_open >= 0:
                truncated = truncated[:last_open].rstrip(_PUNCT_STRIP)
        summary = truncated.rstrip(_PUNCT_STRIP) + " …"

    # Für XML robust aufbereiten (CDATA schützt Sonderzeichen)
    title_out = _sanitize_text(raw_title)
    if len(title_out) > feed_config.TITLE_CHAR_LIMIT:
        title_out = title_out[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"

    # Minimal cleanup
    title_out = _WHITESPACE_RE.sub(" ", title_out).strip()
    title_cdata = _cdata_content(title_out)

    # Line 2: Timeframe
    time_line = format_local_times(
        starts_at if isinstance(starts_at, datetime) else None,
        ends_at if isinstance(ends_at, datetime) else None,
    )
    time_line = _sanitize_text(time_line)
    time_line = _WHITESPACE_CLEANUP_RE.sub(" ", time_line).strip()
    if time_line:
        time_line = f"[{time_line.strip('[]')}]"

    # Skip the summary entirely when it would just repeat the title
    # body verbatim. WL Störung items like ``41E: Ersatzbus 41E halten
    # bei Währinger Str 200`` produce a description that's identical
    # to the title body after the line-prefix is stripped — surfacing
    # both gives the user the same text twice. We compare casefold so
    # ``Linie 11A: Verspätung.`` and ``Verspätung`` are not flagged
    # as duplicates (different content).
    if summary and title_out:
        title_body_match = re.match(r"^[A-Za-z0-9/]+:\s*(\S.*)$", title_out)
        title_body_compare = (
            title_body_match.group(1).strip() if title_body_match else title_out
        )
        if title_body_compare and summary.casefold() == title_body_compare.casefold():
            summary = ""

    # Combine
    desc_parts = []
    if summary:
        desc_parts.append(summary)
    if time_line:
        desc_parts.append(time_line)

    desc_html = "<br/>".join(desc_parts)

    # Plain text summary for <description>
    desc_text = " ".join(desc_parts)
    if len(desc_text) > feed_config.DESCRIPTION_CHAR_LIMIT:
        desc_text_truncated = desc_text[:feed_config.DESCRIPTION_CHAR_LIMIT].rstrip() + "... [TRUNCATED]"
    else:
        desc_text_truncated = desc_text

    # Truncate HTML descriptions using the HTML-aware truncator to prevent broken layout/XSS.
    desc_html = truncate_html(desc_html, feed_config.DESCRIPTION_CHAR_LIMIT, ellipsis="... [TRUNCATED]")

    # Prepare CDATA content (handle ]]> in content)
    desc_cdata = _cdata_content(desc_html)

    return FormattedContent(guid, link, title_cdata, desc_text_truncated, desc_cdata, raw_desc, title_out, desc_html)


def _emit_item(
    it: FeedItem, now: datetime, state: dict[str, dict[str, Any]]
) -> tuple[str, ET.Element, dict[str, str]]:
    """Convert a normalized item dictionary into an RSS <item> element and CDATA replacements.

    Args:
        it: The normalized item dictionary.
        now: The current datetime (used for relative time calculations).
        state: The state dictionary (used to persist first_seen timestamps).

    Returns:
        A tuple containing:
         - The item identity (str)
         - The generated ElementTree.Element
         - A dictionary mapping placeholder strings to their CDATA-wrapped content.
    """
    it_dict = cast(dict[str, Any], it)
    pubDate = _coerce_datetime_field(it_dict, "pubDate")
    starts_at = _coerce_datetime_field(it_dict, "starts_at")
    ends_at = _coerce_datetime_field(it_dict, "ends_at")

    ident, fs_dt = _update_item_state(it, now, state)

    formatted = _format_item_content(
        it,
        ident,
        starts_at if isinstance(starts_at, datetime) else None,
        ends_at if isinstance(ends_at, datetime) else None,
    )

    if not isinstance(pubDate, datetime) and feed_config.FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=feed_config.FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # Generate unique placeholders
    # We use a cryptographically secure random token to ensure uniqueness within the document
    # Ensure placeholder is not accidentally present in the original desc_html or raw_desc
    max_attempts = 100
    attempts = 0
    while True:
        if attempts >= max_attempts:
            raise RuntimeError("Konnte keinen eindeutigen Platzhalter generieren")
        uid = secrets.token_hex(16)
        PH_CONTENT = f"___CDATA_CONTENT_{uid}___"
        PH_TITLE = f"___CDATA_TITLE_{uid}___"
        if PH_CONTENT not in formatted.desc_html and PH_CONTENT not in formatted.raw_desc and PH_TITLE not in formatted.title_out:
            break
        attempts += 1

    # --- ElementTree Construction ---
    item = ET.Element("item")

    # Title
    ET.SubElement(item, "title").text = PH_TITLE

    # Link
    ET.SubElement(item, "link").text = formatted.link

    # GUID
    guid_elem = ET.SubElement(item, "guid")
    guid_elem.text = formatted.guid

    # guid attributes (isPermaLink)
    parsed = urlparse(formatted.guid)
    if not (parsed.scheme and parsed.netloc and formatted.guid == formatted.link):
        guid_elem.set("isPermaLink", "false")

    # pubDate
    if isinstance(pubDate, datetime):
        ET.SubElement(item, "pubDate").text = _fmt_rfc2822(pubDate)

    # Extensions
    ET.SubElement(item, "{https://wien-oepnv.example/schema}first_seen").text = _fmt_rfc2822(fs_dt)

    if isinstance(starts_at, datetime):
        ET.SubElement(item, "{https://wien-oepnv.example/schema}starts_at").text = _fmt_rfc2822(starts_at)

    if isinstance(ends_at, datetime):
        ET.SubElement(item, "{https://wien-oepnv.example/schema}ends_at").text = _fmt_rfc2822(ends_at)

    # Description
    ET.SubElement(item, "description").text = formatted.desc_text_truncated

    # content:encoded
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = PH_CONTENT

    replacements = {
        PH_CONTENT: f"<![CDATA[{formatted.desc_cdata}]]>",
        PH_TITLE: f"<![CDATA[{formatted.title_cdata}]]>",
    }

    return ident, item, replacements


def _make_rss(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
    deletions: set[str] | None = None,
) -> str:
    """
    Generate the full RSS XML document from a list of items using ElementTree.

    Args:
        items: List of item dictionaries.
        now: Current timestamp.
        state: State dictionary for tracking items.
        deletions: IDs to be removed from the state.

    Returns:
        The generated RSS XML string with CDATA sections.
    """
    if deletions is None:
        deletions = set()

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    # Security: route the env-controlled FEED_TITLE / FEED_DESC through
    # the canonical ``_sanitize_text`` (``_CONTROL_RE`` strip — C0/C1
    # controls + DEL + BiDi format controls + zero-width chars + line
    # separators + BOM) before they land in the channel-level RSS XML.
    # Pre-fix the env vars flowed verbatim into ``<title>`` / ``<description>``,
    # letting a leaked CI env / compromised secret store / intentional
    # misconfig plant CVE-2021-42574 Trojan-Source primitives (RLO, LSEP,
    # ZWSP, …) into the published ``docs/feed.xml`` channel metadata —
    # the prominent "feed name" displayed by every subscriber's RSS
    # reader. Mirrors the per-item sinks already routed through the
    # helper (title / description / time-line / guid) so the closing-
    # checklist of the *Trojan-Source RSS Drift* family covers BOTH
    # per-item AND channel-level RSS sinks. ``FEED_LINK`` is already
    # pinned by ``validate_public_feed_url`` (HTTPS-only + GitHub host
    # allow-list + ``_UNSAFE_URL_CHARS`` strip) so its emission needs
    # no additional sanitisation.
    ET.SubElement(channel, "title").text = _sanitize_text(feed_config.FEED_TITLE)
    ET.SubElement(channel, "link").text = feed_config.FEED_LINK
    ET.SubElement(channel, "description").text = _sanitize_text(feed_config.FEED_DESC)

    # Atom self/alternate-Links + Sprache. Diese drei Tags wurden früher
    # vom Perl-basierten "Normalize feed metadata (SEO)"-Step in
    # .github/workflows/build-feed.yml nachträglich injiziert. Generierung
    # direkt im Python-Builder hält das XML strukturell wohlgeformt und
    # entfernt den Sprachen-Mix in CI.
    pages_base = feed_config.PAGES_BASE_URL.rstrip("/")
    atom_alternate = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_alternate.set("rel", "alternate")
    atom_alternate.set("type", "text/html")
    atom_alternate.set("href", f"{pages_base}/")
    atom_self = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_self.set("rel", "self")
    atom_self.set("type", "application/rss+xml")
    atom_self.set("href", f"{pages_base}/feed.xml")
    ET.SubElement(channel, "language").text = "de"

    ET.SubElement(channel, "lastBuildDate").text = _fmt_rfc2822(now)
    ET.SubElement(channel, "ttl").text = str(feed_config.FEED_TTL)

    item_replacements: dict[str, str] = {}
    identities_in_feed: list[str] = []
    emitted = 0
    for it in items:
        if emitted >= feed_config.MAX_ITEMS:
            break
        ident, elem, repl = _emit_item(it, now, state)
        channel.append(elem)
        item_replacements.update(repl)
        identities_in_feed.append(ident)
        emitted += 1

    # Pretty print the tree
    if hasattr(ET, "indent"):
        ET.indent(rss, space="  ", level=0)

    # Serialize to string using native ElementTree declaration
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    xml_str = xml_bytes.decode("utf-8")

    # Inject CDATA
    for placeholder, cdata in item_replacements.items():
        xml_str = xml_str.replace(placeholder, cdata)

    return cast(str, xml_str)


def lint() -> int:
    """Run structural checks on the aggregated feed items without writing RSS."""
    init_providers()
    refresh_from_env()
    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    now = datetime.now(UTC)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    exit_code = 0

    try:
        items = _invoke_collect_items(report)
        raw_count = len(items)

        filtered_items, _ = _drop_old_items(items, now, state)
        filtered_count = len(filtered_items)
        duplicate_summaries = _summarize_duplicates(filtered_items)
        duplicates_removed = sum(summary.count - 1 for summary in duplicate_summaries)

        deduped_items = _dedupe_items(list(filtered_items))
        deduped_items = cast(
            list[FeedItem],
            deduplicate_fuzzy(cast(list[dict[str, Any]], deduped_items)),
        )
        deduped_count = len(deduped_items)
        new_items_count = _count_new_items(deduped_items, state)
        missing_guid_items = [it for it in filtered_items if not it.get("guid")]

        metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

        print("Feed-Lint Bericht")
        print("==================")
        print(f"Rohdaten: {metrics.raw_items}")
        print(f"Nach Altersfilter: {metrics.filtered_items}")
        print(
            f"Nach Deduplizierung: {metrics.deduped_items} "
            f"(entfernte Duplikate: {metrics.duplicate_count})"
        )
        print(f"Neue Items (vs. State): {metrics.new_items}")

        if duplicate_summaries:
            print("\nErkannte Duplikat-Gruppen:")
            for summary in duplicate_summaries:
                titles = ", ".join(
                    title or "<ohne Titel>" for title in summary.titles if title is not None
                )
                titles = titles or "<keine Beispiele>"
                print(
                    f"- {summary.count}× Schlüssel {summary.dedupe_key}: {titles}"
                )

        if stale_cache_messages:
            print("\nVeraltete Cache-Dateien:")
            for message in stale_cache_messages:
                print(f"- {message}")

        if missing_guid_items:
            print("\nEinträge ohne GUID:")
            for item in missing_guid_items:
                source = item.get("source") or "unbekannt"
                title = item.get("title") or "<ohne Titel>"
                print(f"- {source}: {title}")

        provider_failures = report.has_errors()
        if provider_failures:
            print("\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.")

        if not duplicate_summaries and not missing_guid_items and not provider_failures:
            print("\nKeine strukturellen Probleme gefunden.")

        if provider_failures:
            exit_code = 2
        elif duplicate_summaries or missing_guid_items or stale_cache_messages:
            exit_code = 1
        else:
            exit_code = 0

        report.finish(
            build_successful=exit_code == 0,
            raw_items=metrics.raw_items,
            final_items=metrics.deduped_items,
        )
        return exit_code
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): broad framework catch-all
        # — sanitise the bound exception name in the message string.
        log.exception("Feed-Lint fehlgeschlagen: %s", sanitize_log_arg(str(exc)))
        report.record_exception(exc)
        report.finish(build_successful=False)
        return 2
    finally:
        report.log_results()


def main() -> int:
    """Execute the full feed generation pipeline (collect, dedupe, generate RSS)."""
    init_providers()
    refresh_from_env()
    configure_logging()

    statuses = provider_statuses()
    report = RunReport(statuses)
    report.prune_logs()
    report.attach_error_collector()
    _log_startup_summary(statuses)
    _validate_configuration(statuses)

    job_start = perf_counter()
    now = datetime.now(UTC)
    state = _load_state()
    stale_cache_messages = _detect_stale_caches(report, now)
    if stale_cache_messages:
        log.warning("Veraltete Caches erkannt: %s", "; ".join(stale_cache_messages))
    health_metrics: FeedHealthMetrics | None = None
    duplicate_summaries: list[DuplicateSummary] = []
    # Initialize counters to 0 to avoid redundant checks later (Task 3A)
    raw_count: int = 0
    filtered_count: int = 0
    deduped_count: int = 0
    duplicates_removed: int = 0
    new_items_count: int = 0
    items: list[FeedItem] = []
    health_path = validate_path(Path(feed_config.FEED_HEALTH_PATH), "FEED_HEALTH_PATH")
    health_json_path = validate_path(
        Path(feed_config.FEED_HEALTH_JSON_PATH), "FEED_HEALTH_JSON_PATH"
    )

    def _write_health_outputs(active_metrics: FeedHealthMetrics) -> None:
        try:
            write_feed_health_report(
                report, active_metrics, output_path=health_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            # Security (Clear-Text-Logging Drift): defensive framework catch.
            log.warning(
                "Feed-Health-Markdown konnte nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )
        try:
            write_feed_health_json(
                report, active_metrics, output_path=health_json_path
            )
        except Exception as exc:  # pragma: no cover - defensive
            # Security (Clear-Text-Logging Drift): defensive framework catch.
            log.warning(
                "Feed-Health-JSON konnte nicht geschrieben werden: %s",
                sanitize_log_arg(str(exc)),
            )

    try:
        collect_start = perf_counter()
        items = _invoke_collect_items(report)
        collect_duration = perf_counter() - collect_start
        raw_count = len(items)
        log.info(
            "Provider-Abfrage abgeschlossen: %d Items in %.2fs",
            raw_count,
            collect_duration,
        )

        filter_start = perf_counter()
        items, dropped_ids = _drop_old_items(items, now, state)
        filter_duration = perf_counter() - filter_start
        filtered_count = len(items)
        log.info(
            "Altersfilter angewendet: %d Items nach %.2fs (vorher: %d)",
            len(items),
            filter_duration,
            raw_count,
        )

        # Performance: capture the pre-dedupe count and run the duplicate
        # summary BEFORE ``_dedupe_items`` mutates the list. The previous
        # ``copy.deepcopy(items)`` snapshot was defensive overhead — at
        # ~200 items per typical run it costs ~MBs of RAM and ~100ms of
        # CPU; at 100x scale (a stress-day with thousands of items) it
        # would dominate the build. ``_summarize_duplicates`` only reads
        # from the items (the only mutation it can trigger is a benign
        # ``_calculated_identity`` cache key on the dict, which doesn't
        # affect the summary's output); ``_dedupe_items`` runs strictly
        # after both observers have seen the pre-dedupe state, so the
        # snapshot copy was redundant.
        pre_dedupe_count = len(items)
        duplicate_summaries = _summarize_duplicates(items)

        dedupe_start = perf_counter()
        deduped = _dedupe_items(items)
        dedupe_duration = perf_counter() - dedupe_start
        log.info(
            "Duplikate entfernt: %d eindeutige Items nach %.2fs (vorher: %d)",
            len(deduped),
            dedupe_duration,
            pre_dedupe_count,
        )

        fuzzy_start = perf_counter()
        fuzzy_deduped = cast(
            list[FeedItem],
            deduplicate_fuzzy(cast(list[dict[str, Any]], deduped)),
        )
        fuzzy_duration = perf_counter() - fuzzy_start
        if len(fuzzy_deduped) < len(deduped):
            log.info(
                "Fuzzy Duplikate entfernt: %d verbleiben nach %.2fs (vorher: %d)",
                len(fuzzy_deduped),
                fuzzy_duration,
                len(deduped),
            )

        items = fuzzy_deduped
        deduped_count = len(items)
        duplicates_removed = sum(summary.count - 1 for summary in duplicate_summaries)
        if not items:
            log.warning("Keine Items gesammelt.")
            items = []
        else:
            log.debug("Sortiere %d Items nach Priorität.", len(items))
        items.sort(key=_sort_key)

        new_items_count = _count_new_items(items, state)

        health_metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

        rss_start = perf_counter()
        rss = _make_rss(items, now, state, deletions=dropped_ids)
        rss_duration = perf_counter() - rss_start

        out_path = validate_path(Path(feed_config.OUT_PATH), "OUT_PATH")
        with atomic_write(
            out_path, mode="w", encoding="utf-8", permissions=0o644
        ) as f:
            f.write(rss)

        try:
            _save_state(state, deletions=dropped_ids)
        except Exception as e:
            # Security (Clear-Text-Logging Drift): broad framework catch.
            log.warning(
                "State speichern fehlgeschlagen (%s) – Feed wurde geschrieben, State bleibt veraltet.",
                sanitize_log_arg(str(e)),
            )

        total_duration = perf_counter() - job_start
        log.info(
            "Feed geschrieben: %s (%d Items) in %.2fs (RSS-Erzeugung: %.2fs)",
            out_path,
            min(len(items), feed_config.MAX_ITEMS),
            total_duration,
            rss_duration,
        )
        report.finish(
            build_successful=True,
            raw_items=raw_count,
            final_items=len(items),
            durations={
                "collect": collect_duration,
                "filter": filter_duration,
                "dedupe": dedupe_duration,
                "rss": rss_duration,
                "total": total_duration,
            },
            feed_path=out_path,
        )
        _write_health_outputs(health_metrics)
        report.log_results()
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        # Security (Clear-Text-Logging Drift): outer-most pipeline catch.
        log.exception("Feed-Bau fehlgeschlagen: %s", sanitize_log_arg(str(exc)))
        report.record_exception(exc)
        if health_metrics is None:
            # Simplified fallback using pre-initialized counters (Task 3A).
            health_metrics = FeedHealthMetrics(
                raw_items=raw_count,
                filtered_items=filtered_count,
                deduped_items=deduped_count,
                new_items=new_items_count,
                duplicate_count=duplicates_removed,
                duplicates=tuple(duplicate_summaries),
            )
        report.finish(build_successful=False)
        _write_health_outputs(health_metrics)
        report.log_results()
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Security: Prevent stack trace and sensitive info leakage to stderr
        if os.getenv("WIEN_OEPNV_DEBUG") == "1":
            raise

        # The exception is likely already logged by the application logger if configured.
        # We fail securely by not exposing internal details.
        print("Error: An unexpected error occurred. See logs for details (or set WIEN_OEPNV_DEBUG=1).", file=sys.stderr)
        sys.exit(1)
