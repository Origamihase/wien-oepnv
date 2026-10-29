"""Which VAO API version the Stammstrecke monitor asks, chosen from the VAO's own list.

Operator decision 2026-10-29 (Michael, after the VAO withdrew v1.13.0 on
2026-10-28 between 14:30 and 15:00 and the Stammstrecke went without data
until a code change at 22:30): "Eine dynamische und automatische Wahl für
ein Fallback klingt für mich besser" and "Lies automatisch aus, was die
neuste Version ist. Wenn sie nicht funktioniert, nimm automatisch die
davor". A version change no longer needs a pull request.

The VAO lists its versions at ``…/vao/restproxy`` (JSON, no access key, not
counted against the daily budget), each with its decommission date. Every
poll reads that list and asks the newest version that is still in service.
When it does not answer usably, the same poll asks the next older one. A
version that failed is tried behind the others for :data:`FAILED_SKIP`, so a
fault that hits every version (a refused key, a VAO outage) costs one budget
slot per poll, not one per listed version. When the list cannot be read, the
poll asks the version that worked last, else ``DEFAULT_VERSION``.

The choice lives in :data:`VERSION_STATE_PATH`, committed with each Update
run. The CI probe (``scripts/verify_vor_access_id.py``) asks the version it
names; the Feed-Wächter opens an issue when it changes (rule
``vao-version``). ``DEFAULT_VERSION`` in :mod:`src.providers.vor` stays the
version for a checkout without that file.
"""

from __future__ import annotations

import json as _json_lib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from .files import atomic_write, read_capped_json

LOGGER = logging.getLogger(__name__)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
VERSION_STATE_PATH: Final = REPO_ROOT / "cache" / "stammstrecke" / "vao_version.json"

#: How long a version that failed is tried only after the others.
FAILED_SKIP: Final = timedelta(hours=24)
#: Polls per run at most: the chosen version and, when it fails, one fallback.
MAX_ATTEMPTS: Final = 2

_VERSION_RE: Final = re.compile(r"^v\d{1,3}\.\d{1,3}\.\d{1,3}$")
_DECOMMISSION_RE: Final = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_MAX_LISTED: Final = 20


def is_version(value: object) -> bool:
    return isinstance(value, str) and bool(_VERSION_RE.match(value))


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version[1:].split("."))


@dataclass(frozen=True)
class ListedVersion:
    """One entry of the VAO version list."""

    version: str
    decommission: date | None = None

    def in_service(self, today: date) -> bool:
        return self.decommission is None or today <= self.decommission


def _parse_decommission(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    match = _DECOMMISSION_RE.match(raw.strip())
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_version_list(payload: object) -> list[ListedVersion]:
    """The versions of the VAO list, newest first; entries without a version are dropped."""
    if not isinstance(payload, list):
        return []
    found: dict[str, ListedVersion] = {}
    for entry in payload[:_MAX_LISTED]:
        if not isinstance(entry, dict):
            continue
        version = entry.get("serviceVersion")
        if not isinstance(version, str) or not is_version(version) or version in found:
            continue
        found[version] = ListedVersion(version, _parse_decommission(entry.get("Decommission")))
    return sorted(found.values(), key=lambda item: version_key(item.version), reverse=True)


@dataclass
class VersionState:
    """What :data:`VERSION_STATE_PATH` holds."""

    version: str | None = None
    previous: str | None = None
    changed_at: datetime | None = None
    checked_at: datetime | None = None
    listed: list[str] = field(default_factory=list)
    failed: dict[str, datetime] = field(default_factory=dict)


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def load_state(path: Path | None = None) -> VersionState:
    """The stored choice; an empty state when the file is missing or unreadable."""
    payload = read_capped_json(
        path if path is not None else VERSION_STATE_PATH,
        label="VAO-Version",
        logger=LOGGER,
    )
    if not isinstance(payload, dict):
        return VersionState()
    failed_raw = payload.get("failed")
    failed: dict[str, datetime] = {}
    if isinstance(failed_raw, dict):
        for version, moment in failed_raw.items():
            parsed = _parse_time(moment)
            if is_version(version) and parsed is not None:
                failed[version] = parsed
    listed_raw = payload.get("listed")
    version = payload.get("version")
    previous = payload.get("previous")
    return VersionState(
        version=version if is_version(version) else None,
        previous=previous if is_version(previous) else None,
        changed_at=_parse_time(payload.get("changed_at")),
        checked_at=_parse_time(payload.get("checked_at")),
        listed=[v for v in listed_raw if is_version(v)] if isinstance(listed_raw, list) else [],
        failed=failed,
    )


def _state_to_json(state: VersionState) -> dict[str, Any]:
    def stamp(moment: datetime | None) -> str | None:
        return moment.isoformat(timespec="seconds") if moment is not None else None

    return {
        "version": state.version,
        "previous": state.previous,
        "changed_at": stamp(state.changed_at),
        "checked_at": stamp(state.checked_at),
        "listed": list(state.listed),
        "failed": {version: stamp(moment) for version, moment in sorted(state.failed.items())},
    }


def save_state(state: VersionState, path: Path | None = None) -> bool:
    """Persist *state* atomically; best-effort like the poll status next to it."""
    target = path if path is not None else VERSION_STATE_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(target, mode="w", encoding="utf-8", permissions=0o644) as fh:
            _json_lib.dump(_state_to_json(state), fh, indent=2, sort_keys=True,
                           ensure_ascii=True, allow_nan=False)
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning("VAO-Version konnte nicht gespeichert werden: %s", type(exc).__name__)
        return False


def candidates(
    listed: list[ListedVersion] | None,
    state: VersionState,
    *,
    default: str,
    now: datetime,
) -> list[str]:
    """The versions this poll may ask, in order, at most :data:`MAX_ATTEMPTS`.

    With a readable list: the versions in service, newest first, those that
    failed within :data:`FAILED_SKIP` behind the others. A second attempt is
    only made with a version that has not failed lately; when every version
    failed lately, one attempt is all. Without a list: the version that
    worked last, else *default*.
    """
    today = now.date()
    in_service = [item.version for item in listed or [] if item.in_service(today)]
    if not in_service:
        return [state.version or default]

    def failed_lately(version: str) -> bool:
        moment = state.failed.get(version)
        return moment is not None and now - moment < FAILED_SKIP

    fresh = [version for version in in_service if not failed_lately(version)]
    stale = [version for version in in_service if failed_lately(version)]
    first = (fresh or stale)[0]
    second = [version for version in fresh if version != first][: MAX_ATTEMPTS - 1]
    return [first, *second]


def record_outcome(
    state: VersionState,
    *,
    version: str,
    worked: bool,
    now: datetime,
    listed: list[ListedVersion] | None,
) -> VersionState:
    """Fold one attempt into the state. A working version becomes the one in use."""
    if listed is not None:
        state.listed = [item.version for item in listed]
        state.checked_at = now
    if not worked:
        state.failed[version] = now
        return state
    state.failed.pop(version, None)
    if state.version != version:
        state.previous = state.version
        state.version = version
        state.changed_at = now
    return state


__all__ = [
    "FAILED_SKIP",
    "MAX_ATTEMPTS",
    "VERSION_STATE_PATH",
    "ListedVersion",
    "VersionState",
    "candidates",
    "is_version",
    "load_state",
    "parse_version_list",
    "record_outcome",
    "save_state",
    "version_key",
]
