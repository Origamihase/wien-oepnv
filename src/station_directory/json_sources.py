"""Capped on-disk JSON loaders of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Mapping

from src.station_directory.model import _normalize_location_keys
from src.utils.env import _path_fingerprint
from src.utils.files import read_capped_json
from src.utils.stations_validation import is_synthetic_vor_id

logger = logging.getLogger("update_station_directory")


# Security cap against wide-but-flat JSON size-bomb attacks. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``: depth-bomb catch alone misses ``MemoryError``
# (a ``BaseException`` subclass) so a planted-huge file (~1 GiB of
# ``[0,0,…]``) buffered via ``path.read_text()`` propagates past the
# loader and crashes the cron pipeline (the orchestrator runs every
# update script via ``subprocess.run(check=True)``). 50 MiB is ~285x
# the production stations.json so legitimate state is never rejected.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024


def _load_existing_station_entries(
    path: Path,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    if not path.exists():
        return {}, []
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Without
    # the cap a wide-but-flat planted file would propagate
    # ``MemoryError`` past the loader and crash the cron pipeline.
    payload = read_capped_json(
        path,
        MAX_JSON_FILE_BYTES,
        label="Existing station directory",
    )
    if payload is None:
        logger.warning(
            "Could not parse existing station directory "
            "[path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return {}, []

    if isinstance(payload, dict):
        payload = payload.get("stations", [])

    mapping: dict[str, dict[str, object]] = {}
    manual_stations: list[dict[str, object]] = []

    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue

            bst_id = entry.get("bst_id")
            source = entry.get("source", "")

            is_manual = False
            if bst_id is None:
                is_manual = True
            elif source != "oebb":
                if isinstance(source, str):
                    stripped = source.strip()
                    if stripped:
                        # Token-based classification: a substring match for
                        # ``"oebb"`` inside a comma-separated source string
                        # would mis-classify entries whose ``source`` carries
                        # only a metadata provider whose name *contains* the
                        # canonical ``oebb`` token (e.g. ``oebb_geonetz`` —
                        # the GeoNetz EVA/IFOPT enrichment, not the ÖBB
                        # Excel ``Verzeichnis der Verkehrsstationen``).
                        # Pre-fix the substring check at this site bucketed
                        # the synthetic ``Wien Hauptbahnhof`` (``bst_id=
                        # 900100``, ``source=google_places,oebb_geonetz,
                        # vor,wl``) and ``Wien Kaiserebersdorf`` (``bst_id=
                        # 900105``, ``source=oebb_geonetz,vor``) into the
                        # ÖBB-Excel ``mapping`` even though the underlying
                        # ``bst_id`` is never present in the live ÖBB
                        # workbook. The entries therefore failed to round-
                        # trip through the rebuild and dropped out of
                        # ``data/stations.json`` on every weekly cron tick —
                        # the regression visible in commit ``484c1f6``'s
                        # post-mortem (the file fix there did not address
                        # the underlying classifier bug).
                        tokens = {t.strip() for t in stripped.split(",") if t.strip()}
                        if "oebb" in tokens:
                            is_manual = False
                        else:
                            # No bare ``oebb`` token. Route to the ÖBB-Excel
                            # ``mapping`` (so the re-pull merges into the entry)
                            # ONLY when it carries a real Betriebsstellen
                            # ``bst_code``. Two non-workbook shapes stay manual:
                            #   * synthetic VOR stops, whose ``bst_code`` is a
                            #     9xxxx placeholder the live workbook never
                            #     emits (e.g. ``Wien Hauptbahnhof`` 900100) —
                            #     the case the token check originally fixed; and
                            #   * WL-/VOR-only haltestellen, which carry no
                            #     ``bst_code`` at all (e.g. ``source="wl,vor"``).
                            # The pre-fix ``"oebb" not in tokens`` shortcut
                            # mis-filed a real workbook station that had merely
                            # lost its bare ``oebb`` token (``Siebenhirten``,
                            # bst_id=1371, source="oebb_geonetz,osm") as manual,
                            # duplicating it against the fresh extract (same
                            # bst_id in both lists, no dedup at the combine
                            # step) and tripping ``validate_stations``. Keying
                            # off a real (non-synthetic) ``bst_code`` keeps the
                            # synthetic / WL-only entries manual while letting a
                            # genuine Betriebsstelle round-trip through mapping.
                            bst_code = entry.get("bst_code")
                            has_real_oebb_code = (
                                isinstance(bst_code, str)
                                and bool(bst_code.strip())
                                and not is_synthetic_vor_id(bst_code.strip())
                            )
                            is_manual = not has_real_oebb_code
                    else:
                        # Backward-compat: entries written before the
                        # ``as_dict`` source-default fix lack a source
                        # field entirely (empty string). If they carry the
                        # typical ÖBB Excel fields (``bst_id`` +
                        # ``bst_code``), treat them as ÖBB — otherwise the
                        # next Excel pull would create a duplicate and
                        # trip the canonical-name uniqueness gate (see
                        # PR #1203 cron failure post-mortem).
                        bst_code = entry.get("bst_code")
                        is_manual = not (isinstance(bst_code, str) and bst_code.strip())
                elif isinstance(source, list) and "oebb" in source:
                    is_manual = False
                elif not source:
                    # Backward-compat (non-string variant): ``None`` / unset
                    # ``source`` falls through to the same ``bst_code`` guard.
                    bst_code = entry.get("bst_code")
                    is_manual = not (isinstance(bst_code, str) and bst_code.strip())
                else:
                    is_manual = True

            if is_manual:
                manual_stations.append(entry)
                continue

            if bst_id is not None:
                try:
                    lookup_id = str(int(float(bst_id)))
                except (ValueError, TypeError):
                    lookup_id = str(bst_id)
                if lookup_id:
                    mapping[lookup_id] = entry

    return mapping, manual_stations


def _load_vor_name_to_id_map(path: Path | None) -> dict[str, str]:
    """Read ``vor-haltestellen.mapping.json`` produced by fetch_vor_haltestellen.

    Returns a dict from the *exact* station name we asked for (i.e. the
    ÖBB Excel-name) to the resolved VOR id. Lets us short-circuit the
    fuzzy `_select_vor_stop` matcher: if the fetcher already determined
    `Hohenau` resolves to `430377800`, we reuse that mapping instead of
    re-deriving it from the candidate list (which can yield ambiguous
    or empty results when the resolved name carries a heavy disambiguation
    suffix like `Hohenau an der March Bahnhof`).
    """
    if path is None or not path.exists():
        return {}
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as the other loaders in this script.
    payload = read_capped_json(
        path,
        MAX_JSON_FILE_BYTES,
        label="VOR mapping",
    )
    if payload is None:
        logger.warning(
            "Could not read VOR mapping [path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return {}
    if not isinstance(payload, list):
        return {}
    mapping: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("station_name")
        vor_id = entry.get("vor_id")
        if isinstance(name, str) and isinstance(vor_id, str):
            text_name = name.strip()
            text_id = vor_id.strip()
            if text_name and text_id:
                mapping[text_name] = text_id
    if mapping:
        logger.info("Loaded %d direct name→vor_id mappings", len(mapping))
    return mapping


def _load_geonetz_stops(path: Path) -> dict[str, dict[str, object]]:
    """Read ``data/oebb_geonetz_stops.json`` into a ``bsts_id`` lookup.

    The file is the compact projection produced by
    ``scripts/extract_oebb_geonetz_stops.py`` from the ÖBB-Infrastruktur
    GeoNetz dataset (data.oebb.at GeoNetz_12-2024). Missing file or
    malformed payload degrades to an empty dict with a warning — the
    enrichment is a best-effort metadata-only tier whose absence must
    never crash the cron pipeline.

    Security: ``read_capped_json`` enforces both the depth-bomb catch
    tuple AND the byte-size cap (see :data:`MAX_JSON_FILE_BYTES`),
    plus ``parse_constant`` + ``parse_float`` non-finite literal
    rejection. Pre-fix the loader used ``json.loads(path.read_text(...))``
    which buffers the entire file into memory before parsing — a
    planted huge file at ``data/oebb_geonetz_stops.json`` (compromised
    CI runner / hostile PR / corrupted previous run / partial flush +
    power loss) would propagate ``MemoryError`` (a ``BaseException``
    subclass NOT caught by ``except Exception:``) past the loader and
    crash the weekly station refresh cron pipeline (the orchestrator
    runs every update script via ``subprocess.run(check=True)``).
    Pre-fix also accepted ``NaN`` / ``Infinity`` / scientific-notation
    overflow tokens at the JSON parse boundary — a poisoned coordinate
    field would propagate as ``float('nan')`` / ``float('inf')`` past
    the ``isinstance(value, str)`` guards on ``bsts_id`` into
    downstream enrichment that compares via ``==``/``!=``
    (``nan != nan`` is True — silent dedup invariant breakage).
    Mirrors the canonical loader pattern pinned at
    :func:`_load_existing_station_entries` and
    :func:`load_pendler_station_ids` in this module.
    """
    if not path.exists():
        logger.info("GeoNetz stops file not found: %s — skipping enrichment", path)
        return {}
    # Security: ``read_capped_json`` enforces the depth-bomb catch tuple,
    # the byte-size cap, AND ``parse_constant`` / ``parse_float`` non-
    # finite literal rejection. Returns ``None`` on missing / invalid /
    # oversized files — closing the unbounded ``path.read_text()``
    # MemoryError vector that pre-fix could crash the cron pipeline.
    raw = read_capped_json(
        path,
        MAX_JSON_FILE_BYTES,
        label="GeoNetz stops",
        logger=logger,
    )
    if raw is None:
        logger.warning(
            "Failed to load GeoNetz stops from [path-sha256=%s] "
            "(missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return {}
    stops = raw.get("stops") if isinstance(raw, Mapping) else None
    if not isinstance(stops, list):
        logger.warning("GeoNetz stops file %s has no 'stops' list", path)
        return {}
    lookup: dict[str, dict[str, object]] = {}
    for entry in stops:
        if not isinstance(entry, Mapping):
            continue
        bsts = entry.get("bsts_id")
        if not isinstance(bsts, str) or not bsts:
            continue
        # Coerce to ``dict[str, object]`` — the loader output uses
        # immutable Mapping interface but downstream want a mutable
        # row they can patch (e.g. for tests). Plain copy is enough.
        lookup[bsts] = dict(entry)
    logger.info("Loaded %d GeoNetz stops from %s", len(lookup), path)
    return lookup


def load_pendler_station_ids(path: Path) -> set[str]:
    if not path.exists():
        logger.warning(
            "Pendler station list not found: [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return set()
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). When the
    # file exists but is unreadable / invalid / oversized, surface a
    # ``ValueError`` so the canonical exit-1 contract for malformed
    # state is preserved.
    data = read_capped_json(
        path,
        MAX_JSON_FILE_BYTES,
        label="Pendler station list",
    )
    if data is None:
        raise ValueError(f"Invalid JSON in pendler station list: {path}")

    if not isinstance(data, list):
        raise ValueError(f"Pendler station list must be a JSON array: {path}")

    pendler_ids: set[str] = set()
    for entry in data:
        if isinstance(entry, bool):
            raise ValueError(f"Invalid pendler station identifier (boolean) in {path}: {entry!r}")
        if isinstance(entry, int):
            pendler_ids.add(str(entry))
            continue
        if isinstance(entry, str):
            token = entry.strip()
            if token.isdigit():
                pendler_ids.add(token)
                continue
        raise ValueError(f"Invalid pendler station identifier in {path}: {entry!r}")

    logger.info("Loaded %d pendler station IDs", len(pendler_ids))
    return pendler_ids


def load_pendler_name_candidates(path: Path) -> set[str]:
    """Load the name-based pendler whitelist (`data/pendler_candidates.json`).

    Returns a set of normalized name keys (via :func:`_normalize_location_keys`)
    so the caller can do an O(1) ``key in candidates`` check while iterating
    the ÖBB Excel rows. A missing or malformed file degrades to an empty set
    with a warning — the bst_id-based whitelist remains the primary path.

    Both ``name`` and the optional ``alternative_names`` array contribute to
    the key set; this lets the file declare the canonical research-name plus
    the spellings ÖBB itself uses (e.g. "Guntramsdorf Südbahn" canonical with
    "Guntramsdorf" as alternative).
    """
    if not path.exists():
        logger.info(
            "Pendler candidates file not found: [path-sha256=%s] "
            "(using bst_id whitelist only)",
            _path_fingerprint(path),
        )
        return set()
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as the other loaders in this script.
    data = read_capped_json(
        path,
        MAX_JSON_FILE_BYTES,
        label="Pendler candidates",
    )
    if data is None:
        logger.warning(
            "Invalid JSON in pendler candidates file "
            "[path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return set()

    if not isinstance(data, dict):
        logger.warning(
            "Pendler candidates file [path-sha256=%s] must be a JSON object",
            _path_fingerprint(path),
        )
        return set()

    raw = data.get("candidates", [])
    if not isinstance(raw, list):
        logger.warning(
            "Pendler candidates file [path-sha256=%s]: 'candidates' must be a list",
            _path_fingerprint(path),
        )
        return set()

    keys: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        names: list[str] = []
        if isinstance(name, str) and name.strip():
            names.append(name)
        alternative = entry.get("alternative_names")
        if isinstance(alternative, list):
            for alt in alternative:
                if isinstance(alt, str) and alt.strip():
                    names.append(alt)
        for variant in names:
            for key in _normalize_location_keys(variant):
                if key:
                    keys.add(key)
    logger.info(
        "Loaded %d pendler name-keys from %d candidates",
        len(keys),
        len(raw),
    )
    return keys
