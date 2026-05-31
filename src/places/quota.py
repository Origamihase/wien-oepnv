"""Quota management helpers for the Google Places API (New)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from pathlib import Path
from collections.abc import Callable, Mapping

from ..utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
    atomic_write,
)
from ..feed.config import validate_path

LOGGER = logging.getLogger("places.quota")

_KIND_KEYS = ("nearby", "text", "details")

# Security: defense-in-depth byte-size cap on the on-disk quota state
# file. The file is a small JSON object (5-10 fields, <1 KiB in
# practice). The depth-bomb defence catches a deeply-nested attack via
# ``RecursionError``, but a wide-but-flat attack such as an artificially-
# inflated ``counts`` map with millions of keys would slip past the
# depth check and exhaust memory in ``json.loads`` /
# ``path.read_text`` — both buffer the whole file before parsing, so
# the loader allocates O(file_size) before any per-key validation runs.
# 1 MiB is ~1000x the largest legitimate quota state observed in
# production and bounds the worst-case parse cost well below any cron
# runner's ulimit. Threat model mirrors ``MAX_CACHE_FILE_BYTES`` in
# ``src/utils/cache.py``: compromised CI runner / corrupted previous
# write / partial flush + power loss.
MAX_QUOTA_FILE_BYTES = 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in _KIND_KEYS}


@dataclass(frozen=True)
class QuotaConfig:
    """Configuration limits for the quota manager."""

    limit_total: int | None
    limit_nearby: int | None
    limit_text: int | None
    limit_details: int | None
    limit_daily: int | None

    def limit_for(self, kind: str) -> int | None:
        if kind == "nearby":
            return self.limit_nearby
        if kind == "text":
            return self.limit_text
        if kind == "details":
            return self.limit_details
        return None


@dataclass
class MonthlyQuota:
    """Persisted request counters scoped to the current Europe/Vienna month."""

    month_key: str
    counts: dict[str, int] = field(default_factory=_empty_counts)
    total: int = 0
    daily_key: str = ""
    daily_total: int = 0
    _now_func: Callable[[], datetime] = field(default=_utc_now, repr=False)

    @staticmethod
    def current_month_key(now: datetime | None = None) -> str:
        # Convert to Europe/Vienna before forming the key so the monthly
        # counter resets on the SAME calendar boundary as ``current_daily_key``
        # (and the operator's local mental model). Keying off raw UTC made the
        # two counters reset on different boundaries for the ~1–2 h each month
        # where Vienna has crossed into the new month but UTC has not.
        reference = now or _utc_now()
        local_ref = reference.astimezone(ZoneInfo("Europe/Vienna"))
        return f"{local_ref.year:04d}-{local_ref.month:02d}"

    @staticmethod
    def current_daily_key(now: datetime | None = None) -> str:
        reference = now or _utc_now()
        local_ref = reference.astimezone(ZoneInfo("Europe/Vienna"))
        return f"{local_ref.year:04d}-{local_ref.month:02d}-{local_ref.day:02d}"

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        now_func: Callable[[], datetime] | None = None,
    ) -> MonthlyQuota:
        now_callable = now_func or _utc_now
        if not path.exists():
            return cls(
                month_key=cls.current_month_key(now_callable()),
                counts=_empty_counts(),
                total=0,
                daily_key=cls.current_daily_key(now_callable()),
                daily_total=0,
                _now_func=now_callable,
            )

        # Security: ``RecursionError`` covers JSON depth-bomb attacks in
        # the on-disk quota state file (planted by a compromised runner /
        # corrupted previous write). Without this catch the depth-bomb
        # propagates out of ``MonthlyQuota.load`` — the public caller in
        # ``fetch_google_places_stations.py`` happens to wrap this in
        # ``except Exception``, but a future caller without that broad
        # catch would crash. Same canonical defence as the network-sourced
        # parsers in ``src/places/client.py``.
        # Security: byte-size cap (see MAX_QUOTA_FILE_BYTES) defeats the
        # wide-but-flat size-bomb attack that the depth-bomb catch above
        # does NOT cover (json.loads does not raise RecursionError on a
        # flat-but-huge document; MemoryError is a BaseException that
        # propagates past the json.JSONDecodeError handler).
        # Open first, then ``os.fstat`` the descriptor — closes the
        # TOCTOU between ``stat`` and ``open``/``read_text`` that lets
        # an attacker swap the inode between the two syscalls. The
        # ``read(MAX_QUOTA_FILE_BYTES + 1)`` cap defends against
        # special files (FIFOs, ``/dev/zero``) that report
        # ``st_size == 0`` but yield unbounded bytes on read.
        try:
            with path.open("rb") as handle:
                file_size = os.fstat(handle.fileno()).st_size
                if file_size > MAX_QUOTA_FILE_BYTES:
                    raise ValueError(
                        f"Quota state file too large (> {MAX_QUOTA_FILE_BYTES} bytes)"
                    )
                raw_bytes = handle.read(MAX_QUOTA_FILE_BYTES + 1)
                if len(raw_bytes) > MAX_QUOTA_FILE_BYTES:
                    raise ValueError(
                        f"Quota state file too large (> {MAX_QUOTA_FILE_BYTES} bytes)"
                    )
        except OSError as exc:
            raise ValueError("Quota state is not readable") from exc
        try:
            # Security (reader-side non-finite literal defence): mirrors
            # the writer-side ``allow_nan=False`` pin at
            # :meth:`MonthlyQuota.save_atomic` (Round 1488). The current
            # writer casts every numeric field to ``int(...)`` so today's
            # NaN risk is low, but a future float-typed quota field
            # (fractional cost accounting, latency averages, daily-quota
            # multiplier) reading back ``NaN`` / ``Infinity`` would
            # propagate ``float('nan')`` / ``float('inf')`` past the
            # subsequent ``isinstance(value, int)`` type-guards (which
            # ``False`` on float) and crash the cron pipeline via the
            # writer-side pin. Defense-in-depth at the parse boundary.
            raw = json.loads(
                raw_bytes,
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
        except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
            raise ValueError("Quota state is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("Quota state must be a JSON object")

        month = raw.get("month")
        if not isinstance(month, str):
            raise ValueError("Quota state requires string field 'month'")

        counts_raw = raw.get("counts", {})
        if not isinstance(counts_raw, dict):
            raise ValueError("Quota state field 'counts' must be an object")
        counts: dict[str, int] = _empty_counts()
        for key in _KIND_KEYS:
            value = counts_raw.get(key, 0)
            if isinstance(value, int) and value >= 0:
                counts[key] = value
            else:
                raise ValueError(f"Quota counter for '{key}' must be a non-negative integer")

        total_raw = raw.get("total", 0)
        if not isinstance(total_raw, int) or total_raw < 0:
            raise ValueError("Quota field 'total' must be a non-negative integer")

        daily_key = raw.get("daily_key", "")
        if not isinstance(daily_key, str):
            raise ValueError("Quota state field 'daily_key' must be a string")

        # Strict validation mirrors the sibling ``total`` / ``counts`` checks
        # above. The pre-fix silent coerce-to-0 let a corrupted state file
        # (compromised CI runner, partial flush + power loss, operator
        # mis-edit) bypass the daily cap (``PLACES_LIMIT_DAILY``) for the
        # whole day — while a sibling negative ``total`` still aborted the
        # load. Inconsistent strictness is a budget-escape vector.
        daily_total = raw.get("daily_total", 0)
        if not isinstance(daily_total, int) or daily_total < 0:
            raise ValueError(
                "Quota field 'daily_total' must be a non-negative integer"
            )

        return cls(
            month_key=month,
            counts=counts,
            total=total_raw,
            daily_key=daily_key,
            daily_total=daily_total,
            _now_func=now_callable,
        )

    def save_atomic(self, path: Path) -> None:
        payload = {
            "month": self.month_key,
            "counts": {key: int(self.counts.get(key, 0)) for key in _KIND_KEYS},
            "total": int(self.total),
            "daily_key": self.daily_key,
            "daily_total": int(self.daily_total),
        }
        # Explicitly set 0600 permissions.
        # Security (Trojan-Source / BiDi-Mark Drift Round 11): the file is
        # operator-facing diagnostic state, committed to ``main`` by the
        # weekly ``update-stations.yml`` cron job (the OSM-first / Google-
        # Places-fallback step inside ``update_all_stations.py`` charges
        # against this quota) and reviewed via ``cat`` / ``less`` / the
        # GitHub web UI / IDE preview. ``ensure_ascii=True``
        # escapes every non-ASCII code point as a literal ``\uXXXX``
        # sequence, so a future quota-state field carrying station- /
        # provider- / environment-controlled content cannot leak the
        # canonical CVE-2021-42574 Trojan-Source / zero-width / Unicode-
        # line-terminator / 8-bit C1 union as raw UTF-8 bytes. Mirrors the
        # canonical fix shape pinned in PR #1434 / PR #1435 for the
        # sibling ``data/*.json`` sidecar writers (``_write_quarantine_file``,
        # ``_save_state``, ``_write_heartbeat_file``). Forensic intent is
        # preserved (``MonthlyQuota.load`` recovers the original string from
        # the literal escape sequence via ``json.loads``).
        #
        # Security (Coordinate finite/range drift, committed-writer
        # defence-in-depth): ``allow_nan=False`` mirrors the canonical
        # writer-side pin established in Round 1485 at
        # :func:`src.places.merge.write_stations` and extended in
        # Round 1487 to :func:`src.utils.cache.write_cache` (the
        # sibling cache-events writer). The current payload casts
        # every numeric to ``int(...)`` so present-day NaN risk is
        # nil, but the pin surfaces a future ``float`` field (e.g.
        # fractional cost accounting, latency averages) as a loud
        # ``ValueError`` rather than silently landing non-standard
        # ``NaN`` / ``Infinity`` literals (invalid per RFC 8259)
        # in the committed ``data/places_quota.json`` sidecar.
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")

    def maybe_reset_month(self) -> bool:
        changed = False
        now_dt = self._now_func()

        current_month = self.current_month_key(now_dt)
        if self.month_key != current_month:
            self.month_key = current_month
            self.counts = _empty_counts()
            self.total = 0
            LOGGER.info("Quota reset for new month %s", current_month)
            changed = True

        current_day = self.current_daily_key(now_dt)
        if self.daily_key != current_day:
            self.daily_key = current_day
            self.daily_total = 0
            LOGGER.info("Quota reset for new day %s", current_day)
            changed = True

        return changed

    def can_consume(self, kind: str, cfg: QuotaConfig) -> bool:
        if cfg.limit_total is not None and self.total >= cfg.limit_total:
            return False
        if cfg.limit_daily is not None and self.daily_total >= cfg.limit_daily:
            return False
        limit = cfg.limit_for(kind)
        if limit is not None and self.counts.get(kind, 0) >= limit:
            return False
        return True

    def consume(self, kind: str, cfg: QuotaConfig) -> None:
        if not self.can_consume(kind, cfg):
            raise RuntimeError(f"Quota exceeded for {kind}")
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.total += 1
        self.daily_total += 1


def _parse_limit(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    limit = int(stripped)
    if limit < 0:
        raise ValueError("Quota limits must not be negative")
    return limit


def load_quota_config_from_env(env: Mapping[str, str] | None = None) -> QuotaConfig:
    environment = env or os.environ
    defaults = {
        "PLACES_LIMIT_TOTAL": 4000,
        "PLACES_LIMIT_NEARBY": 1500,
        "PLACES_LIMIT_TEXT": 1500,
        "PLACES_LIMIT_DETAILS": 1000,
        "PLACES_LIMIT_DAILY": 200,
    }

    limits: dict[str, int | None] = {}
    for key, default in defaults.items():
        try:
            value = _parse_limit(environment.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid integer for {key}: {environment.get(key)!r}") from exc
        limits[key] = default if value is None else value

    return QuotaConfig(
        limit_total=limits["PLACES_LIMIT_TOTAL"],
        limit_nearby=limits["PLACES_LIMIT_NEARBY"],
        limit_text=limits["PLACES_LIMIT_TEXT"],
        limit_details=limits["PLACES_LIMIT_DETAILS"],
        limit_daily=limits["PLACES_LIMIT_DAILY"],
    )


def resolve_quota_state_path(env: Mapping[str, str] | None = None) -> Path:
    environment = env or os.environ
    override = environment.get("PLACES_QUOTA_STATE")
    if override:
        # Security: validate configured paths to prevent path traversal outside allowed roots.
        return validate_path(Path(override), "PLACES_QUOTA_STATE")
    base = environment.get("STATE_PATH")
    if base:
        base_path = validate_path(Path(base), "STATE_PATH")
        return validate_path(base_path / "places_quota.json", "PLACES_QUOTA_STATE")
    return validate_path(Path("data/places_quota.json"), "PLACES_QUOTA_STATE")
