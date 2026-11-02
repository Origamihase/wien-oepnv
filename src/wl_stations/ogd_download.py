"""OGD CSV download with ``ETag`` sidecar of ``scripts/update_wl_stations.py``.

Moved verbatim from the script. ``_download_ogd_csv`` still imports
``src.utils.http`` inside the function, so tests patch that module.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Mapping

from src.wl_stations.base import (
    _path_fingerprint,
    _project_root,
    atomic_write,
    log,
    read_capped_json,
)


OGD_DOWNLOAD_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "wien-oepnv station updater "
    "(https://github.com/Origamihase/wien-oepnv)"
)

# Bandwidth-saver: each WL OGD CSV download stores ``ETag`` and
# ``Last-Modified`` in a sibling ``<csv>.cache.json`` so the next run
# can send ``If-None-Match`` / ``If-Modified-Since`` and short-circuit
# on ``304 Not Modified``. The sidecar is tiny (<1 KiB) so the cap is
# generous; oversized files are silently treated as missing (which
# falls back to an unconditional GET, the safe direction).
MAX_OGD_CACHE_SIDECAR_BYTES = 64 * 1024
_OGD_CACHE_SIDECAR_SUFFIX = ".cache.json"


def _cache_sidecar_path(target: Path) -> Path:
    """Return the sidecar path that stores ``ETag`` / ``Last-Modified``.

    Convention: ``<target>.cache.json`` next to the CSV. The sidecar
    therefore moves/disappears with the file it describes — there is no
    risk of a stale sidecar describing a target that no longer exists.
    """
    return target.with_name(target.name + _OGD_CACHE_SIDECAR_SUFFIX)


def _read_cache_validators(target: Path) -> dict[str, str]:
    """Return ``If-None-Match`` / ``If-Modified-Since`` headers for *target*.

    Returns an empty dict when either the target CSV or its sidecar is
    missing, when the sidecar is corrupted/oversized, or when neither
    validator was captured by the previous fetch. The caller falls back
    to an unconditional GET in those cases — the safe direction.
    """
    sidecar = _cache_sidecar_path(target)
    if not target.exists() or not sidecar.exists():
        return {}
    payload = read_capped_json(
        sidecar,
        MAX_OGD_CACHE_SIDECAR_BYTES,
        label="WL OGD cache sidecar",
        logger=log,
    )
    if not isinstance(payload, dict):
        return {}
    validators: dict[str, str] = {}
    etag = payload.get("etag")
    if isinstance(etag, str) and etag:
        validators["If-None-Match"] = etag
    last_modified = payload.get("last_modified")
    if isinstance(last_modified, str) and last_modified:
        validators["If-Modified-Since"] = last_modified
    return validators


def _write_cache_validators(target: Path, headers: Mapping[str, str]) -> None:
    """Persist ``ETag`` / ``Last-Modified`` from *headers* into the sidecar.

    Best-effort: a write failure is logged but never raised so a
    successful CSV download is not cancelled by a sidecar I/O glitch.
    When the upstream returned neither validator we skip writing entirely
    — a sidecar without validators would force a full-body re-download
    on the next run anyway, so the empty file would be useless.
    """
    etag = headers.get("ETag") or headers.get("etag")
    last_modified = headers.get("Last-Modified") or headers.get("last-modified")
    if not etag and not last_modified:
        return
    now = datetime.now(UTC).isoformat(timespec="seconds")
    payload = {
        "version": 1,
        "etag": etag or "",
        "last_modified": last_modified or "",
        "fetched_at": now,
        "checked_at": now,
    }
    _write_sidecar(target, payload)


def _stamp_checked_at(target: Path) -> None:
    """Record in the sidecar that the source answered ``304 Not Modified``.

    ``checked_at`` is the last good answer of the source (200 or 304);
    ``scripts/health_check.py`` turns red when it grows too old, because a
    failed download keeps the previous file and would otherwise go
    unnoticed. ``fetched_at`` stays the time of the last full download.
    """
    sidecar = _cache_sidecar_path(target)
    payload = read_capped_json(
        sidecar,
        MAX_OGD_CACHE_SIDECAR_BYTES,
        label="WL OGD cache sidecar",
        logger=log,
    )
    if not isinstance(payload, dict):
        return
    payload["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _write_sidecar(target, payload)


def _write_sidecar(target: Path, payload: Mapping[str, object]) -> None:
    sidecar = _cache_sidecar_path(target)
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            sidecar, mode="w", encoding="utf-8", permissions=0o644
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
    except OSError as exc:
        log.warning(
            "Failed to write WL OGD cache sidecar [path-sha256=%s] (%s)",
            _path_fingerprint(sidecar),
            exc,
        )


def _download_ogd_csv(url: str, target: Path) -> bool:
    """Download a Wiener Linien OGD CSV and write it to *target* atomically.

    Uses HTTP conditional requests (``If-None-Match`` / ``If-Modified-Since``)
    when a previous fetch persisted validators in the sidecar — a ``304
    Not Modified`` response keeps the existing local file untouched and
    returns ``True`` (the file is up to date, which is a success state).
    A fresh ``200`` response writes the body atomically and refreshes the
    sidecar with the new validators.

    Returns ``True`` on success (200 written or 304 skipped),
    ``False`` on any error. Errors are logged but not raised, so callers
    can fall back to existing local files.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:  # pragma: no cover - defensive
        sys.path.insert(0, str(base_dir))
    from src.utils.http import request_safe, session_with_retries

    validators = _read_cache_validators(target)
    headers: dict[str, str] = {"User-Agent": USER_AGENT, **validators}
    if validators:
        log.info(
            "Downloading WL OGD (conditional, validators=%s): %s",
            ",".join(sorted(validators)),
            url,
        )
    else:
        log.info("Downloading WL OGD: %s", url)

    try:
        with session_with_retries(USER_AGENT) as session:
            response = request_safe(
                session,
                url,
                method="GET",
                timeout=OGD_DOWNLOAD_TIMEOUT_SECONDS,
                headers=headers,
                raise_for_status=True,
            )
    except Exception as exc:  # pragma: no cover - network-dependent
        log.warning("Failed to download %s (%s); using local file if present", url, exc)
        return False

    if response.status_code == 304:
        if not target.exists():
            # Defensive: the server claims our copy is current but we
            # don't have one. This would only happen if the sidecar and
            # the CSV were independently moved/deleted between runs. Drop
            # the stale sidecar so the next invocation does a full GET.
            log.warning(
                "WL OGD returned 304 but local target [path-sha256=%s] is "
                "missing; clearing stale sidecar",
                _path_fingerprint(target),
            )
            sidecar = _cache_sidecar_path(target)
            try:
                sidecar.unlink()
            except FileNotFoundError:
                log.debug(
                    "Stale WL OGD cache sidecar already absent [path-sha256=%s]",
                    _path_fingerprint(sidecar),
                )
            except OSError as exc:
                log.warning(
                    "Failed to clear stale WL OGD cache sidecar (%s)", exc
                )
            return False
        log.info(
            "WL OGD not modified (304); keeping existing [path-sha256=%s]",
            _path_fingerprint(target),
        )
        _stamp_checked_at(target)
        return True

    content = response.content
    if not content:
        log.warning("Empty response from %s; using local file if present", url)
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(target, mode="wb", permissions=0o644) as handle:
        handle.write(content)
    log.info(
        "Saved [path-sha256=%s] (%d bytes)",
        _path_fingerprint(target),
        len(content),
    )
    _write_cache_validators(target, response.headers)
    return True
