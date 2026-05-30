from __future__ import annotations

import hashlib
import html
import inspect
import json
import logging
import os
import re
import secrets
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    ThreadPoolExecutor,
    TimeoutError,
    wait,
)
from datetime import datetime, timedelta, UTC
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any, cast, NamedTuple
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests
from dateutil import parser

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

# WL Störung items must carry a real line prefix (``U6:``, ``41E:``,
# ``9/40/41/42:``, ``D:``); without it the user can't tell which line
# is affected and the meldung is useless. Real cache items
# ``Verkehrsunfall Betrieb ab Nordbrücke``, ``Fahrtbehinderung wegen
# Verkehrsunfall``, ``Einstieg: Brünner Straße 31-31A`` and
# ``Sperre Bahnsteig Richtung Siebenhirten`` all carry
# ``_identity='wl|störung|L=|D=...'`` (empty line set) and a title
# whose leading word *looks* like a line prefix to the eye but is
# actually a generic German noun (``Einstieg:``, ``Sperre`` without a
# colon).
#
# The validation is delegated to :func:`_extract_prefix_lines` so the
# same strict-line-token gate that protects the title-rebuild step
# (rejects ``Achtung:``, ``Hinweis:``, ``Einstieg:`` etc.) also drives
# the drop decision here. Items with NO recognisable line prefix —
# regardless of how their title is shaped — are dropped at cache-read
# time.

# WL descriptions sometimes end with a dangling ``>`` / ``<`` that the
# source uses as a "service onwards" arrow indicator. With a
# destination after it (``Betrieb ab Schwedenplatz > Praterstern``) it
# carries meaning; standalone at the end of the summary
# (``Betrieb ab Schwedenplatz >``) it reads like a broken HTML tag
# glyph or truncation artifact next to the ``[Am …]`` timeframe
# bracket. We only strip the *trailing* form — mid-text occurrences
# stay put.
_TRAILING_DIRECTIONAL_MARKER_RE = re.compile(r"\s*[<>]+\s*$")

# Line prefixes WL puts at the start of descriptions are redundant
# noise — the title already carries the line attribution via the
# canonical ``40/41:`` prefix. Real cache examples:
#
#   * ``40+41: Betrieb ab Gersthof``
#   * ``Linie 40: Nach einer Fahrtbehinderung …``
#   * ``Linien 9/40/41/42: Umleitung``
#   * ``Linie D: Unregelmäßige Intervalle …`` (WL tram ``D`` — single
#     bare letter, no digit)
#
# Stripping the prefix at cache-read time cleans up the user-visible
# summary and avoids it being copied verbatim into the description
# during cross-line dedup-merge. The line-token shape mirrors
# ``_STRICT_LINE_TOKEN_RE`` in ``src/providers/wl_lines.py``: either a
# digit-bearing code (``[A-Z]{0,4}\d{1,3}[A-Z]?``) or a single bare
# uppercase letter (WL tram ``D``). Pure multi-letter German words
# (``Achtung``, ``Information``, ``Hinweis``) cannot match either
# shape so generic prefixes and time markers like ``17:30 Uhr…``
# stay untouched.
_WL_DESC_LINE_TOKEN = r"(?:[A-Z]{0,4}\d{1,3}[A-Z]?|[A-Z])"  # nosec B105  # noqa: S105 — regex fragment, not a secret
_WL_DESC_LINIE_PREFIX_RE = re.compile(
    rf"^\s*Linien?\s+(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s+",
    re.IGNORECASE,
)
_WL_DESC_COMPACT_PREFIX_RE = re.compile(
    rf"^\s*(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s+",
    re.IGNORECASE,
)


def _strip_wl_description_line_prefix(desc: str) -> str:
    """Remove a leading WL ``Linie N:`` / ``40+41:`` prefix from a description.

    The title already carries the line attribution, so the prefix is
    pure noise that mirrors back into the rendered summary.
    """
    if not desc:
        return desc
    cleaned = _WL_DESC_LINIE_PREFIX_RE.sub("", desc, count=1)
    if cleaned == desc:
        cleaned = _WL_DESC_COMPACT_PREFIX_RE.sub("", desc, count=1)
    return cleaned


def _strip_trailing_directional_marker(summary: str) -> str:
    """Drop a trailing WL ``>`` / ``<`` arrow with surrounding whitespace.

    Called before the title-body duplicate check in
    :func:`_format_item_content` so a summary that only differs from
    the title body by a dangling marker still matches and gets
    dropped as redundant.
    """
    if not summary:
        return summary
    return _TRAILING_DIRECTIONAL_MARKER_RE.sub("", summary).rstrip()


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
    4. Cached items can carry a stacked / non-canonical line prefix
       (``40: 40+41: Betrieb ab Gersthof``) when an older
       ``_ensure_line_prefix`` prepended ``relatedLines`` on top of the
       title's own ``40+41:`` block. The re-parse via
       ``_extract_prefix_lines`` unions the layers and re-emits a
       canonical ``40/41: …`` prefix so the user immediately sees
       which lines are affected.
    """
    from .providers.wl_lines import _extract_prefix_lines

    out: list[Any] = []
    for original in items:
        if not isinstance(original, dict):
            out.append(original)
            continue
        item = original
        title = item.get("title")
        if isinstance(title, str) and title:
            cleaned = re.sub(r"\s+", " ", title).strip()
            # Rebuild a stacked or non-canonical line prefix (e.g.
            # ``40: 40+41: Betrieb ab Gersthof`` →
            # ``40/41: Betrieb ab Gersthof``). Only touches items where
            # ``_extract_prefix_lines`` recovers ≥1 line tokens AND the
            # rebuilt form differs from the cached title — leaves bodies
            # without a prefix block untouched. Line order follows WL's
            # original sequence in the title (``41E/10A:`` round-trips,
            # ``40: 40+41:`` collapses to ``40/41:`` without reordering).
            body, prefix_lines = _extract_prefix_lines(cleaned)
            if prefix_lines and body:
                canonical = "/".join(prefix_lines)
                rebuilt = f"{canonical}: {body}"
                if rebuilt != cleaned:
                    cleaned = rebuilt
            if cleaned != title:
                item = dict(item)
                item["title"] = cleaned
            # Drop items whose visible title ends with a preposition
            # that demands an object — the WL source is clearly
            # incomplete and the user gets no useful information.
            title_body = cleaned.split(":", 1)[-1].strip() if ":" in cleaned else cleaned
            if _INCOMPLETE_TITLE_TAIL_RE.search(title_body):
                continue
            # Drop WL Störung items without a recognisable line
            # prefix — WL didn't provide a line code and the user
            # can't disambiguate the affected line from the title
            # alone. ``prefix_lines`` was computed via
            # :func:`_extract_prefix_lines` above (with the strict
            # line-token gate) so a generic German word prefix like
            # ``Einstieg: Brünner Straße 31-31A`` or
            # ``Sperre Bahnsteig Richtung Siebenhirten`` (both real
            # cache items) is correctly classified as "no line
            # prefix" and dropped.
            category = item.get("category")
            if category == "Störung" and not prefix_lines:
                continue
        # Strip a redundant ``Linie 40:`` / ``40+41:`` prefix from the
        # description — the title already attributes the line(s).
        desc = item.get("description")
        if isinstance(desc, str) and desc:
            stripped = _strip_wl_description_line_prefix(desc)
            if stripped != desc:
                if item is original:
                    item = dict(item)
                item["description"] = stripped
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


def _baustellen_title_names_station(title: str, label: str) -> bool:
    """Return ``True`` if ``title`` already mentions a distinctive token of
    the station ``label``, so the prefix isn't doubled up (e.g. avoid
    ``Wien Floridsdorf: Umbau Bahnhofsvorplatz Floridsdorf``). The generic
    ``Wien`` token (and other ≤4-char tokens) is ignored on purpose."""
    lowered = title.lower()
    return any(
        len(token) > 4 and token in lowered
        for token in re.split(r"\W+", label.lower())
    )


def _post_filter_baustellen(items: list[Any]) -> list[Any]:
    """Defence-in-depth relevance gate plus ÖPNV title enrichment.

    ``update_baustellen_cache.py`` already drops non-ÖPNV-relevant sites at
    ingestion, but the on-disk cache (or the bundled fallback sample) may
    predate the current policy — so the gate is re-applied here, the same
    defence-in-depth contract as :func:`_post_filter_oebb`.

    An item is kept when it is ÖPNV-relevant — at/near a rail Bahnhof OR its
    text mentions public transport (the "Bahnhofsnähe ODER ÖPNV-Text"
    policy). When a Bahnhof matches, its name is prefixed onto the title so
    the entry reads as a transit message at a glance — mirroring the
    line/route prefixes WL and ÖBB carry, and the title re-derivation
    :func:`_post_filter_oebb` performs. The prefix is skipped when the title
    already names the station, keeping the headline compact.

    Items carrying neither a title nor a description are treated as
    stubs/metadata and passed through unchanged.
    """
    from .providers.baustellen import mentions_oepnv, relevant_station, u_bahn_lines
    from .utils.stations import display_name

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
        blob = f"{title} {description}"
        station = relevant_station(item.get("location"))
        if station is None and not mentions_oepnv(blob):
            continue
        # Title prefix: the affected Bahnhof (geo) takes precedence as the
        # most concrete locator; otherwise the U-Bahn line(s) named in the
        # text. Bus/tram line numbers are deliberately not guessed here (see
        # the provider module) — their impact leads the description instead.
        label = ""
        if station is not None:
            label = display_name(station) or station
        else:
            lines = u_bahn_lines(blob)
            if lines:
                label = "/".join(lines)
        if label and not _baustellen_title_names_station(title, label):
            item = dict(item)
            item["title"] = f"{label}: {title}" if title else label
        out.append(item)
    return out


def read_cache_baustellen() -> list[Any]:
    return _post_filter_baustellen(list(read_cache("baustellen") or []))


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
    """Invoke the fetch callable, passing the timeout if supported.

    Trusts the :func:`_fetch_supports_timeout` introspection result —
    pre-fix the ``except TypeError: return fetch()`` retry caught any
    TypeError raised from INSIDE the fetch body (e.g. an ``int(timeout)``
    conversion failure, a type-assertion error), then re-invoked the
    fetcher with no kwargs. The retry duplicated HTTP requests, side
    effects, quota debits, and the ``report.provider_started`` event
    sequence — masking the real internal error behind a doubled run.
    """
    if supports_timeout:
        return fetch(timeout=None if timeout is None else timeout)
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
# BiDi-Mark Drift family (Rounds 2-5)
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
# XML-invalid code points (added 2026-05): ElementTree serialises feed item
# title/description/guid into the public docs/feed.xml + feed.en.xml. Two byte
# shapes from a hostile/garbled upstream value survive the Cf/control set above
# and break the feed (both verified end-to-end through ``_make_rss``):
#   * U+FFFE / U+FFFF \u2014 forbidden by the XML 1.0 Char production
#     ([#x20-#xD7FF] | [#xE000-#xFFFD]); a planted title yields a feed that
#     fails to parse (ParseError: not well-formed) in every subscriber's reader.
#   * U+D800-U+DFFF surrogates \u2014 a lone surrogate (reachable via a ``\uD800``
#     escape in upstream JSON) raises UnicodeEncodeError at serialisation and
#     aborts the WHOLE build.
# NOTE: the noncharacters U+FDD0-U+FDEF and supplementary-plane U+nFFFE/U+nFFFF
# are deliberately NOT stripped \u2014 they are *valid* per the XML 1.0 Char grammar
# (confirmed: they round-trip through ElementTree), so removing them would be
# silent data loss, not a fix.
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff"
    r"\ud800-\udfff\ufe00-\ufe0f\ufff9-\ufffb\ufffe\uffff"
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


# ---------------- Translation engine (Helsinki-NLP/opus-mt-de-en) ----------------
#
# The transformers pipeline is loaded lazily so the CLI commands
# (``feed lint``, ``feed build`` with ``WIEN_OEPNV_LANGS=de``) do not
# pay the ~300 MB model-load cost when the EN feed is not requested.
# The state is held in a single module-level dict so CodeQL does not
# misread a sentinel ``_TRANSLATION_LOAD_FAILED`` flag as an unused
# global (CodeQL's "Unused global variable" check does not follow the
# ``global`` declaration through a circuit-breaker assignment).
_TRANSLATION_STATE: dict[str, Any] = {"pipeline": None, "load_failed": False}
_TRANSLATION_MODEL_NAME = "Helsinki-NLP/opus-mt-de-en"

# Translation-cache epoch. EN translations are cached per disruption
# identity in ``state[ident]["translations"]["en"]`` and persisted
# across builds. The cache key (see :func:`_identity_for_item`) is
# stable across cosmetic changes — a Baustellen item keyed on
# ``title + date-range`` survives a description-only edit — so a
# translation computed once is served for the lifetime of the item
# (multi-year for long construction projects).
#
# That persistence is a problem whenever the masking / glossary logic
# IMPROVES: an item translated by an older build keeps its stale
# rendering forever, because :func:`_cached_translation` only forces a
# retry when the cached value equals the German source (the
# "Sticky-German" guard). A cached value that is a *wrong but
# non-German* translation — e.g. ``Schlachthausgasse`` rendered as
# "slaughterhouse gas" before the street-suffix masker existed — is
# served indefinitely.
#
# The epoch breaks that lock. Bump this integer whenever a change to
# :func:`_mask_entities`, the entity patterns, or the domain glossary
# would alter the EN output for already-cached items. On the next
# build :func:`_apply_lang_overlay` evicts every translation stamped
# with an older epoch and recomputes it through the improved
# pipeline, then re-stamps the current epoch so subsequent builds
# trust the cache again (no churn).
#
# Epoch history:
#   1 — first epoch. Invalidates every pre-epoch cache entry, picking
#       up the street-suffix masker (#1624), the metadata-driven
#       glossary (#1625) and the disruption-core scope tightening.
#   2 — "ggü." / "ggü" (gegenüber) → "opp." / "opp" street-addressing
#       abbreviation added to the base glossary.
#   3 — station masker now derives clean surface variants from
#       ``(WL)`` / ``(VOR)``-suffixed canonical names (e.g.
#       ``Schloss Hetzendorf`` from ``Wien Schloss Hetzendorf (WL)``),
#       so Vienna stop names with a translatable component are no
#       longer mistranslated ("Schloss Hetzendorf" → "lock Hetzendorf").
_TRANSLATION_CACHE_EPOCH = 3

# Static lookup for German → English time-line prefixes used inside the
# bracketed ``[…]`` timeframe (see ``format_local_times``). Translating
# these via the ML model would be wasteful — every disruption shares
# the same five prefixes — and slow (each translate call hits the
# tokenizer). The dictionary mapping mirrors the prefixes emitted by
# ``format_local_times`` in :mod:`src.build_feed`.
_TIME_PREFIX_DE_TO_EN: dict[str, str] = {
    "Seit": "Since",
    "Bis": "Until",
    "Ab": "From",
    "Am": "On",
}


# ---------------- Entity preservation (proper-noun masking) ----------------
#
# The Helsinki opus-mt-de-en model translates German to English by
# statistical token mapping; it does not know which spans are proper
# nouns and will happily translate ``Stephansplatz`` to
# ``Stephen's Square`` or ``Wiener Linien`` to ``Vienna Lines``.
# To prevent this we mask known entities BEFORE handing the text to
# the model and unmask them AFTER inference. Three entity sources are
# composed (longest-first so e.g. ``Wien Hauptbahnhof`` matches before
# ``Hauptbahnhof``):
#
#   1. A static list of operator brands / network names that never
#      change shape across feed builds.
#   2. A compiled regex for ÖPNV line identifiers (U-Bahn, S-Bahn,
#      tram, bus) — these are short alphanumeric tokens
#      (``U1``..``U6``, ``S1``..``S99``, ``1``..``99[A-Z]?``) that
#      machine translation routinely loses.
#   3. A lazily-built regex covering every name + alias from the
#      project's station directory (``data/stations.json`` via
#      :func:`src.utils.stations._station_entries`). Loading is gated
#      behind ``@lru_cache`` so the CLI commands that never request
#      EN output pay zero cost.
#
# The placeholder format ``XENT<n>X`` is alphanumeric and starts with
# a letter so the SentencePiece tokenizer used by Marian/Helsinki
# models keeps each placeholder as a single token without inserting
# extra whitespace. Translation that drops a placeholder degrades
# gracefully — :func:`_unmask_entities` only restores the placeholders
# it can still find.
_BRAND_ENTITIES: tuple[str, ...] = (
    "Wiener Linien",
    "Wiener Lokalbahnen",
    "Badner Bahn",
    "WLB",
    "ÖBB",
    "VOR",
    "VAO",
    "WESTbahn",
    "Cat",
    "RegioJet",
    "S-Bahn",
    "S-Bahn-Stammstrecke",
    "Stammstrecke",
)

_LINE_ENTITY_RE: re.Pattern[str] = re.compile(
    r"\b(U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?)\b"
)

# ÖPNV domain glossary. The Helsinki opus-mt-de-en model has only
# seen everyday German prose during training; it routinely
# mistranslates Austrian transit jargon because the closest token in
# its vocabulary lives in a different domain. Live regressions
# observed on the public feed (2026-05-22):
#
#   * ``Betriebsstörung``        → "Harmful vehicle"   (sic)
#   * ``Fahrtbehinderung``       → "Disability"
#   * ``Aufgelassen``            → "Open"               (opposite meaning!)
#   * ``Hauptfahrbahn``          → "main runway"
#   * ``Schadhaftem Fahrzeug``   → "Harmful vehicle"
#
# Each entry below is a German source token mapped to its canonical
# English equivalent in the transit domain. The mapping is consumed
# by :func:`_apply_domain_glossary` BEFORE the model sees the text:
# the German token is replaced by an ``XGLO…`` placeholder whose
# mapping entry carries the *English* term. After the model returns
# its (now correct, because the problematic word is gone) translation,
# :func:`_unmask_entities` substitutes the English term back in.
#
# Architecture: three-layer composition driven by the per-item
# metadata (``FeedItem["source"]``, ``FeedItem["category"]``) that
# every provider already attaches to a disruption. At translation
# time the active glossary is the merger of:
#
#   * :data:`_GLOSSARY_BASE` — universally applicable transit jargon
#   * :data:`_GLOSSARY_BY_SOURCE` ``[source]`` — operator-specific
#     vocabulary (Wiener Linien / ÖBB / Stadt Wien Baustellen / …)
#   * :data:`_GLOSSARY_BY_CATEGORY` ``[category]`` — disruption-type
#     specific vocabulary (architectural extension point — empty
#     today because the project has a tight source↔category coupling)
#
# Layering applies later overlays last, so a source / category entry
# can override a base entry when needed. The merger and the compiled
# alternation regex are cached per ``(source, category)`` combination
# via :func:`_resolve_glossary` and :func:`_domain_glossary_pattern`
# — the build pays the merge/compile cost once per unique combination,
# not once per item. Longer compound terms appear first in the
# regex alternation so e.g. ``Schadhaftem Fahrzeug`` beats the
# single-word ``Schadhaftem`` and the model receives one placeholder
# per concept rather than two.
_GLOSSARY_BASE: dict[str, str] = {
    # --- Disruption-type nouns --------------------------------------
    "Betriebsstörung": "service disruption",
    "Betriebsstörungen": "service disruptions",
    "Fahrtbehinderung": "service obstruction",
    "Fahrtbehinderungen": "service obstructions",
    "Streckenstörung": "line disruption",
    "Streckensperre": "line closure",
    "Gleissperre": "track closure",
    "Signalstörung": "signal disruption",
    "Weichenstörung": "switch fault",
    "Stellwerksstörung": "interlocking failure",
    "Oberleitungsstörung": "overhead-line fault",
    "Stromausfall": "power outage",
    "Verspätung": "delay",
    "Verspätungen": "delays",
    "Fahrtausfall": "service cancellation",
    "Fahrtausfälle": "service cancellations",
    "Zugausfall": "train cancellation",
    "Zugausfälle": "train cancellations",
    # --- Disruption resolution / progress markers -------------------
    # Marian's defaults read awkwardly here ("behoben" → "fixed",
    # "Behebung" → "elimination"). The transit-domain natural English
    # mirrors how UK / Irish operators phrase the same updates.
    "behoben": "resolved",
    "Behebung": "resolution",
    "Ursache": "cause",
    "wird untersucht": "is being investigated",
    "wird geprüft": "is being checked",
    # --- Cancelled stops --------------------------------------------
    # Operator-agnostic (WL: "Halt am Karlsplatz entfällt"; ÖBB: "Halt
    # in St. Pölten entfällt"). Sits in base because the surface form
    # and the EN rendering are identical across operators — there is
    # no operator-specific override to write.
    "Halt entfällt": "stop omitted",
    "Halt entfallen": "stop omitted",
    "Halte entfallen": "stops omitted",
    # --- Construction / works ---------------------------------------
    "Gleisbauarbeiten": "track construction works",
    "Gleisarbeiten": "track works",
    "Gleisschaden": "track damage",
    "Bauarbeiten": "construction works",
    "Wartungsarbeiten": "maintenance works",
    "Reparaturarbeiten": "repair works",
    "Kranarbeiten": "crane works",
    "Rohrleitungsarbeiten": "pipeline works",
    "Straßenbauarbeiten": "roadworks",
    # --- Time / expectancy adverbs ----------------------------------
    # Marian translates "voraussichtlich" as "presumably" which reads
    # awkwardly in a disruption / construction timeline. The transit
    # use case is always "expected to last / end at …".
    "voraussichtlich": "expected",
    "voraussichtliche Dauer": "expected duration",
    "voraussichtliches Ende": "expected end",
    "bis auf Weiteres": "until further notice",
    # --- Replacement services ---------------------------------------
    "Schienenersatzverkehr": "rail replacement service",
    "Ersatzverkehr": "replacement service",
    "Ersatzbus": "replacement bus",
    # --- Emergencies / events ---------------------------------------
    "Polizeieinsatz": "police operation",
    "Rettungseinsatz": "rescue operation",
    "Notarzteinsatz": "ambulance operation",
    "Feuerwehreinsatz": "fire-brigade operation",
    "Verkehrsunfall": "traffic accident",
    "Verkehrsüberlastung": "traffic congestion",
    "Staatsbesuch": "state visit",
    "Veranstaltung": "event",
    "Demonstration": "demonstration",
    # --- State / mode adjectives ------------------------------------
    "Schadhaftes Fahrzeug": "defective vehicle",
    "Schadhafter Fahrzeug": "defective vehicle",
    "Schadhaftem Fahrzeug": "defective vehicle",
    "Schadhafter LKW": "defective truck",
    "Schadhaftem LKW": "defective truck",
    "Hauptfahrbahn": "main carriageway",
    "Aufgelassen": "Discontinued",
    "Aufgelassene": "Discontinued",
    "Personen im Gleisbereich": "persons on the tracks",
    "Umleitung": "diversion",
    "Unregelmäßige Intervalle": "irregular intervals",
    "Eingeschränkter Betrieb": "restricted service",
    # --- Compound idioms specific to ÖBB/WL ticker text ------------
    "Betrieb ab": "service from",
    # --- Street-addressing abbreviations ----------------------------
    # "ggü." (gegenüber) is the Baustellen-feed shorthand for
    # "opposite house number N" (e.g. "Simonygasse ggü. 2B"). Marian
    # leaves the unknown abbreviation verbatim, so the EN feed showed
    # the German "ggü." untranslated. Map it to the English "opp."
    # (opposite). Longest-first ordering makes the dotted form win
    # over the bare "ggü" so the period is preserved as "opp.".
    "ggü.": "opp.",
    "ggü": "opp",
}


# Operator-specific glossary overlays. Each key is the literal
# ``source`` value emitted by the matching provider (see
# ``src/providers/*.py`` and ``src/feed/stammstrecke.py``). An item's
# active glossary is :data:`_GLOSSARY_BASE` ∪ this overlay; entries
# here override base entries when both define the same key (rare —
# the overlays focus on terms that do NOT appear in the base set so
# they would be too narrow to apply universally).
_GLOSSARY_BY_SOURCE: dict[str, dict[str, str]] = {
    # Urban transit (metros, trams, buses). Scope: disruption-core
    # vocabulary only (Störungen / Verspätungen / Ausfälle). Facility
    # vocabulary (Aufzug / Rolltreppe / Niederflur) is intentionally
    # OUT — those items are not feed content. "Kurzführung" stays
    # because it describes an operational truncation of a line in
    # response to a disruption (the line short-runs to a substitute
    # terminus); Marian otherwise renders it as the literal "short
    # conduct" which is meaningless in transit English.
    "Wiener Linien": {
        "Kurzführung": "short-running service",
        "kurzgeführt": "operating short-running",
    },
    # Long-distance + regional rail. Scope: disruption-core
    # vocabulary only — train-type names (so the EN feed can phrase
    # "Personenzug 5072 verspätet" naturally), platform / connection
    # disruption phrases, line-section closures. Service-info
    # vocabulary (Reservierungspflicht / Fahrkartenpflicht / etc.) is
    # OUT — those are not disruption content.
    "ÖBB": {
        "Personenzug": "passenger train",
        "Personenzüge": "passenger trains",
        "Regionalzug": "regional train",
        "Regionalzüge": "regional trains",
        "Fernverkehr": "long-distance service",
        "Nahverkehr": "regional service",
        "Bahnsteigwechsel": "platform change",
        "Anschlussverlust": "missed connection",
        "Tunnelsperre": "tunnel closure",
    },
    # Road construction sites (Stadt Wien open-data Baustellen feed).
    # Talks about lane closures and routing in road-traffic vocabulary
    # that does NOT appear in WL/ÖBB items, so the WL elevator/tram
    # vocabulary would never apply here and vice versa.
    "Stadt Wien – Baustellen": {
        "Vollsperre": "full closure",
        "Vollsperrung": "full closure",
        "Teilsperre": "partial closure",
        "Teilsperrung": "partial closure",
        "Spurbeschränkung": "lane restriction",
        "Spurreduktion": "lane reduction",
        "Bauphase": "construction phase",
        "Verkehrsführung": "traffic routing",
    },
    # VOR/VAO Stammstrecke-Verspätungsmonitor emits highly templated
    # CSV-driven items; the surface vocabulary is already covered by
    # the base glossary. Empty overlay kept for symmetry with the
    # other operators (and so the resolver finds the key without
    # falling through to ``.get(..., {})``).
    "VOR/VAO": {},
}


# Category-specific glossary overlays. Architectural extension point:
# the project currently has a tight 1:1 source↔category coupling
# (Stadt Wien ↔ Baustelle; WL/ÖBB/VOR ↔ Störung), so source-driven
# entries cover today's vocabulary needs. The category axis activates
# once an operator publishes a new category (e.g. WL also emitting a
# "Baustelle" category for tram-replacement works) and we want
# category-specific overrides regardless of which source emits them.
_GLOSSARY_BY_CATEGORY: dict[str, dict[str, str]] = {}


@lru_cache(maxsize=32)
def _resolve_glossary(
    source: str | None, category: str | None
) -> dict[str, str]:
    """Return the merged glossary for a given ``(source, category)``.

    Layering order: base → source overlay → category overlay. Later
    overlays override earlier entries with the same key. Unknown
    ``source`` / ``category`` values degrade silently to the base
    layer — a typo in the feed's source string MUST NOT abort the EN
    build.

    Caches up to 32 unique combinations; today's catalogue is ~4
    sources × ~3 categories + the ``(None, None)`` legacy entry, well
    under the cap. The returned dict is INTERNAL cached state —
    callers MUST treat it as read-only (only ``.get()`` and ``.keys()``
    usages exist today).
    """
    merged: dict[str, str] = dict(_GLOSSARY_BASE)
    if source:
        merged.update(_GLOSSARY_BY_SOURCE.get(source, {}))
    if category:
        merged.update(_GLOSSARY_BY_CATEGORY.get(category, {}))
    return merged


@lru_cache(maxsize=32)
def _domain_glossary_pattern(
    source: str | None, category: str | None
) -> re.Pattern[str]:
    """Compile the case-insensitive, longest-first regex for the
    active glossary derived from ``(source, category)``.

    Cached per metadata combination so the regex compile work is paid
    once per unique combo, not per feed item. The regex matches any
    key from the active layered glossary; the actual surface →
    English mapping is resolved at match time inside
    :func:`_apply_domain_glossary` via :func:`_resolve_glossary`.

    Longest-first sort is critical: ``Schadhaftem Fahrzeug`` must beat
    the single-word ``Schadhaftem`` alternation so the model sees one
    placeholder, not two.
    """
    glossary = _resolve_glossary(source, category)
    sorted_terms = sorted(glossary.keys(), key=len, reverse=True)
    if not sorted_terms:
        # Defensive: empty glossary should never happen because the
        # base layer is always populated. Return a never-match pattern
        # rather than letting ``re.compile("(?<!\\w)(?:)(?!\\w)")``
        # produce ill-defined alternation.
        return re.compile(r"(?!)")
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(term) for term in sorted_terms)
        + r")(?!\w)"
    )
    return re.compile(pattern, re.IGNORECASE)


def _norm_metadata(value: Any) -> str | None:
    """Normalise a ``FeedItem`` source/category field for metadata
    lookup.

    Returns ``None`` for non-string or whitespace-only input so the
    caller can treat "no metadata" uniformly. Trims edge whitespace
    so a provider that emits ``" Wiener Linien "`` still matches the
    canonical key in :data:`_GLOSSARY_BY_SOURCE`.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


# German compound-noun suffixes that mark a token as a street / public-
# space proper noun. The masker treats any word ending in one of these
# suffixes — and capitalised at the start — as an entity to preserve
# verbatim. Live regressions on the public feed (2026-05-22):
#
#   * ``Pasettistraße`` → "Pasetti Street"
#   * ``Landstraßer Hauptstraße`` → "Landstraßer main road"
#
# The model heuristically renders ``-straße`` as "Street" / "road" once
# the word stem is unknown; by masking the entire compound we keep the
# Austrian street name as a single recognisable label in the EN feed.
# The Latin-1 supplement umlauts (``ä``/``ö``/``ü``/``ß``) are part of
# ``\w`` under Python 3 ``re`` so the ``\b`` word boundary works
# naturally on tokens like ``Hellwagstraße``.
_STREET_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-.]{1,30}"
    r"(?:"
    r"[Ss]traße|[Ss]trasse|[Gg]asse|[Pp]latz|[Bb]rücke|[Bb]rucke"
    r"|[Mm]arkt|[Ww]eg|[Rr]ing|[Aa]llee|[Ss]tieg|[Ss]teig"
    r"|[Pp]romenade|[Kk]ai|[Gg]raben"
    r")\b"
)

# Placeholder pattern recognised by :func:`_unmask_entities` and the
# ``X``-bookended form deliberately avoids ``_`` (which the
# SentencePiece tokenizer used by Marian models treats specially).
#
# A per-process random hex nonce is embedded between the ``XENT``/``XGLO``
# prefix and the index so a placeholder can never collide with a token of
# the same *shape* that an upstream (Zero-Trusted) title/description happens
# to carry. Without it, a crafted source token such as ``XENT0X`` either
# collided with a generated placeholder and was rewritten to that entity's
# surface form on unmask, or matched the unmask sweep and was deleted
# (``mapping.get(ph, "")``) — corrupting the EN feed body. The nonce makes
# both impossible (the source cannot predict it) while staying ``_``-free
# for SentencePiece; the literal ``X`` separator before the index keeps the
# regex unambiguous and leaves any old/foreign ``XENT<digits>X`` unmatched.
_PLACEHOLDER_NONCE = secrets.token_hex(8)
_ENTITY_PLACEHOLDER_FORMAT = "XENT" + _PLACEHOLDER_NONCE + "X{index}X"
_ENTITY_PLACEHOLDER_RE: re.Pattern[str] = re.compile("XENT" + _PLACEHOLDER_NONCE + r"X\d+X")


# Unicode glyphs that the Helsinki opus-mt-de-en tokenizer is likely
# to map to its ``<unk>`` slot — and silently drop on detokenize.
# Empirically observed in the live feed (2026-05-22): an ÖBB title
# ``Wien Hauptbahnhof ↔ Wien Floridsdorf ↔ Wien Meidling`` translated
# to ``Wien Hauptbahnhof Wien Floridsdorf Wien Meidling`` — the
# bidirectional arrow ``↔`` (U+2194) was stripped. The fix routes
# these glyphs through the same placeholder-mask path as proper
# nouns so they survive the round trip.
#
# Coverage by Unicode block:
#   * U+2010..U+2015 — dashes (hyphen, figure dash, en-/em-dash, …)
#     plus U+2026 (horizontal ellipsis) and U+2022/U+2023 (bullets);
#   * U+2190..U+21FF — Arrows block;
#   * U+27F0..U+27FF — Supplemental Arrows-A;
#   * U+2900..U+297F — Supplemental Arrows-B;
#   * U+2B00..U+2BFF — Miscellaneous Symbols and Arrows.
#
# The class is intentionally narrow: it does NOT cover the Latin-1
# supplement (where ``ä``/``ö``/``ü`` live and MUST survive into the
# model). False-positive risk is therefore zero for typical German
# disruption text.
_PRESERVED_SYMBOLS_RE: re.Pattern[str] = re.compile(
    r"["
    r"‐-―•‣…"
    r"←-⇿"
    r"⟰-⟿"
    r"⤀-⥿"
    r"⬀-⯿"
    r"]"
)


# Aliases that look like a noise-prefixed station ("Bahnhof X", "Bf X",
# "Station X", …) are skipped — those are spelling variants of the
# canonical name, not user-facing short forms. The match is
# case-insensitive and bounded by ``\b`` so the regex does not eat
# fragments of legitimate words ("Bahnhofstraße" stays untouched).
_ALIAS_NOISE_RE: re.Pattern[str] = re.compile(
    r"(?i)\b(?:Bahnhof|Bahnst|Bahnhst|Bhf|Bf|Hbf|Station|Hp|hl\.?\s*st\.?)\b"
)

# Trailing data-source marker suffix on a canonical station name — the
# ``(WL)`` in ``Wien Schloss Hetzendorf (WL)`` or the ``(VOR)`` /
# ``(ÖBB)`` equivalents. Stripped when deriving the clean surface
# variants in :func:`_station_entity_pattern` so the masker protects
# the form real feed text carries (without the marker). Anchored to
# the END so a parenthetical that is genuinely part of a name (none
# exist today, but defensively) mid-string is left intact.
_STATION_PAREN_SUFFIX_RE: re.Pattern[str] = re.compile(r"\s*\([^)]*\)\s*$")

# Aliases must look like a clean, short ``Wien X`` station name to be
# eligible for inclusion. Length cap keeps the regex bounded; the
# character class keeps spelling variants ("Wien Schwedenplatz",
# "Wien Heiligenstadt", "Wien Mitte") in while filtering out anything
# carrying digits, parentheses, slashes, or other markup.
_ALIAS_CLEAN_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß. \-]{6,28}$"
)


@lru_cache(maxsize=1)
def _station_entity_pattern() -> re.Pattern[str] | None:
    """Build a regex that matches every meaningful station name from
    the project's station directory.

    Three name forms per entry are emitted:

      * The canonical ``name`` field (e.g. ``Wien Stephansplatz``,
        ``Wien Mitte-Landstraße``).
      * The bare form with the leading ``Wien `` prefix dropped
        (e.g. ``Stephansplatz``) so disruption text that omits the
        city prefix still matches.
      * Curated ``Wien X`` aliases from the entry's alias list — only
        for ``in_vienna`` entries, only when the alias looks like a
        user-facing short form (passes ``_ALIAS_CLEAN_RE`` and does
        NOT contain a "Bahnhof"/"Bf"/"Station" noise prefix). This
        captures real-world short forms like ``Wien Mitte`` (alias
        of canonical ``Wien Mitte-Landstraße``) that ÖBB and WL feed
        text routinely use without the long suffix. Without the
        alias coverage the masker missed ``Wien Mitte`` and the
        translation pipeline rewrote ``Wien`` → ``Vienna`` for that
        token alone, producing ``Vienna Mitte`` in the EN feed.

    The bare form is NOT emitted for aliases (only for canonicals)
    because alias text can be ambiguous outside the Vienna context —
    e.g. ``Mitte`` alone is not Vienna-specific, but ``Wien Mitte``
    is. Restricting bare-form generation to canonical names keeps
    the regex precise without false positives.

    The ~244k raw aliases in ``data/stations.json`` are reduced to
    ~11k Vienna short-forms after the ``in_vienna`` + clean-regex +
    noise-regex filters, so the compiled pattern stays under a
    megabyte and first-compile cost is ~350 ms (one-time, amortised
    over the build).

    Entries are sorted longest-first so ``Wien Hauptbahnhof`` matches
    ahead of ``Hauptbahnhof`` (greedy left-to-right alternation in
    :mod:`re` honours the first alternative that matches at a given
    position; the longest-first order makes the alternation pick the
    most specific name). Lookup is cached for the lifetime of the
    process via :func:`functools.lru_cache`.
    """
    try:
        from .utils.stations import _station_entries
    except ImportError:
        # Defensive: keeps the entity masker functional even if the
        # stations helper is unavailable (e.g. a future refactor or a
        # stripped-down test fixture).
        return None

    names: set[str] = set()
    for entry in _station_entries():
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            continue
        stripped = raw_name.strip()
        # Filter out pathological short tokens, pure-digit IDs and
        # values that would collide with the line-identifier regex
        # (e.g. ``5B`` as a station alias).
        if (
            len(stripped) < 4
            or stripped.isdigit()
            or _LINE_ENTITY_RE.fullmatch(stripped)
        ):
            continue
        names.add(stripped)
        # Emit clean surface variants. The station directory stores
        # Vienna stops with a data-source marker suffix (e.g. ``Wien
        # Schloss Hetzendorf (WL)``) — 1720 of 1773 Vienna canonicals
        # carry one. Real feed text uses the clean form WITHOUT the
        # ``(WL)`` / ``(VOR)`` marker, and frequently without the
        # ``Wien `` city prefix too. Pre-fix the bare-form derivation
        # only dropped the ``Wien `` prefix, leaving the useless
        # ``Schloss Hetzendorf (WL)`` in the regex while the feed's
        # actual ``Schloss Hetzendorf`` slipped through — the model
        # then mistranslated the ``Schloss`` component to "lock" /
        # "Castle". Emit every clean variant so the masker protects
        # the surface form the feed carries:
        #   ``Wien Schloss Hetzendorf (WL)`` (canonical, above)
        #   ``Wien Schloss Hetzendorf``       (suffix stripped)
        #   ``Schloss Hetzendorf (WL)``       (prefix stripped)
        #   ``Schloss Hetzendorf``            (both stripped)
        clean = _STATION_PAREN_SUFFIX_RE.sub("", stripped).strip()
        variants = {clean}
        if stripped.lower().startswith("wien "):
            variants.add(stripped[5:].strip())
            variants.add(clean[5:].strip())
        for variant in variants:
            if (
                len(variant) >= 4
                and not variant.isdigit()
                and not _LINE_ENTITY_RE.fullmatch(variant)
            ):
                names.add(variant)

        # Curated ``Wien X`` aliases — restrict to Vienna entries so
        # the regex cannot accidentally protect a non-Vienna alias
        # that happens to start with ``Wien``.
        if not entry.get("in_vienna"):
            continue
        aliases = entry.get("aliases")
        if not isinstance(aliases, list):
            continue
        for raw_alias in aliases:
            if not isinstance(raw_alias, str):
                continue
            alias = raw_alias.strip()
            if (
                not alias
                or alias == stripped
                or not alias.startswith("Wien ")
                or _ALIAS_NOISE_RE.search(alias)
                or not _ALIAS_CLEAN_RE.fullmatch(alias)
            ):
                continue
            names.add(alias)

    if not names:
        return None

    sorted_names = sorted(names, key=len, reverse=True)
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(name) for name in sorted_names)
        + r")(?!\w)"
    )
    return re.compile(pattern, re.IGNORECASE)


@lru_cache(maxsize=1)
def _brand_entity_pattern() -> re.Pattern[str]:
    """Compile the static brand list into a longest-first, case-
    sensitive regex.

    Case-sensitive matching is critical: the brand list contains
    all-caps abbreviations (``VOR``, ``VAO``, ``WLB``, ``ÖBB``) that
    would collide with everyday German prepositions under
    ``re.IGNORECASE`` — the canonical example was ``VOR`` (operator)
    matching ``vor`` (preposition "before") in disruption text such
    as ``"… Pasettistraße vor Hellwagstraße"``. The case-sensitive
    pattern keeps the brand list useful for the upper-case forms
    operators actually publish, while letting the surrounding prose
    flow through to the translator untouched.
    """
    sorted_brands = sorted(_BRAND_ENTITIES, key=len, reverse=True)
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(brand) for brand in sorted_brands)
        + r")(?!\w)"
    )
    return re.compile(pattern)


def _mask_entities(text: str) -> tuple[str, dict[str, str]]:
    """Replace known entities in ``text`` with stable placeholders.

    The masker applies five passes in priority order — each pass is a
    **verbatim shield**: the placeholder restores to the original
    German surface form on unmask:

      1. **Brands** — static operator / network names.
      2. **Stations** — canonical names + bare forms + curated
         ``Wien X`` aliases from the project's station directory.
      3. **Line tokens** — ``U6``, ``S40``, ``5A``, …
      4. **Street suffixes** — capitalised compound nouns ending in a
         German street/place suffix (``…straße``, ``…gasse``,
         ``…platz``, …) are preserved verbatim. This catches every
         street name that is NOT also a registered station alias.
      5. **Preserved Unicode symbols** — arrows, bullets, em-/en-
         dashes, the ellipsis: glyphs that Marian's SentencePiece
         tokenizer otherwise maps to ``<unk>`` and drops.

    Domain-jargon translation (``Betriebsstörung`` →
    ``service disruption`` etc.) is a SEPARATE concern handled by
    :func:`_apply_domain_glossary` and composed in
    :func:`_translate_text_attempt`. Keeping the two passes split
    preserves the masker's identity contract — ``unmask(mask(x)) == x``
    for arbitrary text — and lets the property-based tests in
    ``tests/test_entity_masking_properties.py`` keep their strong
    round-trip invariant.

    Longest-matching span wins regardless of which source discovered
    it. Each unique surface form gets exactly one placeholder so a
    sentence mentioning the same station twice — or the same ``↔``
    arrow twice — survives one round-trip through the translation
    model with identical tokens.

    Returns a tuple ``(masked_text, mapping)`` where ``mapping``
    resolves each placeholder back to the German surface form the
    feed should restore.
    """
    if not text:
        return text, {}

    mapping: dict[str, str] = {}
    surface_to_placeholder: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        surface = match.group(0)
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        placeholder = _ENTITY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = surface
        surface_to_placeholder[surface] = placeholder
        return placeholder

    working = _brand_entity_pattern().sub(_replace, text)
    station_pattern = _station_entity_pattern()
    if station_pattern is not None:
        working = station_pattern.sub(_replace, working)
    working = _LINE_ENTITY_RE.sub(_replace, working)
    working = _STREET_SUFFIX_RE.sub(_replace, working)
    working = _PRESERVED_SYMBOLS_RE.sub(_replace, working)
    return working, mapping


# Glossary placeholders use a distinct ``XGLO<n>X`` prefix so the
# combined unmask regex can tell them apart from the verbatim
# ``XENT<n>X`` placeholders emitted by :func:`_mask_entities`. The
# unmasker handles both via a single union regex (see
# :data:`_UNMASK_PLACEHOLDER_RE`).
_GLOSSARY_PLACEHOLDER_FORMAT = "XGLO" + _PLACEHOLDER_NONCE + "X{index}X"

# Unified regex for the unmasker — matches both entity (``XENT…``) and glossary
# (``XGLO…``) placeholders. Built from ``_ENTITY_PLACEHOLDER_RE.pattern`` (plus
# the sibling glossary shape) so the entity regex, the entity format, and this
# combined sweep stay in lock-step from a single source of truth. Defined at
# module import time so :func:`_unmask_entities` does not pay the compile cost
# per call.
_UNMASK_PLACEHOLDER_RE: re.Pattern[str] = re.compile(
    _ENTITY_PLACEHOLDER_RE.pattern + "|XGLO" + _PLACEHOLDER_NONCE + r"X\d+X"
)


def _apply_domain_glossary(
    text: str,
    *,
    source: str | None = None,
    category: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Substitute ÖPNV-domain German terms with placeholders that
    resolve to their canonical English equivalents.

    The Helsinki opus-mt-de-en model has only seen everyday German
    prose during training; it routinely mistranslates Austrian
    transit jargon because the closest token in its vocabulary lives
    in a different domain. Live regressions on the public feed
    (2026-05-22):

      * ``Betriebsstörung``        → "Harmful vehicle"
      * ``Fahrtbehinderung``       → "Disability"
      * ``Aufgelassen``            → "Open" (opposite meaning!)
      * ``Hauptfahrbahn``          → "main runway"

    This helper short-circuits the mistranslation: each known DE
    term is replaced by a stable ``XGLO<n>X`` placeholder whose
    mapping entry carries the *English* equivalent. After the model
    finishes translating the (now jargon-free) surrounding prose,
    :func:`_unmask_entities` substitutes the English term back in.

    The mask/unmask round-trip is therefore **DE → EN** for glossary
    entries (unlike :func:`_mask_entities` which is strictly
    identity). The two operations compose in
    :func:`_translate_text_attempt`.

    Metadata-driven layering: when ``source`` and/or ``category`` are
    passed in (typically extracted from the ``FeedItem`` by
    :func:`_format_item_content`), the active glossary is the merger
    of :data:`_GLOSSARY_BASE` with the matching overlays in
    :data:`_GLOSSARY_BY_SOURCE` / :data:`_GLOSSARY_BY_CATEGORY`. The
    overlay broadens coverage for vocabulary that is too narrow to
    apply universally (``Aufzug`` should only resolve to "elevator"
    for Wiener Linien items; ``Vollsperre`` only for Stadt Wien
    Baustellen items). Without metadata the function uses only the
    base layer — preserves backward compatibility for callers that
    do not care about per-item context.
    """
    if not text:
        return text, {}

    glossary = _resolve_glossary(source, category)
    pattern = _domain_glossary_pattern(source, category)

    mapping: dict[str, str] = {}
    surface_to_placeholder: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        surface = match.group(0)
        en_term = glossary.get(surface)
        if en_term is None:
            # Case-insensitive fallback (the glossary pattern matches
            # ``betriebsstörung`` at sentence start; the dict keys are
            # capitalised because that is how WL and ÖBB publish the
            # term in 99 % of items). Small dict, linear scan is fine.
            for key, value in glossary.items():
                if key.lower() == surface.lower():
                    en_term = value
                    break
        if en_term is None:
            # Pattern matched but resolution failed — return the
            # surface unchanged so no placeholder dangles in the
            # output. Defensive only; should not happen given the
            # pattern is built from the active glossary's keys.
            return surface
        cached = surface_to_placeholder.get(surface)
        if cached is not None:
            return cached
        placeholder = _GLOSSARY_PLACEHOLDER_FORMAT.format(index=len(mapping))
        mapping[placeholder] = en_term
        surface_to_placeholder[surface] = placeholder
        return placeholder

    processed = pattern.sub(_replace, text)
    return processed, mapping


def _unmask_entities(text: str, mapping: dict[str, str]) -> str:
    """Restore entity surface forms in ``text`` using ``mapping``.

    Handles BOTH placeholder formats emitted by the masking pipeline:

      * ``XENT<n>X`` — verbatim entity placeholders produced by
        :func:`_mask_entities` (brands, stations, lines, streets,
        symbols). Mapping value is the German surface form to
        restore.
      * ``XGLO<n>X`` — domain-glossary placeholders produced by
        :func:`_apply_domain_glossary`. Mapping value is the English
        equivalent of the German term.

    Tolerant of the translator dropping or reordering placeholders —
    only placeholders that still appear in ``text`` are restored, the
    rest are silently discarded so the output never carries a literal
    ``XENT3X`` / ``XGLO2X`` token to subscribers.
    """
    if not mapping:
        return text

    def _restore(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return mapping.get(placeholder, "")

    return _UNMASK_PLACEHOLDER_RE.sub(_restore, text)


def _get_translation_pipeline() -> Any:
    """Lazily instantiate the German → English translation pipeline.

    Returns the pipeline on success, ``None`` on any import or model-
    load failure (defensive: the EN feed degrades to the German
    original rather than crashing the build). State is held in a
    module-level dict to avoid the ``global`` declaration pattern
    CodeQL misclassifies as an unused-global write.
    """
    if _TRANSLATION_STATE["pipeline"] is not None:
        return _TRANSLATION_STATE["pipeline"]
    if _TRANSLATION_STATE["load_failed"]:
        return None
    try:
        from transformers import pipeline
        # ``translation_de_to_en`` is Hugging Face's runtime shorthand
        # for ``task="translation"`` with the language pair encoded in
        # the task name. The transformers package enumerates the
        # canonical tasks via ``Literal[...]`` ``@overload``\s, so the
        # shorthand does not match any overload under mypy strict.
        # The runtime accepts it (the docstring of ``pipeline`` lists
        # the shorthand explicitly); keeping the literal string for
        # spec parity and silencing the call-overload locally — the
        # ``unused-ignore`` companion handles environments where the
        # transformers package is loaded without overload metadata
        # (the import-untyped branch via ``ignore_missing_imports``).
        _TRANSLATION_STATE["pipeline"] = pipeline(  # type: ignore[call-overload, unused-ignore]
            "translation_de_to_en", model=_TRANSLATION_MODEL_NAME,
        )
        log.info(
            "Übersetzungs-Pipeline %s geladen.", _TRANSLATION_MODEL_NAME
        )
    except Exception as exc:
        _TRANSLATION_STATE["load_failed"] = True
        log.warning(
            "Übersetzungs-Pipeline konnte nicht geladen werden (%s) – "
            "EN-Feed nutzt Originaltext.",
            sanitize_log_arg(str(exc)),
        )
        return None
    return _TRANSLATION_STATE["pipeline"]


def _translate_text_attempt(
    text: str,
    ident: str = "",
    *,
    source: str | None = None,
    category: str | None = None,
) -> str | None:
    """Translate ``text`` from German to English with entity preservation.

    Pipeline:

      1. :func:`_mask_entities` replaces known brands, station names
         and ÖPNV line identifiers with alphanumeric placeholders so
         the ML model cannot mistranslate proper nouns.
      2. The masked text goes through the Hugging Face translation
         pipeline. ``truncation=True`` caps long inputs at the model's
         max context window instead of letting Marian crash on > 512
         tokens.
      3. :func:`_unmask_entities` restores the original surface forms
         from the placeholder mapping.

    Returns ``None`` on ANY failure (pipeline unavailable, runtime
    error, malformed output, empty result) so callers can distinguish
    "translated" from "had to fall back to the German source". Use
    :func:`_translate_text` if you want a safe fallback that always
    returns a string.

    ``ident`` is included in warning logs (sanitised) so the GitHub
    Actions log shows exactly which feed identities failed to
    translate — critical for diagnosing partial-translation drift.

    ``source`` and ``category`` (when supplied — they are the
    matching ``FeedItem`` fields) drive the metadata-aware glossary
    layering in :func:`_apply_domain_glossary`: operator-specific
    vocabulary activates only when the item came from that operator.
    """
    if not text or not text.strip():
        return None
    pipe = _get_translation_pipeline()
    if pipe is None:
        return None
    # Compose two mask passes:
    #   1. Domain-glossary substitution — DE jargon → ``XGLO<n>X``
    #      placeholders that resolve to canonical English terms. Runs
    #      FIRST so the entity masker afterwards sees the placeholder
    #      tokens (which look like opaque proper nouns) and leaves
    #      them untouched.
    #   2. Verbatim entity masking — brands, stations, lines, streets,
    #      Unicode symbols → ``XENT<n>X`` placeholders that resolve
    #      to the original German surface form.
    # The two mappings are merged into a single dict for unmasking
    # (each pass uses a distinct placeholder format so indices cannot
    # collide).
    glossary_processed, glossary_mapping = _apply_domain_glossary(
        text, source=source, category=category,
    )
    masked_text, entity_mapping = _mask_entities(glossary_processed)
    combined_mapping = {**glossary_mapping, **entity_mapping}
    try:
        # ``truncation=True`` enforces the model's input cap (512 tokens
        # for opus-mt-de-en) BEFORE Marian asserts and crashes the
        # whole feed build. Without it, a single long disruption text
        # would abort the EN-feed pass for every item that follows.
        result = pipe(masked_text, max_length=512, truncation=True)
    except Exception as exc:
        log.warning(
            "Translation failed for identity %s — pipeline raised %s: %s",
            sanitize_log_arg(ident or "<unknown>"),
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return None
    if not isinstance(result, list) or not result:
        log.warning(
            "Translation failed for identity %s — empty/invalid result shape.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    first = result[0]
    if not isinstance(first, dict):
        log.warning(
            "Translation failed for identity %s — result[0] not a dict.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    translated = first.get("translation_text")
    if not isinstance(translated, str) or not translated.strip():
        log.warning(
            "Translation failed for identity %s — translator returned empty text.",
            sanitize_log_arg(ident or "<unknown>"),
        )
        return None
    return _unmask_entities(translated, combined_mapping)


def _translate_text(
    text: str,
    ident: str = "",
    *,
    source: str | None = None,
    category: str | None = None,
) -> str:
    """Translate ``text`` from German to English; fall back to the source on failure.

    Thin wrapper around :func:`_translate_text_attempt` that converts
    the ``None`` failure signal into a safe-fallback string return.
    Kept as a public-style helper so callers that do not need to
    distinguish success from fallback have a single-string API; the
    cache-aware path inside :func:`_cached_translation` uses
    :func:`_translate_text_attempt` directly so a failed translation
    is NEVER cached as the canonical English text.

    ``source`` and ``category`` forward through to
    :func:`_translate_text_attempt` for metadata-aware glossary
    layering; default ``None`` preserves backward compatibility.
    """
    if not text or not text.strip():
        return text
    attempt = _translate_text_attempt(
        text, ident=ident, source=source, category=category,
    )
    return attempt if attempt is not None else text


def _translate_time_line_en(time_line: str) -> str:
    """Swap a leading German time-line prefix (e.g. ``Seit``) for English.

    ``time_line`` is the bracketed form emitted by
    :func:`_format_item_content` — e.g. ``[Seit 05.01.2026]`` or
    ``[05.01.2026 – 06.01.2026]``. Date-range strings without a
    German prefix word pass through unchanged.
    """
    if not time_line:
        return time_line
    stripped = time_line.strip().strip("[]").strip()
    if not stripped:
        return time_line
    for de_prefix, en_prefix in _TIME_PREFIX_DE_TO_EN.items():
        if stripped == de_prefix:
            return f"[{en_prefix}]"
        if stripped.startswith(f"{de_prefix} "):
            return f"[{en_prefix} {stripped[len(de_prefix) + 1:]}]"
    return time_line


def _cached_translation(
    text: str,
    field: str,
    ident: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> tuple[str, bool]:
    """Return the cached EN translation of ``text`` for ``ident``/``field``.

    Returns a tuple ``(translation, succeeded)`` where ``succeeded`` is
    ``True`` only when the ML pipeline actually produced a translation
    (either fresh or from the persistent cache). On failure the German
    source is returned with ``succeeded=False`` and **nothing is
    cached** — this fixes the "Sticky German" cache-corruption bug
    where a transient pipeline failure (model not yet downloaded,
    transformers import error, OOM) would poison the cache with the
    German source text and lock the item in German forever.

    Two further drift-protection guards:

      * If the persisted "translation" is byte-identical to the
        German source, treat it as a stale fallback from an earlier
        buggy build and retry. Real translations from a successful
        ML pass almost never equal the source verbatim (and the few
        that do — e.g. ``ÖBB`` alone — round-trip safely).
      * If ``state`` or ``ident`` is missing, fall through to a
        cache-less translation via :func:`_translate_text_attempt`.

    ``source`` and ``category`` forward through to
    :func:`_translate_text_attempt` for metadata-aware glossary
    layering. The translation cache key remains ``(ident, field)`` —
    item metadata is implicit in ``ident`` (one disruption belongs to
    exactly one operator), so the cache layout does not need
    additional axes.
    """
    if not text:
        return text, True  # empty input is trivially "translated"
    if state is None or not ident:
        attempt = _translate_text_attempt(
            text, ident=ident, source=source, category=category,
        )
        return (attempt, True) if attempt is not None else (text, False)
    entry = state.setdefault(ident, {})
    translations_raw = entry.get("translations")
    if not isinstance(translations_raw, dict):
        translations_raw = {}
        entry["translations"] = translations_raw
    en_raw = translations_raw.get("en")
    if not isinstance(en_raw, dict):
        en_raw = {}
        translations_raw["en"] = en_raw
    cached = en_raw.get(field)
    if isinstance(cached, str) and cached and cached != text:
        return cached, True
    if isinstance(cached, str) and cached == text:
        # Stale-fallback heuristic: a prior run cached the German
        # source as the "translation" — re-attempt now that the
        # pipeline may be healthy.
        log.info(
            "Cached EN translation for %s/%s equals source; retrying.",
            sanitize_log_arg(ident),
            sanitize_log_arg(field),
        )
    attempt = _translate_text_attempt(
        text, ident=ident, source=source, category=category,
    )
    if attempt is None:
        # Translation failed — do NOT persist the German source as
        # the "translation". The next run gets a clean retry.
        return text, False
    en_raw[field] = attempt
    return attempt, True


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


def _prune_expired_merged_state(
    merged_state: dict[str, dict[str, Any]], state: dict[str, dict[str, Any]]
) -> None:
    """Drop resurrected, retention-expired entries from ``merged_state`` in place.

    ``_load_state`` drops expired entries from the in-memory working set, but
    ``_save_state`` re-reads the raw on-disk file via ``_read_state_capped`` and
    merges it — so an entry this run no longer tracks (another run's items,
    parallel-writer leftovers) is otherwise resurrected and re-written every
    cycle, growing the file until it trips ``MAX_STATE_FILE_BYTES`` and the
    loader wipes *all* first_seen tracking at once. Prune only entries NOT in
    this run's ``state``: those are already within retention via ``_load_state``
    and must keep their freshly written first_seen for cross-run dedup /
    parallel-writer safety (a recent parallel-writer entry has a recent
    first_seen and is kept; unparseable first_seen -> kept).
    """
    if feed_config.STATE_RETENTION_DAYS <= 0:
        return
    retention_cutoff = _to_utc(datetime.now(UTC)) - timedelta(
        days=feed_config.STATE_RETENTION_DAYS
    )
    for ident in list(merged_state.keys()):
        if ident in state:
            continue
        entry = merged_state.get(ident)
        if not isinstance(entry, dict):
            continue
        fs_utc = _parse_first_seen(entry, None)
        if fs_utc is not None and fs_utc < retention_cutoff:
            merged_state.pop(ident, None)


def _write_merged_state(
    path: Path, state: dict[str, dict[str, Any]], deletions: set[str] | None
) -> None:
    """Merge ``state`` into the on-disk state file and atomically rewrite it.

    The caller MUST hold the exclusive state lock. Reads the existing file under
    the byte-size cap, layers this run's ``state`` on top, drops ``deletions``,
    prunes retention-expired survivors, then writes via ``atomic_write``.
    """
    # Safe merge: read existing state to avoid overwriting parallel updates.
    # Security: open-then-fstat closes the TOCTOU between the size cap and
    # ``open``. ``read(MAX + 1)`` defends against zero-st_size special files.
    # See _load_state.
    merged_state = _read_state_capped(path)
    merged_state.update(state)
    if deletions:
        for k in deletions:
            merged_state.pop(k, None)
    # Prune resurrected, retention-expired entries so the on-disk state file
    # cannot grow until it trips ``MAX_STATE_FILE_BYTES`` and the loader wipes
    # *all* first_seen tracking at once.
    _prune_expired_merged_state(merged_state, state)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as f:
        # Security (Trojan-Source / BiDi-Mark Drift Round 10):
        # ``ensure_ascii=True`` escapes every non-ASCII code point as a literal
        # ``\uXXXX`` sequence. The state dict's KEYS carry feed-item identities
        # computed by ``_identity_for_item`` — the WL/non-OEBB fallback branches
        # embed the raw provider title verbatim. A planted upstream title
        # carrying U+202E (RIGHT-TO-LEFT OVERRIDE) / zero-width / Unicode
        # line-separator / 8-bit C1 bytes would otherwise reach
        # ``data/first_seen.json`` — a file committed to ``main`` by
        # ``build-feed.yml`` — as raw UTF-8, triggering BiDi reversal in any
        # ``cat`` / ``less`` / GitHub web UI / IDE viewer. Mirrors the canonical
        # fix shape pinned in PR #1434 for ``_write_quarantine_file``. Forensic
        # intent is preserved (``json.loads`` recovers the original bytes from
        # the literal escape sequence).
        #
        # Security (Coordinate finite/range drift, committed-writer
        # defence-in-depth): ``allow_nan=False`` mirrors the canonical writer-side
        # pin (Round 1485, :func:`src.places.merge.write_stations`). A planted
        # ``NaN`` / ``Infinity`` literal in a previous-run ``data/first_seen.json``
        # survives the ``json.loads`` round-trip and re-writes verbatim without
        # the pin. ``ensure_ascii=True`` already blocks Trojan-Source primitives;
        # ``allow_nan=False`` closes the sibling RFC-8259 non-conformance drift.
        json.dump(merged_state, f, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)


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
                _write_merged_state(path, state, deletions)
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
    # ``errors="surrogatepass"`` (here and the two ``json.dumps`` hashes below):
    # a lone surrogate (U+D800-U+DFFF, reachable via a ``\uD800`` escape in an
    # upstream JSON title) would otherwise raise ``UnicodeEncodeError`` at this
    # identity-hash encode — BEFORE ``_sanitize_text`` strips it from the
    # rendered title — aborting the whole build. ``surrogatepass`` lets the
    # hash encode the bytes deterministically; the hash value is unchanged for
    # every surrogate-free input (the normal case), so no first_seen churn.
    fuzzy_hash = hashlib.sha256(
        fuzzy_raw.encode("utf-8", "surrogatepass")
    ).hexdigest()

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
                    hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
                    result = f"{base}|H={hashed}|F={fuzzy_hash}"
            else:
                result = f"{base}|F={fuzzy_hash}"
        # Fallback: Ohne Quelle/Kategorie Titel oder vollständigen Hash anhängen
        elif item.get("title"):
            result = f"{base}|T={item['title']}|F={fuzzy_hash}"
        else:
            raw = json.dumps(item, sort_keys=True, default=str)
            hashed = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
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

    # Pre-compute the per-group worker limit across ALL fetchers BEFORE
    # the main submit loop. Pre-fix the loop only registered a
    # semaphore when the CURRENT fetcher's own per-provider env-limit
    # was set, so a sibling provider in the same ``concurrency_key``
    # group without its own env override ran unbounded — silently
    # defeating the operator-intended shared cap. If two group members
    # set different positive limits, the tighter one (``min``) wins so
    # the group respects every member's stated upper bound.
    group_limits: dict[str, int] = {}
    for fetch in network_fetchers:
        provider_name = provider_names.get(fetch, _provider_display_name(fetch))
        env_name = provider_envs.get(fetch)
        concurrency_key = _provider_concurrency_key(fetch, provider_name)
        worker_limit = _provider_worker_limit(
            fetch, env_name, provider_name, concurrency_key
        )
        if worker_limit is not None and worker_limit > 0:
            current = group_limits.get(concurrency_key)
            group_limits[concurrency_key] = (
                min(current, worker_limit) if current is not None else worker_limit
            )
    semaphores: dict[str, BoundedSemaphore] = {
        key: BoundedSemaphore(limit) for key, limit in group_limits.items()
    }

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
        # Every member of a group with a positive limit picks up the
        # pre-computed shared semaphore — including members whose OWN
        # ``worker_limit`` resolved to ``None``.
        semaphore: BoundedSemaphore | None = semaphores.get(concurrency_key)
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
        ``docs/architecture.md`` §1 for the full sequence diagram.
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
    """Entferne Items, die nicht mehr gültig oder zu lange im Feed sind.

    Zwei Regeln:

    1. **Ungültig → sofort raus:** ein ``ends_at`` in der Vergangenheit
       (über die ``ENDS_AT_GRACE_MINUTES`` hinaus) bedeutet, die Störung ist
       behoben → die Meldung macht sofort Platz.
    2. **Alter → FIFO nach ``first_seen``:** das Alter zählt ab dem
       Auftauchen im Feed (``first_seen`` aus dem State), NICHT ab dem
       Quell-Startdatum. So fällt ein aktiver Langläufer nicht wegen eines
       alten Startdatums heraus; ein noch nicht im State vermerktes Item ist
       brandneu und wird hier nie ausgemustert.
    """

    out: list[FeedItem] = []
    dropped: set[str] = set()
    now_utc = _to_utc(now)
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]

        ident, state_entry = _lookup_state(it, state)

        ends_at = it.get("ends_at")
        if isinstance(ends_at, datetime):
            if _to_utc(ends_at) < now_utc - timedelta(minutes=feed_config.ENDS_AT_GRACE_MINUTES):
                dropped.add(ident)
                continue

        # Age out by how long the item has been in the feed (first_seen) —
        # FIFO by appearance ("man kennt die Meldung schon"). The source
        # start date is deliberately NOT used, so an active long-runner that
        # only recently surfaced is not retired for an old upstream start;
        # an item not yet in the state is brand-new and never aged out here.
        first_seen_dt = _parse_first_seen(state_entry, None)
        age_days: float | None = (
            (now_utc - first_seen_dt).total_seconds() / 86400.0
            if first_seen_dt is not None
            else None
        )

        if age_days is not None:
            if age_days > feed_config.ABSOLUTE_MAX_AGE_DAYS:
                dropped.add(ident)
                continue
            if age_days > feed_config.MAX_ITEM_AGE_DAYS:
                if not isinstance(ends_at, datetime):
                    dropped.add(ident)
                    continue

        out.append(it)

    # Duplicate-identity guard: when two items share a state-key (e.g. a
    # duplicate-guid pair across providers / plugins) and one expired or
    # aged out while the other survives, the loop above added the shared
    # identity to ``dropped`` from the expired sibling AND will re-emit the
    # state entry from the survivor in ``_make_rss``. Pre-fix
    # ``_save_state`` then unconditionally pruned the freshly-written
    # survivor entry — silent perpetual churn (re-publish via fresh
    # pubDate, FIFO age retirement disabled). Subtract the survivors'
    # identities so ``dropped`` carries only items with no surviving
    # twin.
    if dropped:
        dropped -= {_lookup_state(it, state)[0] for it in out}
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


def _state_key_for_item(it: FeedItem) -> str:
    """Return the persistent ``first_seen`` state key for ``it``.

    Prefers the provider ``guid`` — stable across upstream title/description
    edits and the read-side title enrichment — so an item's ``first_seen``
    (and the age-based retirement that relies on it) survives those changes.
    Falls back to the content identity when no guid is present, so EVERY
    item, provider-independently, still gets a first_seen entry.
    """
    guid = it.get("guid")
    if guid:
        return str(guid)
    return _identity_for_item(it)


def _lookup_state(
    it: FeedItem, state: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any] | None]:
    """Return ``(stable_key, entry)`` for ``it``'s first_seen state.

    Prefers the guid-based key (:func:`_state_key_for_item`); on a miss it
    falls back to a legacy identity-keyed entry so first_seen migrates
    without a reset. Shared by the writer (:func:`_update_item_state`) and
    the age check (:func:`_drop_old_items`) so both stay consistent.
    """
    key = _state_key_for_item(it)
    entry = state.get(key)
    if entry is None:
        legacy = _identity_for_item(it)
        if legacy != key:
            entry = state.get(legacy)
    return key, entry


def _parse_first_seen(
    entry: dict[str, Any] | None, fallback: datetime | None
) -> datetime | None:
    """Return the UTC ``first_seen`` datetime from a state ``entry``.

    Falls back to ``fallback`` when the entry is absent or its ``first_seen``
    is missing/unparseable.
    """
    if not entry:
        return fallback
    raw = entry.get("first_seen")
    if raw is None:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return _to_utc(parsed)


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
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        # The state is persisted under the guid-preferring key scheme
        # (:func:`_state_key_for_item`, with a legacy-identity fallback) used
        # by both the writer and the age-out. Comparing the raw content
        # identity (:func:`_identity_for_item`) against guid-keyed state
        # counted every guid-bearing item as "new" on every run — use the
        # same lookup so an already-tracked item is recognised.
        _key, entry = _lookup_state(it, state)
        if entry is None:
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

# Feed-ordering priority by category, applied as a tie-breaker when several
# items share the same ``first_seen``. Lower rank sorts first. ``Störung``
# (WL/ÖBB live disruptions AND the Stammstrecke delay/cancellation monitor,
# whose ``EVENT_CATEGORY`` is also ``"Störung"``) leads; ``Baustelle``
# (long-running construction) trails; every other category (e.g. WL
# ``Hinweis``) sits between. Keyed on the casefolded category so a provider
# emitting ``"störung"`` still matches. Operator policy (2026-05): disruptions
# matter more for the feed than construction.
_CATEGORY_FEED_RANK: dict[str, int] = {
    "störung": 0,
    "baustelle": 2,
}
_DEFAULT_CATEGORY_FEED_RANK = 1


def _category_feed_rank(item: FeedItem) -> int:
    """Return the feed-priority rank for *item*'s category (lower sorts first)."""
    category = item.get("category")
    if isinstance(category, str):
        return _CATEGORY_FEED_RANK.get(
            category.strip().casefold(), _DEFAULT_CATEGORY_FEED_RANK
        )
    return _DEFAULT_CATEGORY_FEED_RANK


def _recency_sort_key(
    item: FeedItem, state: dict[str, dict[str, Any]], now_utc: datetime
) -> tuple[float, int, float, str]:
    """FIFO-by-``first_seen`` ordering for the feed: newest-appeared first.

    Sorts by the persisted (guid-stable) ``first_seen`` descending. An item
    not yet in the state counts as just-appeared (``now``) so genuinely new
    disruptions lead. No-longer-valid items are already removed upstream by
    :func:`_drop_old_items`, so the visible Top-N are the newest still-valid
    disruptions; older ones fall off the bottom as fresher ones arrive.

    Ties on ``first_seen`` (a batch of items that surfaced in the SAME build —
    e.g. a fresh set of construction sites that all get ``first_seen = now``)
    are broken, in order, by:

    1. **Category priority** (:func:`_category_feed_rank`) — live disruptions
       (``Störung``: WL/ÖBB plus the Stammstrecke delay/cancellation monitor)
       outrank long-running ``Baustelle`` items; everything else (e.g. WL
       ``Hinweis``) sits between. Operator policy: Störungen are more important
       for the feed than Baustellen.
    2. **``pubDate`` descending** — the source's own recency signal; the more
       recently published item leads. A missing/unparseable ``pubDate`` sorts
       last within its group (``-inf`` → ``+inf`` after negation).
    3. **guid** — final deterministic tiebreaker for a stable total order
       (otherwise items tying on all of the above would shuffle between builds).
    """
    _, entry = _lookup_state(item, state)
    first_seen = _parse_first_seen(entry, now_utc) or now_utc
    pub = _parse_datetime(item.get("pubDate"))
    pub_ts = pub.timestamp() if isinstance(pub, datetime) else float("-inf")
    guid_val = item.get("guid")
    guid_str = str(guid_val) if guid_val else _identity_for_item(item)
    return (-first_seen.timestamp(), _category_feed_rank(item), -pub_ts, guid_str)


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


def _placeholder_collides_with_formatted(
    ph_content: str, ph_title: str, formatted: FormattedContent
) -> bool:
    """Return True if either placeholder appears in any text-bearing field
    of *formatted*.

    Security (CDATA placeholder collision drift closure):
    ``_emit_item`` injects ``ph_content`` / ``ph_title`` as the ``.text``
    of ``<content:encoded>`` / ``<title>`` ElementTree elements and
    later substitutes them via a global ``xml_str.replace(...)`` pass
    for CDATA-wrapped content. The downstream replace is element-agnostic
    — any occurrence of either placeholder ANYWHERE in the serialised
    XML gets replaced, including inside ``<link>`` / ``<guid>`` /
    ``<description>`` element text whose values come from upstream-
    controlled fields. Pre-fix the loop only verified absence in three
    of the eight ``FormattedContent`` text fields (``desc_html``,
    ``raw_desc``, ``title_out``); the remaining five (``link``, ``guid``,
    ``desc_text_truncated``, ``title_cdata``, ``desc_cdata``) leaked
    upstream-controlled spans into the placeholder-replacement target
    set without a collision check. An upstream item whose ``link`` or
    ``guid`` coincidentally matched the random placeholder pattern
    would corrupt the serialised RSS XML (CDATA injected into the
    wrong element, original element text consumed by the replacement).
    Practical exploitability requires predicting a 128-bit UID
    (astronomically low for a remote attacker), but the project's
    Zero-Trust upstream contract (AGENTS.md §3) demands defense-in-
    depth at every upstream-data boundary regardless of practical
    exploitability — a legitimate URL that happens to embed the
    placeholder pattern (highly unlikely yet possible) would silently
    corrupt the public feed. The check examines every text-bearing
    ``FormattedContent`` field, mirroring the ``isinstance`` Zero-Trust
    guards added at every other upstream JSON-parse site
    (``src/providers/vor.py``, ``src/providers/wl_fetch.py``,
    ``src/places/client.py``).
    """
    text_fields = (
        formatted.link,
        formatted.guid,
        formatted.title_out,
        formatted.title_cdata,
        formatted.desc_html,
        formatted.desc_cdata,
        formatted.desc_text_truncated,
        formatted.raw_desc,
    )
    return any(ph_content in field or ph_title in field for field in text_fields)


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
    ident, st = _lookup_state(it, state)
    is_strictly_new = not st
    if not st:
        st = {"first_seen": _to_utc(now).isoformat()}
    state[ident] = st
    # Legacy-key migration cleanup: ``_lookup_state`` returns the modern
    # guid-shaped key in ``ident``, but on a guid-key miss it falls back
    # to the legacy ``_identity_for_item`` entry to migrate forward. Pre-
    # fix we wrote the entry under the new guid key but left the legacy
    # entry on disk; ``STATE_RETENTION_DAYS`` (60d) bounded the bloat,
    # but every subsequent build kept comparing the same now-redundant
    # legacy key in ``_lookup_state``'s fallback path until the prune
    # eventually fired. Dropping the legacy entry once the migration has
    # written the new key keeps ``data/first_seen.json`` tidy and saves
    # the legacy-lookup cost on every cycle.
    legacy = _identity_for_item(it)
    if legacy != ident and legacy in state:
        state.pop(legacy, None)

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


# Category-prefix duplicate stripping is extracted from
# :func:`_format_item_content` so the complexity gate (C901, baseline 31)
# still tolerates the lang/state additions introduced for the bilingual
# feed. The block was a tight-knit sub-block of the formatter (no
# external dependencies beyond ``re`` and the inputs) — pulling it out
# preserves behaviour byte-for-byte while freeing ~8 branches from the
# caller.
_CATEGORY_PREFIX_WORDS: frozenset[str] = frozenset({
    "bauarbeiten",
    "gleisbauarbeiten",
    "straßenbauarbeiten",
    "strassenbauarbeiten",
    "rohrleitungsarbeiten",
    "kranarbeiten",
    "brückenbauarbeiten",
    "brueckenbauarbeiten",
    "brückenarbeiten",
    "brueckenarbeiten",
    "schienenarbeiten",
    "veranstaltung",
    "demonstration",
    "filmaufnahmen",
    "falschparker",
})

_TITLE_BODY_RE = re.compile(r"^[A-Za-z0-9/]+:\s*(\S.*)$")
_DATE_RANGE_PREFIX_RE = re.compile(
    r"^\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}\s*•\s*"
)
_DATE_SINGLE_PREFIX_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}\s*•\s*")


def _drop_category_word(summary: str, word: str) -> str:
    """Strip ``word`` (and a following separator) from the start of summary."""
    leading = re.match(
        rf"^{re.escape(word)}\s*[:.,;–—-]?\s+", summary, re.IGNORECASE
    )
    if leading:
        return summary[leading.end():].strip()
    return summary


def _strip_summary_category_prefix(summary: str, raw_title: str) -> str:
    """Remove a leading category H2 word that duplicates the title body
    or signals an HTML-heading leak.

    Real WL Hinweis items render with a ``<h2>Gleisbauarbeiten</h2>
    <p>Wegen …</p>`` HTML pair. After HTML-to-text conversion the
    heading word lands at the start of the body, producing redundant
    openings. Three patterns are recognised:

      1. ``T: "9/40/41/42: Gleisbauarbeiten"`` /
         ``D: "Gleisbauarbeiten Wegen ..."`` — first words match.
      2. ``T: "62A: Busse halten ..."`` /
         ``D: "Bauarbeiten Busse halten ..."`` — category prepended to
         an otherwise title-equivalent description.
      3. ``T: "27A/28A/29A: Fronleichnamsumzug"`` /
         ``D: "Veranstaltung Wegen Abhaltung des …"`` — the leading
         word is a known WL category AND the next word is ``Wegen``,
         so the body restates the cause and the heading word is pure
         noise. This catches the audit-round-7 cases where the title
         body is unrelated to the category word (Fronleichnamsumzug,
         Filmaufnahmen, Dornbacher Straße, …).
    """
    if not summary:
        return summary

    words = summary.split()
    if not words:
        return summary
    first_summary_word = words[0]
    summary_cf = first_summary_word.casefold()
    if summary_cf not in _CATEGORY_PREFIX_WORDS:
        return summary

    # Branch 3 — heading-leak via ``<Category> Wegen <body>``. Does not
    # depend on the title shape because real German prose never opens
    # a sentence with the bare category word followed by ``Wegen``;
    # the only producer of that pattern is the WL HTML heading leak.
    if len(words) >= 2 and words[1].casefold() == "wegen":
        return _drop_category_word(summary, first_summary_word)

    # Branches 1 & 2 — title-body comparison.
    title_match = _TITLE_BODY_RE.match(raw_title or "")
    title_body = title_match.group(1).strip() if title_match else (raw_title or "").strip()
    if not title_body:
        return summary

    first_title_word = title_body.split()[0]
    title_cf = first_title_word.casefold()
    if summary_cf == title_cf:
        return _drop_category_word(summary, first_summary_word)

    if len(words) >= 2 and words[1].casefold() == title_cf:
        return _drop_category_word(summary, first_summary_word)
    return summary


# Truncation tail-cleanup helpers — extracted from
# :func:`_format_item_content` for the same C901 budget reason as
# :func:`_strip_summary_category_prefix` above. Both blocks were tight
# self-contained sub-blocks; pulling them out preserves behaviour.
_TRUNCATION_PUNCT_STRIP = " ,;:-)/"
_TRUNCATION_UNIT_TOKENS: frozenset[str] = frozenset(
    {"Uhr", "min", "sec", "h", "km", "kg", "m", "cm", "s", "ms"}
)


def _should_drop_trailing_tail(tail: str) -> bool:
    """Decide whether the trailing token after a truncation rsplit is noise.

    Drops short German abbreviations, all-uppercase line markers, bare
    numbers, and standalone unit tokens; preserves real content words.
    """
    tail_stripped = tail.rstrip(".")
    ends_with_period = tail.endswith(".")
    if not tail_stripped:
        return True
    if len(tail) > 5:
        return False
    if ends_with_period and tail_stripped.isalpha():
        return True
    if ends_with_period and tail_stripped.isdigit():
        return True
    if tail_stripped.isdigit():
        return True
    if tail_stripped.isalpha() and tail_stripped.isupper():
        return True
    return tail in _TRUNCATION_UNIT_TOKENS or tail_stripped in _TRUNCATION_UNIT_TOKENS


def _trim_truncation_tail(truncated: str) -> str:
    """Iteratively drop noise tokens at the end of a hard-truncated summary."""
    for _ in range(8):
        truncated = truncated.rstrip(_TRUNCATION_PUNCT_STRIP)
        last_space = truncated.rfind(" ")
        if last_space <= 0:
            break
        tail = truncated[last_space + 1:]
        if _should_drop_trailing_tail(tail):
            truncated = truncated[:last_space]
        else:
            break
    return truncated


def _truncate_summary_180(summary: str) -> str:
    """Hard-limit ``summary`` to 180 characters with TV-friendly tail cleanup."""
    if len(summary) <= 180:
        return summary
    truncated = summary[:175].rsplit(" ", 1)[0]
    truncated = _trim_truncation_tail(truncated)
    if truncated.count("(") > truncated.count(")"):
        last_open = truncated.rfind("(")
        if last_open >= 0:
            truncated = truncated[:last_open].rstrip(_TRUNCATION_PUNCT_STRIP)
    return truncated.rstrip(_TRUNCATION_PUNCT_STRIP) + " …"


def _compose_description(summary: str, time_line: str) -> tuple[str, str]:
    """Assemble (desc_text_truncated, desc_html) from summary + time_line."""
    desc_parts: list[str] = []
    if summary:
        desc_parts.append(summary)
    if time_line:
        desc_parts.append(time_line)
    # Security (stored HTML/JS injection on the public feed): ``summary`` and
    # ``time_line`` are PLAIN TEXT, but ``desc_html`` is emitted verbatim into
    # the ``<content:encoded>`` CDATA body, which every conformant RSS reader
    # renders as HTML. ``html_to_text`` decodes entity-escaped angle brackets
    # (``&lt;img onerror=…&gt;`` → ``<img onerror=…>``) via
    # ``HTMLParser(convert_charrefs=True)``, so a compromised/MITM'd upstream
    # could otherwise land an executable tag in subscribers' readers. Escape
    # each text part for the HTML context; only the builder's own structural
    # ``<br/>`` separators stay live. ``desc_text_truncated`` below stays PLAIN
    # display text here (so the WL directional ``>`` markers, line-prefix logic
    # and truncation tests keep operating on the unescaped form); contextual
    # output-encoding for it happens at its HTML-rendered ``<description>`` sink
    # in :func:`_emit_item`.
    desc_html = "<br/>".join(html.escape(part, quote=False) for part in desc_parts)
    desc_text = " ".join(desc_parts)
    if len(desc_text) > feed_config.DESCRIPTION_CHAR_LIMIT:
        desc_text_truncated = (
            desc_text[: feed_config.DESCRIPTION_CHAR_LIMIT].rstrip()
            + "... [TRUNCATED]"
        )
    else:
        desc_text_truncated = desc_text
    desc_html = truncate_html(
        desc_html,
        feed_config.DESCRIPTION_CHAR_LIMIT,
        ellipsis="... [TRUNCATED]",
    )
    return desc_text_truncated, desc_html


def _summary_duplicates_title(summary: str, title_out: str) -> bool:
    """Return True when summary is just the title body restated verbatim."""
    if not (summary and title_out):
        return False
    title_body_match = _TITLE_BODY_RE.match(title_out)
    title_body_compare = (
        title_body_match.group(1).strip() if title_body_match else title_out
    )
    if not title_body_compare:
        return False
    return summary.casefold() == title_body_compare.casefold()


def _evict_stale_translations(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Drop cached EN translations that predate the current
    :data:`_TRANSLATION_CACHE_EPOCH`.

    Called at the top of :func:`_apply_lang_overlay` before any cache
    lookup. When the persisted epoch is older than the current one the
    item was translated by a build with weaker masking / glossary
    logic; the whole ``en`` sub-dict is removed so every field is
    recomputed through the improved pipeline. The epoch is re-stamped
    only AFTER a successful retranslation (see
    :func:`_stamp_translation_epoch`) so a transient pipeline failure
    cannot lock in a half-empty cache at the current epoch.
    """
    if state is None or not ident:
        return
    entry = state.get(ident)
    if not isinstance(entry, dict):
        return
    translations = entry.get("translations")
    if not isinstance(translations, dict):
        return
    epoch_raw = translations.get("epoch", 0)
    epoch = epoch_raw if isinstance(epoch_raw, int) else 0
    if epoch < _TRANSLATION_CACHE_EPOCH:
        if "en" in translations:
            log.info(
                "Evicting stale EN translation for %s (epoch %s < %s).",
                sanitize_log_arg(ident),
                epoch,
                _TRANSLATION_CACHE_EPOCH,
            )
        translations.pop("en", None)


def _stamp_translation_epoch(
    ident: str, state: dict[str, dict[str, Any]] | None
) -> None:
    """Record that ``ident``'s cached translations were produced under
    the current :data:`_TRANSLATION_CACHE_EPOCH`.

    Called only after every field translated successfully, so a future
    build trusts the cache instead of evicting it again (no churn even
    for items whose translation legitimately drops a placeholder).
    """
    if state is None or not ident:
        return
    entry = state.setdefault(ident, {})
    translations = entry.setdefault("translations", {})
    if isinstance(translations, dict):
        translations["epoch"] = _TRANSLATION_CACHE_EPOCH


def _apply_lang_overlay(
    base: FormattedContent,
    summary_de: str,
    time_line_de: str,
    ident: str,
    lang: str,
    state: dict[str, dict[str, Any]] | None,
    *,
    source: str | None = None,
    category: str | None = None,
) -> FormattedContent:
    """Translate the German formatted output into English, if requested.

    Cache lookups go through :func:`_cached_translation` so each
    disruption identity is translated exactly once per lifecycle (the
    EN strings are persisted in ``state[ident]["translations"]["en"]``
    and round-trip through :func:`_save_state`).

    **Per-item atomic fallback contract**: the EN feed promises the
    same content as the DE feed, only in another language. To honour
    that promise an item must be either fully translated (title +
    summary + time-line) OR a verbatim copy of the German source — a
    mixed-language item or a "Partially translated" marker would mean
    the EN subscriber sees information the DE subscriber does not,
    which violates the content-parity contract requested by the
    operator. When ``_cached_translation`` reports a failure on
    either field the function returns ``base`` unchanged so the EN
    feed item is byte-identical to the DE item for that disruption.

    For ``lang != "en"`` the input is returned unchanged.

    ``source`` / ``category`` (normalised
    :data:`FeedItem` fields) drive metadata-aware glossary layering
    inside the translation cascade. Default ``None`` keeps the
    function callable from the existing tests that do not need to
    exercise the metadata path.

    Cache freshness: cached translations are tagged with the
    :data:`_TRANSLATION_CACHE_EPOCH` they were produced under. Stale
    entries (older epoch) are evicted up-front so a masking / glossary
    improvement is picked up on the next build instead of serving the
    old rendering for the lifetime of the item.
    """
    if lang != "en":
        return base

    # Pick up masking / glossary improvements: discard cached
    # translations produced under an older epoch so the lookups below
    # recompute them. Re-stamped at the end only when every field
    # translated successfully.
    _evict_stale_translations(ident, state)

    title_raw, title_ok = _cached_translation(
        base.title_out, "title", ident, state,
        source=source, category=category,
    )
    if summary_de:
        summary_raw, summary_ok = _cached_translation(
            summary_de, "summary", ident, state,
            source=source, category=category,
        )
    else:
        summary_raw = ""
        summary_ok = True

    if not (title_ok and summary_ok):
        log.info(
            "Translation incomplete for identity %s "
            "(title_ok=%s, summary_ok=%s) — EN feed item falls back to "
            "the German source verbatim to preserve DE↔EN content parity.",
            sanitize_log_arg(ident or "<unknown>"),
            title_ok,
            summary_ok,
        )
        return base

    # Every field translated under the current epoch — stamp it so the
    # next build trusts the cache instead of evicting and recomputing.
    _stamp_translation_epoch(ident, state)

    # Mirror the DE length contract (see ``_format_item_content`` lines
    # ~3826-3839): German→English expansion (compound nouns split into
    # several words) can push a translated title past ``TITLE_CHAR_LIMIT``
    # or a summary past the 180-char TV-screen cap. The DE strings fed into
    # translation were already capped, so re-apply both caps to the
    # translated output to keep the EN feed within the same contract.
    title_en = _sanitize_text(title_raw)
    if len(title_en) > feed_config.TITLE_CHAR_LIMIT:
        title_en = title_en[: feed_config.TITLE_CHAR_LIMIT].rstrip() + " …"
    title_en = _WHITESPACE_RE.sub(" ", title_en).strip()
    summary_en = _truncate_summary_180(_sanitize_text(summary_raw))
    time_line_en = _translate_time_line_en(time_line_de)
    desc_text_truncated_en, desc_html_en = _compose_description(
        summary_en, time_line_en
    )
    return FormattedContent(
        guid=base.guid,
        link=base.link,
        title_cdata=_cdata_content(title_en),
        desc_text_truncated=desc_text_truncated_en,
        desc_cdata=_cdata_content(desc_html_en),
        raw_desc=base.raw_desc,
        title_out=title_en,
        desc_html=desc_html_en,
    )


def _format_item_content(
    it: FeedItem,
    ident: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
    *,
    lang: str = "de",
    state: dict[str, dict[str, Any]] | None = None,
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
    summary = _DATE_RANGE_PREFIX_RE.sub("", summary)
    summary = _DATE_SINGLE_PREFIX_RE.sub("", summary)

    # Bulletpoints auflösen, um einen fließenden Satz zu bilden
    summary = summary.replace(" • ", " ").replace("•", " ")
    # WL uses ``#`` as an internal street-junction marker — real cache
    # text reads ``ab Engerthstraße # Elderschplatz über …`` meaning
    # "starting from the Engerthstraße / Elderschplatz intersection".
    # The bare ``#`` glyph looks like a stray hashtag to a feed
    # subscriber, so swap it for ``/`` which mirrors WL's own
    # ``40/41`` line-separator convention and reads as a junction
    # marker without ambiguity. All 46 current cache occurrences
    # carry whitespace on both sides (``\\s+#\\s+``), so the
    # space-anchored swap can't accidentally touch a hashtag inside
    # quoted strings or a URL fragment.
    summary = summary.replace(" # ", " / ")
    summary = _WHITESPACE_CLEANUP_RE.sub(" ", summary).strip()

    # Doppelte Kategorie-Wortpräfixe entfernen (siehe Helper-Docstring).
    summary = _strip_summary_category_prefix(summary, raw_title)

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

    # Harte Begrenzung für den TV-Screen (max. 180 Zeichen).
    # Die Tail-Cleanup-Iteration (Abkürzungen, Linienkürzel, Einheits-
    # Tokens, unbalancierte Klammer) wurde in :func:`_truncate_summary_180`
    # extrahiert, damit die C901-Komplexitätsgrenze (Baseline 31) Platz
    # für den lang/state-Overlay behält.
    summary = _truncate_summary_180(summary)

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

    summary = _strip_trailing_directional_marker(summary)

    # Skip the summary entirely when it would just repeat the title
    # body verbatim. WL Störung items like ``41E: Ersatzbus 41E halten
    # bei Währinger Str 200`` produce a description that's identical
    # to the title body after the line-prefix is stripped — surfacing
    # both gives the user the same text twice. We compare casefold so
    # ``Linie 11A: Verspätung.`` and ``Verspätung`` are not flagged
    # as duplicates (different content).
    if _summary_duplicates_title(summary, title_out):
        summary = ""

    desc_text_truncated, desc_html = _compose_description(summary, time_line)

    # Prepare CDATA content (handle ]]> in content)
    desc_cdata = _cdata_content(desc_html)

    base = FormattedContent(
        guid, link, title_cdata, desc_text_truncated, desc_cdata,
        raw_desc, title_out, desc_html,
    )
    # Extract the per-item metadata that drives glossary layering in
    # the EN translation cascade. Both fields are normalised to
    # ``None`` for empty / non-string values so the downstream
    # ``_resolve_glossary`` cache key is stable across the
    # ``None``/``""``/``"  "`` edge cases.
    source_meta = _norm_metadata(it.get("source"))
    category_meta = _norm_metadata(it.get("category"))
    return _apply_lang_overlay(
        base, summary, time_line, ident, lang, state,
        source=source_meta, category=category_meta,
    )


def _emit_item(
    it: FeedItem,
    now: datetime,
    state: dict[str, dict[str, Any]],
    *,
    lang: str = "de",
) -> tuple[str, ET.Element, dict[str, str]]:
    """Convert a normalized item dictionary into an RSS <item> element and CDATA replacements.

    Args:
        it: The normalized item dictionary.
        now: The current datetime (used for relative time calculations).
        state: The state dictionary (used to persist first_seen timestamps).
        lang: Target output language (``"de"`` or ``"en"``). When ``"en"``
            the formatter applies a translation overlay (cached in
            ``state[ident]["translations"]["en"]``).

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
        lang=lang,
        state=state,
    )

    if not isinstance(pubDate, datetime) and feed_config.FRESH_PUBDATE_WINDOW_MIN > 0:
        age = _to_utc(now) - _to_utc(fs_dt)
        if age <= timedelta(minutes=feed_config.FRESH_PUBDATE_WINDOW_MIN):
            pubDate = now

    # Generate unique placeholders.
    # We use a cryptographically secure random token to ensure uniqueness within the document.
    # ``_placeholder_collides_with_formatted`` verifies the candidate
    # placeholders do not appear in ANY of the eight text-bearing
    # ``FormattedContent`` fields — closing the upstream-controlled
    # collision gap on ``link`` / ``guid`` / ``desc_text_truncated`` /
    # ``title_cdata`` / ``desc_cdata`` that the pre-fix inline check
    # missed (only ``desc_html`` / ``raw_desc`` / ``title_out`` were
    # examined).
    max_attempts = 100
    attempts = 0
    while True:
        if attempts >= max_attempts:
            raise RuntimeError("Konnte keinen eindeutigen Platzhalter generieren")
        uid = secrets.token_hex(16)
        PH_CONTENT = f"___CDATA_CONTENT_{uid}___"
        PH_TITLE = f"___CDATA_TITLE_{uid}___"
        if not _placeholder_collides_with_formatted(PH_CONTENT, PH_TITLE, formatted):
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
    # Security (stored HTML/JS injection on the public feed — ``<description>``
    # sibling of the ``<content:encoded>`` output-encoding fix): ``<description>``
    # is an XML TEXT node, so ElementTree escapes ``<>&`` for XML *well-formedness*
    # on serialise. That alone is NOT enough — a conformant RSS reader XML-decodes
    # the node exactly ONCE and the overwhelming majority then render the result
    # as HTML (RSS 2.0 ``<description>`` is HTML by convention; ``content:encoded``
    # was added only to carry the *full* body). ``desc_text_truncated`` carries
    # plain display text in which an upstream ``&lt;img onerror=…&gt;`` has already
    # been decoded by ``html_to_text`` into a live ``<img onerror=…>``; without
    # output-encoding here that tag would execute in the subscriber's reader after
    # its single XML-decode. HTML-escape at this sink so the reader's lone
    # XML-decode yields inert ``&lt;img…&gt;`` *source* text. This is the single
    # per-item ``<description>`` sink for both the DE and EN feeds (``_emit_item``
    # is invoked once per language with the language-resolved ``formatted``).
    ET.SubElement(item, "description").text = html.escape(
        formatted.desc_text_truncated, quote=False
    )

    # content:encoded
    ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = PH_CONTENT

    replacements = {
        PH_CONTENT: f"<![CDATA[{formatted.desc_cdata}]]>",
        PH_TITLE: f"<![CDATA[{formatted.title_cdata}]]>",
    }

    return ident, item, replacements


# Channel-level metadata for the English feed mirror (docs/feed.en.xml).
# Kept as a module-level constant so the EN strings are reviewable in one
# place and never reach the upstream-controlled translation pipeline —
# channel metadata is build-time-owned, not item-derived.
_CHANNEL_METADATA_EN: dict[str, str] = {
    "title": "Vienna Public Transport — Disruptions & Commuter Info",
    "description": (
        "Active disruptions, construction works and restrictions from "
        "official sources (Wiener Linien, ÖBB, VOR/VAO, City of Vienna)."
    ),
    "language": "en",
}


def _channel_metadata(lang: str) -> dict[str, str]:
    """Resolve channel-level metadata for ``lang`` (``"de"`` or ``"en"``)."""
    if lang == "en":
        return _CHANNEL_METADATA_EN
    return {
        "title": feed_config.FEED_TITLE,
        "description": feed_config.FEED_DESC,
        "language": "de",
    }


def _make_rss(
    items: list[FeedItem],
    now: datetime,
    state: dict[str, dict[str, Any]],
    deletions: set[str] | None = None,
    *,
    lang: str = "de",
) -> str:
    """
    Generate the full RSS XML document from a list of items using ElementTree.

    Args:
        items: List of item dictionaries.
        now: Current timestamp.
        state: State dictionary for tracking items.
        deletions: IDs to be removed from the state.
        lang: Target language for the output (``"de"`` or ``"en"``).
            Drives channel metadata, ``<language>``, the atom self
            ``href`` (``feed.xml`` vs ``feed.en.xml``) and the per-item
            translation overlay forwarded to :func:`_emit_item`.

    Returns:
        The generated RSS XML string with CDATA sections.
    """
    if deletions is None:
        deletions = set()

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    metadata = _channel_metadata(lang)
    feed_filename = "feed.en.xml" if lang == "en" else "feed.xml"

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
    # no additional sanitisation. The EN strings come from the
    # module-level constant ``_CHANNEL_METADATA_EN`` and are still routed
    # through the sanitiser for defense-in-depth uniformity.
    ET.SubElement(channel, "title").text = _sanitize_text(metadata["title"])
    ET.SubElement(channel, "link").text = feed_config.FEED_LINK
    ET.SubElement(channel, "description").text = _sanitize_text(metadata["description"])

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
    atom_self.set("href", f"{pages_base}/{feed_filename}")
    ET.SubElement(channel, "language").text = metadata["language"]

    ET.SubElement(channel, "lastBuildDate").text = _fmt_rfc2822(now)
    ET.SubElement(channel, "ttl").text = str(feed_config.FEED_TTL)

    item_replacements: dict[str, str] = {}
    identities_in_feed: list[str] = []
    emitted = 0
    for it in items:
        if emitted >= feed_config.MAX_ITEMS:
            break
        ident, elem, repl = _emit_item(it, now, state, lang=lang)
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
                # Security: ``source`` / ``title`` are upstream-controlled and
                # reach this operator-facing stdout sink BEFORE the per-item
                # ``_format_item_content`` sanitisation runs (lint never formats
                # the items). Route both through ``_sanitize_text`` so the
                # canonical ``_CONTROL_RE`` floor (C0/C1 + BiDi + zero-width +
                # line/paragraph separators + Tag/VS blocks) is stripped here too
                # — the sibling duplicate-group print already sanitises its
                # titles via ``_summarize_duplicates``; this closes the
                # un-sanitised sibling path (Trojan-Source / terminal-escape /
                # log-forgery on the lint report).
                source = _sanitize_text(str(item.get("source") or "unbekannt"))
                title = _sanitize_text(str(item.get("title") or "<ohne Titel>"))
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
            log.debug("Sortiere %d Items nach Priorität (first_seen, neueste zuerst).", len(items))
        now_utc = _to_utc(now)
        items.sort(key=lambda it: _recency_sort_key(it, state, now_utc))

        new_items_count = _count_new_items(items, state)

        health_metrics = FeedHealthMetrics(
            raw_items=raw_count,
            filtered_items=filtered_count,
            deduped_items=deduped_count,
            new_items=new_items_count,
            duplicate_count=duplicates_removed,
            duplicates=tuple(duplicate_summaries),
        )

        # German feed (primary) is built first so the public ``feed.xml``
        # is always refreshed regardless of any translation-pipeline
        # issues encountered for the EN variant.
        rss_start = perf_counter()
        rss_de = _make_rss(items, now, state, deletions=dropped_ids, lang="de")
        rss_duration = perf_counter() - rss_start

        out_path = validate_path(Path(feed_config.OUT_PATH), "OUT_PATH")
        with atomic_write(
            out_path, mode="w", encoding="utf-8", permissions=0o644
        ) as f:
            f.write(rss_de)

        # English mirror — written next to ``feed.xml`` as ``feed.en.xml``.
        # Failures during translation degrade gracefully to the German
        # original via ``_translate_text`` / ``_apply_lang_overlay``;
        # write errors are isolated so the primary feed (already on disk)
        # stays committed even if the EN file cannot be produced.
        en_path = out_path.with_name("feed.en.xml")
        en_out_path = validate_path(en_path, "OUT_PATH")
        try:
            rss_en = _make_rss(
                items, now, state, deletions=dropped_ids, lang="en"
            )
            with atomic_write(
                en_out_path, mode="w", encoding="utf-8", permissions=0o644
            ) as f:
                f.write(rss_en)
        except Exception as exc:
            log.warning(
                "EN-Feed konnte nicht geschrieben werden (%s) – "
                "deutscher Feed ist bereits aktualisiert.",
                sanitize_log_arg(str(exc)),
            )

        try:
            _save_state(state, deletions=dropped_ids)
        except Exception as e:
            # Security (Clear-Text-Logging Drift): broad framework catch.
            log.warning(
                "State speichern fehlgeschlagen (%s) – Feed wurde geschrieben, State bleibt veraltet.",
                sanitize_log_arg(str(e)),
            )
            # Surface the failure on the structured report so monitoring
            # / dashboards see "degraded" instead of a clean
            # ``build_successful=True``. Pre-fix the swallowed exception
            # let the run report record a fully-successful build while
            # first_seen / translations / stats counters silently
            # drifted out of sync with the on-disk feed.
            report.add_warning(
                f"State save failed: {sanitize_log_arg(type(e).__name__)} — "
                "first_seen / translations / stats may drift on the next run."
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
