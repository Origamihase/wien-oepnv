"""Quota management helpers for the Google Places API (New)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Mapping

try:  # pragma: no cover
    from utils.files import atomic_write
except ModuleNotFoundError:  # pragma: no cover
    from ..utils.files import atomic_write

LOGGER = logging.getLogger("places.quota")

_KIND_KEYS = ("nearby", "text", "details")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _empty_counts() -> Dict[str, int]:
    return {key: 0 for key in _KIND_KEYS}


@dataclass(frozen=True)
class QuotaConfig:
    """Configuration limits for the quota manager."""

    limit_total: int | None
    limit_nearby: int | None
    limit_text: int | None
    limit_details: int | None

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
    """Persisted request counters scoped to the current UTC month."""

    month_key: str
    counts: Dict[str, int] = field(default_factory=_empty_counts)
    total: int = 0
    _now_func: Callable[[], datetime] = field(default=_utc_now, repr=False)

    @staticmethod
    def current_month_key(now: datetime | None = None) -> str:
        reference = now or _utc_now()
        return f"{reference.year:04d}-{reference.month:02d}"

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        now_func: Callable[[], datetime] | None = None,
    ) -> "MonthlyQuota":
        now_callable = now_func or _utc_now
        if not path.exists():
            return cls(
                month_key=cls.current_month_key(now_callable()),
                counts=_empty_counts(),
                total=0,
                _now_func=now_callable,
            )

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Quota state must be a JSON object")

        month = raw.get("month")
        if not isinstance(month, str):
            raise ValueError("Quota state requires string field 'month'")

        counts_raw = raw.get("counts", {})
        if not isinstance(counts_raw, dict):
            raise ValueError("Quota state field 'counts' must be an object")
        counts: Dict[str, int] = _empty_counts()
        for key in _KIND_KEYS:
            value = counts_raw.get(key, 0)
            if isinstance(value, int) and value >= 0:
                counts[key] = value
            else:
                raise ValueError(f"Quota counter for '{key}' must be a non-negative integer")

        total_raw = raw.get("total", 0)
        if not isinstance(total_raw, int) or total_raw < 0:
            raise ValueError("Quota field 'total' must be a non-negative integer")

        return cls(month_key=month, counts=counts, total=total_raw, _now_func=now_callable)

    def save_atomic(self, path: Path) -> None:
        payload = {
            "month": self.month_key,
            "counts": {key: int(self.counts.get(key, 0)) for key in _KIND_KEYS},
            "total": int(self.total),
        }
        # Explicitly set 0600 permissions
        with atomic_write(path, mode="w", encoding="utf-8", permissions=0o600) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")

    def maybe_reset_month(self) -> bool:
        current = self.current_month_key(self._now_func())
        if self.month_key == current:
            return False
        self.month_key = current
        self.counts = _empty_counts()
        self.total = 0
        LOGGER.info("Quota reset for new month %s", current)
        return True

    def can_consume(self, kind: str, cfg: QuotaConfig) -> bool:
        if cfg.limit_total is not None and self.total >= cfg.limit_total:
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
    }

    limits: Dict[str, int | None] = {}
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
    )


def resolve_quota_state_path(env: Mapping[str, str] | None = None) -> Path:
    environment = env or os.environ
    override = environment.get("PLACES_QUOTA_STATE")
    if override:
        return Path(override)
    base = environment.get("STATE_PATH")
    if base:
        return Path(base) / "places_quota.json"
    return Path("data/places_quota.json")

