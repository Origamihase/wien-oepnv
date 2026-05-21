#!/usr/bin/env python3
"""Extract a compact stops-only view of the ÖBB GeoNetz dataset.

Upstream: the GeoNetz dataset is published by ÖBB-Infrastruktur AG as
a 23 MiB FeatureCollection ZIP under
``https://data.oebb.at/dam/jcr:d4780bb2-390e-4288-b540-dff1ae1b27ae/GeoNetz_12-2024.zip``.
The decompressed JSON carries 6 645 features split across three classes:

* **STP_* (Stop-Points)** — 1 056 entries — actual rail stations
  (``STP_TYPE=railStation``) with their UIC EVA number, IFOPT id,
  Betriebsstellen-ID, address and authoritative coordinates.
* **RP_* (Routing-Points)** — 2 885 entries — every DB640 node
  (signals, junctions, border points). Not used by this script.
* **RL_* (Routing-Links)** — 2 704 entries — line-segment graph
  edges. Not used by this script either.

For our purposes (station-directory enrichment) only the STP-features
are interesting. Stripping the file down to the seven fields we
actually consume — ``bsts_id``, ``name``, ``lat``, ``lon``,
``eva_nr``, ``ifopt_id``, ``address`` — and JSON-encoding them in a
single ``stops`` array shrinks the on-disk payload from 23 MiB to
roughly 200 KiB, which is small enough to commit verbatim into
``data/oebb_geonetz_stops.json`` and serve every cron tick without
network access.

Usage::

    python scripts/extract_oebb_geonetz_stops.py \
        --raw path/to/raw_geonetz.json \
        --output data/oebb_geonetz_stops.json

Re-run this script whenever ÖBB publishes a new GeoNetz dataset
(typically annually at the SNNB-Fahrplanwechsel in mid-December).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # pragma: no cover - convenience for module execution
    from src.feed.logging_safe import setup_script_logging
    from src.utils.files import read_capped_bytes
except ModuleNotFoundError:  # pragma: no cover - fallback when installed as package
    from feed.logging_safe import setup_script_logging  # type: ignore[no-redef]
    from utils.files import read_capped_bytes  # type: ignore[no-redef]

# Cap mirrors ``MAX_JSON_FILE_BYTES`` in scripts/update_station_directory.py.
# The raw GeoNetz dump is ~23 MiB; 50 MiB leaves ~2x headroom for a future
# fahrplanperiode that adds RP/RL features without forcing a cap-bump.
MAX_GEONETZ_RAW_BYTES = 50 * 1024 * 1024

logger = logging.getLogger("extract_oebb_geonetz_stops")

_DEFAULT_SOURCE_URL = (
    "https://data.oebb.at/dam/jcr:d4780bb2-390e-4288-b540-dff1ae1b27ae/"
    "GeoNetz_12-2024.zip"
)
_DEFAULT_LICENSE = (
    "Datenquelle: ÖBB-Infrastruktur AG (data.oebb.at, "
    "mobilitaetsdaten.gv.at) — CC BY 4.0"
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--raw",
        required=True,
        type=Path,
        help="Path to the decompressed raw GeoNetz JSON (FeatureCollection).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "oebb_geonetz_stops.json",
        help="Where to write the compact stops-only payload.",
    )
    parser.add_argument(
        "--source-url",
        default=_DEFAULT_SOURCE_URL,
        help="URL recorded in the output payload for provenance tracking.",
    )
    return parser.parse_args(argv)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None  # JSON true/false should never end up in a coord field
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str_id(value: Any) -> str | None:
    """Normalise numeric IDs into a string form without losing precision.

    GeoNetz emits BSTS_ID / EVA_NR / PLC as JSON integers (e.g.
    ``8100090``). Persist them as strings so the schema's
    ``pattern: ^[0-9]{7,8}$`` succeeds without integer-overflow risk
    on downstream consumers that round-trip through JavaScript's
    53-bit-precision number type.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _extract_stop_record(feature: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one GeoNetz Stop-Point feature to the compact shape."""
    props = feature.get("properties") or {}
    if "STP_ID" not in props:
        return None  # not a Stop-Point feature

    bsts_id = _coerce_str_id(props.get("BSTS_ID"))
    if not bsts_id:
        return None  # the bsts_id is the join key — skip rows without it

    lat = _coerce_float(props.get("STP_LAT"))
    lon = _coerce_float(props.get("STP_LON"))
    name = props.get("STP_NAME")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()

    record: dict[str, Any] = {
        "bsts_id": bsts_id,
        "name": name,
    }
    if lat is not None and lon is not None:
        record["lat"] = round(lat, 6)
        record["lon"] = round(lon, 6)
    eva = _coerce_str_id(props.get("EVA_NR"))
    if eva:
        record["eva_nr"] = eva
    ifopt = props.get("IFOPT_ID")
    if isinstance(ifopt, str) and ifopt.strip():
        record["ifopt_id"] = ifopt.strip()
    address = props.get("STP_ROADNAME")
    if isinstance(address, str) and address.strip():
        record["address"] = address.strip()
    return record


def extract(raw_path: Path, source_url: str) -> dict[str, Any]:
    """Read the raw FeatureCollection and return the compact payload dict."""
    raw_bytes = read_capped_bytes(
        raw_path, MAX_GEONETZ_RAW_BYTES, label="GeoNetz raw", logger=logger
    )
    if raw_bytes is None:
        raise ValueError(
            f"Raw GeoNetz file {raw_path} unreadable or exceeded "
            f"{MAX_GEONETZ_RAW_BYTES} bytes."
        )
    raw = json.loads(raw_bytes)
    features = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(features, list):
        raise ValueError(
            f"Raw GeoNetz file {raw_path} has no 'features' list — "
            f"expected a FeatureCollection."
        )

    stops: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_duplicate = 0
    for feature in features:
        if not isinstance(feature, dict):
            continue
        record = _extract_stop_record(feature)
        if record is None:
            continue
        if record["bsts_id"] in seen:
            skipped_duplicate += 1
            continue
        seen.add(record["bsts_id"])
        stops.append(record)

    # Inspect the Fahrplanperiode by looking at the first STP feature
    # — GeoNetz attaches STP_FROMDATE/STP_TODATE to every Stop-Point.
    fahrplan_from: str | None = None
    fahrplan_to: str | None = None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        if "STP_ID" not in props:
            continue
        f_from = props.get("STP_FROMDATE")
        f_to = props.get("STP_TODATE")
        if isinstance(f_from, str):
            fahrplan_from = f_from[:10]
        if isinstance(f_to, str):
            fahrplan_to = f_to[:10]
        break

    stops.sort(key=lambda r: (r["name"].casefold(), r["bsts_id"]))

    payload: dict[str, Any] = {
        "$comment": (
            "Compact projection of the ÖBB-Infrastruktur GeoNetz dataset onto "
            "the seven fields the station-directory enrichment pipeline "
            "consumes. Re-generated by scripts/extract_oebb_geonetz_stops.py."
        ),
        "source_url": source_url,
        "license": _DEFAULT_LICENSE,
        "extracted_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "stops": stops,
    }
    if fahrplan_from and fahrplan_to:
        payload["fahrplanperiode"] = {"from": fahrplan_from, "to": fahrplan_to}
    if skipped_duplicate:
        payload["_skipped_duplicate_bsts_ids"] = skipped_duplicate
    return payload


def main(argv: list[str] | None = None) -> int:
    setup_script_logging(logging.INFO)
    args = _parse_args(argv)

    if not args.raw.exists():
        logger.error("Raw GeoNetz file not found: %s", args.raw)
        return 1

    payload = extract(args.raw, args.source_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    size_kib = args.output.stat().st_size / 1024
    logger.info(
        "Wrote %d stops (%.0f KiB) to %s — fahrplanperiode %s..%s",
        len(payload["stops"]),
        size_kib,
        args.output,
        payload.get("fahrplanperiode", {}).get("from", "?"),
        payload.get("fahrplanperiode", {}).get("to", "?"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
