"""ÖBB workbook download (with cached fallback) and parsing of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import json
import logging
import math
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from collections.abc import Iterable

import openpyxl

from src.station_directory.model import Station
from src.utils.env import _path_fingerprint
from src.utils.files import (
    atomic_write,
    read_capped_bytes,
    validate_zip_archive_safe,
)
from src.utils.http import fetch_content_safe, session_with_retries

# Same repository root as ``scripts/update_station_directory.py``'s
# ``Path(__file__).resolve().parents[1]`` (this file sits one level deeper).
_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger("update_station_directory")


# Security cap against planted-huge binary cache payloads at the ÖBB
# workbook fallback path (:data:`DEFAULT_CACHED_WORKBOOK_PATH`). The
# fallback branch in :func:`download_workbook` is reached when the live
# ``data.oebb.at`` fetch fails; ``read_capped_bytes`` is the canonical
# defence shape (mirrors :data:`MAX_JSON_FILE_BYTES` /
# :data:`MAX_CSV_LOCATIONS_BYTES` in this module and
# :data:`MAX_LOG_PRUNE_FILE_BYTES` in :mod:`src.feed.logging`). Pinned
# at 10 MiB — identical to :data:`src.utils.http.MAX_PAYLOAD_SIZE`, the
# upper bound the HTTP-fetch path could have legitimately produced — so
# a cache file larger than what HTTP could have stored is by definition
# tampered (compromised CI runner / hostile PR / manual operator dump /
# partial flush + power loss) and is rejected at the read boundary.
# Production xlsx (~62 KiB) is ~169x under the cap, so legitimate state
# is never rejected. Pre-fix: ``cache_path.read_bytes()`` allocates
# O(file_size) bytes before the surrounding ``except`` (none) runs, so
# a 10 GiB planted file at the cache path raises ``MemoryError`` past
# the surrounding cron orchestrator (``subprocess.run(check=True)`` in
# :mod:`scripts.update_all_stations`), aborting the WHOLE weekly cron
# tick. Post-fix the file is treated as missing and the original
# upstream ``Exception`` re-raised — mirrors the pre-fix shape on a
# missing cache file.
MAX_CACHED_WORKBOOK_BYTES = 10 * 1024 * 1024


# Soft-fail snapshot for the ÖBB workbook — mirrors the pinned-CSV
# fallback pattern used by WL OGD (PR #1441-#1442) and the VOR
# haltestellen snapshot, so a transient ``data.oebb.at`` outage no
# longer zeroes-out a whole weekly cron tick. On every successful
# download the workbook bytes are atomically written here; on the
# next download failure ``download_workbook`` falls back to this
# cached copy (with a warning) before re-raising.
DEFAULT_CACHED_WORKBOOK_PATH = _ROOT / "data" / "oebb-verkehrsstationen.xlsx"
REQUEST_TIMEOUT = 30  # seconds
USER_AGENT = "wien-oepnv station updater " "(https://github.com/Origamihase/wien-oepnv)"

HEADER_VARIANTS: dict[str, set[str]] = {
    "name": {"verkehrsstation"},
    "bst_code": {"bstcode"},
    "bst_id": {"bstid"},
}


def download_workbook(
    url: str, cache_path: Path = DEFAULT_CACHED_WORKBOOK_PATH
) -> BytesIO:
    """Download the ÖBB workbook from *url* with a cached-snapshot fallback.

    Mirrors the soft-fail pattern used by the other upstream sources in
    this pipeline:

    * WL OGD (``scripts/update_wl_stations.py:_download_ogd_csv``) —
      atomic-write to the pinned local CSV on success, read the pinned
      CSV on failure.

    Prior to this commit ÖBB was the only fail-fast source — a
    transient ``data.oebb.at`` outage (CMS migration, CDN issue,
    weekend maintenance) zeroed out the weekly cron tick with no
    recovery path. The new behaviour:

    1. Try the download.
    2. On success, atomic-write the bytes to *cache_path* so the
       next cron tick has a snapshot to fall back to.
    3. On failure, read *cache_path* if it exists (with a warning
       log so the operator sees the upstream went silent).
    4. On failure with no cache, re-raise the original exception —
       there is no station directory to refresh.

    The cache file is committed to the repository by the weekly
    ``update-stations.yml`` auto-commit step (``add_options: "-A"``)
    so the first cron tick after this lands populates the snapshot
    automatically.
    """
    logger.info("Downloading workbook: %s", url)
    try:
        with session_with_retries(USER_AGENT) as session:
            content = fetch_content_safe(session, url, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        # Security: ``read_capped_bytes`` enforces the canonical
        # TOCTOU-safe size cap (mirrors :func:`read_capped_json` /
        # :func:`read_capped_text` in :mod:`src.utils.files`). The cap
        # is :data:`MAX_CACHED_WORKBOOK_BYTES` — identical to the
        # HTTP-fetch upper bound :data:`src.utils.http.MAX_PAYLOAD_SIZE`,
        # so a cache file larger than what HTTP could have legitimately
        # produced is rejected as tampered. Pre-fix
        # ``cache_path.read_bytes()`` allocated O(file_size) bytes
        # against a planted-huge cache file (compromised CI runner /
        # partial flush + power loss / parallel orchestrator atomic
        # state swap) and raised ``MemoryError`` past the surrounding
        # cron orchestrator. Post-fix the file is treated as missing
        # (``None`` return) and the fall-through error branch re-raises
        # the original upstream ``exc`` — mirroring the pre-fix shape
        # on a missing-cache miss.
        cached_bytes = read_capped_bytes(
            cache_path,
            MAX_CACHED_WORKBOOK_BYTES,
            label="ÖBB workbook cache",
            logger=logger,
        )
        if cached_bytes is not None:
            logger.warning(
                "Failed to download %s (%s); falling back to cached workbook [path-sha256=%s]",
                url,
                exc,
                _path_fingerprint(cache_path),
            )
            return BytesIO(cached_bytes)
        logger.error(
            "Failed to download %s and no cached workbook at %s — the "
            "station directory cannot be refreshed",
            url,
            cache_path,
        )
        raise

    # Atomic-cache the successful download for future fallback. A
    # cache write failure must not break the run that just succeeded;
    # log and continue.
    try:
        _persist_workbook_snapshot(cache_path, content)
    except OSError as exc:  # pragma: no cover - filesystem-dependent
        logger.warning(
            "Could not cache workbook to %s (%s); continuing with the "
            "downloaded bytes",
            cache_path,
            exc,
        )
    _stamp_workbook_checked_at(cache_path)

    return BytesIO(content)


def _stamp_workbook_checked_at(cache_path: Path) -> None:
    """Record the time of this good download next to the cached workbook.

    ``scripts/health_check.py`` reads ``checked_at`` from
    ``<workbook>.cache.json`` and turns red when it grows too old: a failed
    download falls back to the cached workbook and the run stays green, so
    without the stamp a dead source would go unnoticed.
    """
    sidecar = cache_path.with_name(cache_path.name + ".cache.json")
    payload = {"version": 1, "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        with atomic_write(sidecar, mode="w", encoding="utf-8", permissions=0o644) as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
    except OSError as exc:  # pragma: no cover - filesystem-dependent
        logger.warning("Could not stamp %s (%s)", sidecar.name, exc)


def _persist_workbook_snapshot(cache_path: Path, payload: bytes) -> None:
    """Atomically persist *payload* to *cache_path*.

    Security profile: *payload* is by contract the public ÖBB
    ``Verzeichnis der Verkehrsstationen`` XLSX — free open data, no
    auth, no PII, no secrets. The file is intentionally committed to
    the repository by the weekly ``update-stations.yml`` auto-commit
    step so the next cron tick has a fall-back snapshot if the live
    ``data.oebb.at`` download fails. This mirrors the pinned-CSV
    pattern already established for ``scripts/update_wl_stations.py
    :_download_ogd_csv``, ``data/vor-haltestellen.csv`` and the
    bundled GTFS stops dump. Threading the bytes through a dedicated
    helper (rather than writing the HTTP-response variable directly
    at the call site) keeps the cache writer's intent legible and
    isolates the file-IO from the network-IO data flow.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(cache_path, mode="wb", permissions=0o644) as handle:
        handle.write(payload)


def _normalize_header(value: object | None) -> str:
    if value is None:
        return ""
    normalized = str(value).strip().lower()
    for token in (" ", "-", "_"):
        normalized = normalized.replace(token, "")
    return normalized


def _match_required_headers(row: Iterable[object]) -> dict[str, int]:
    normalized = [_normalize_header(cell) for cell in row]
    column_map: dict[str, int] = {}
    for header_field, candidates in HEADER_VARIANTS.items():
        for index, value in enumerate(normalized):
            if any(value == candidate or value.startswith(candidate) for candidate in candidates):
                column_map[header_field] = index
                break
    return column_map


def _find_header_row(rows: Iterable[tuple[object, ...]]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows, start=1):
        if not row:
            continue
        column_map = _match_required_headers(row)
        if len(column_map) == len(HEADER_VARIANTS):
            return index, column_map
    raise ValueError("Could not identify the header row in the workbook")


def _coerce_bst_id(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        digits = value.strip()
        if not digits.isdigit():
            return None
        return digits
    if isinstance(value, int | float):
        # ``int(float("nan"))`` raises ValueError and ``int(float("inf"))``
        # raises OverflowError; openpyxl (data_only=True) can return non-finite
        # floats from malformed cached-formula cells. Drop the cell instead of
        # letting the exception escape the row loop and crash the whole run.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return str(int(value))
    return None


def extract_stations(workbook_stream: BytesIO) -> list[Station]:
    # Security: route through the canonical ``validate_zip_archive_safe``
    # helper so the four orthogonal axes (total size, per-entry size,
    # entry count, filename length) are all bounded BEFORE openpyxl
    # parses the workbook. The prior shape ``sum(info.file_size) > 100
    # MiB`` only closed the total-size axis: a malicious ZIP with
    # millions of empty entries (declared ``file_size=0``) trivially
    # passed the total-sum check while inflating ``infolist()`` to
    # millions of ZipInfo objects, OOMing the cron pipeline before any
    # consumer sees a row. The canonical helper closes that gap and
    # pins the auto-discoverable inventory test
    # ``test_no_unbounded_zipfile_zipfile_in_src_or_scripts``.
    try:
        with zipfile.ZipFile(workbook_stream) as archive:
            validate_zip_archive_safe(archive, label="ÖBB workbook")
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid workbook file") from exc

    workbook_stream.seek(0)
    workbook = openpyxl.load_workbook(workbook_stream, data_only=True, read_only=True)
    try:
        worksheet = workbook.active
        if worksheet is None:
            # openpyxl returns None when no sheet sits at the active index
            # (a workbook without sheets); same failure as a file that is no
            # workbook at all.
            raise ValueError("Invalid workbook file: no active worksheet")
        header_row_index, column_map = _find_header_row(worksheet.iter_rows(min_row=1, max_row=25, values_only=True))
        logger.debug("Detected header row at index %s", header_row_index)
        stations: list[Station] = []
        seen_ids: set[str] = set()
        for row in worksheet.iter_rows(min_row=header_row_index + 1, values_only=True):
            if not row:
                continue
            name_cell = row[column_map["name"]] if column_map["name"] < len(row) else None
            code_cell = row[column_map["bst_code"]] if column_map["bst_code"] < len(row) else None
            id_cell = row[column_map["bst_id"]] if column_map["bst_id"] < len(row) else None
            if name_cell is None or code_cell is None:
                continue
            parsed_id = _coerce_bst_id(id_cell)
            if parsed_id is None or parsed_id in seen_ids:
                continue
            station = Station(
                bst_id=parsed_id,
                bst_code=str(code_cell).strip(),
                name=str(name_cell).strip(),
            )
            seen_ids.add(parsed_id)
            stations.append(station)
        # ``bst_id`` is a numeric identifier stored as a string. Pure
        # string sort gives lexicographic order (``'100' < '11' < '9'``);
        # numeric sort matches operator expectation and minimises the
        # diff churn on ``data/stations.json`` when the upstream Excel
        # row order shifts.  Fall back to ``str`` for any non-numeric
        # ID so an unexpected value can't crash the sort.
        def _bst_id_key(item: Station) -> tuple[int, str]:
            try:
                return (int(item.bst_id), "")
            except (TypeError, ValueError):
                return (10**12, str(item.bst_id))

        stations.sort(key=_bst_id_key)
        logger.info("Extracted %d stations", len(stations))
        return stations
    finally:
        workbook.close()
