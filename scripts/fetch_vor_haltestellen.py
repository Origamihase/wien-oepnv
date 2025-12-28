#!/usr/bin/env python3
"""Fetch VOR stop IDs for the station directory via the public VAO API."""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from src.utils.files import atomic_write
    from src.utils.http import session_with_retries
except ImportError:
    # Fallback if running as a package, though sys.path adjustment above should handle it
    from utils.files import atomic_write  # type: ignore
    from utils.http import session_with_retries  # type: ignore


DEFAULT_STATIONS_PATH = BASE_DIR / "data" / "stations.json"
DEFAULT_OUTPUT_PATH = BASE_DIR / "data" / "vor-haltestellen.csv"
DEFAULT_CONFIG_URL = "https://anachb.vor.at/webapp/js/hafas_webapp_config.js"
DEFAULT_MGATE_URL = "https://anachb.vor.at/hamm/gate"

log = logging.getLogger("fetch_vor_haltestellen")


@dataclass
class Station:
    name: str
    bst_id: int | None = None


@dataclass
class VORCandidate:
    ext_id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve station names in stations.json to VOR stop IDs via the VAO API",
    )
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS_PATH, help="stations.json source")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="CSV output path for resolved VOR stops"
    )
    parser.add_argument(
        "--sleep", type=float, default=0.2, help="Delay (in seconds) between API calls to avoid rate limits"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def load_stations(path: Path) -> list[Station]:
    data = json.loads(path.read_text(encoding="utf-8"))
    stations: list[Station] = []
    for entry in data:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        bst_id = entry.get("bst_id")
        if isinstance(bst_id, (int, float)):
            try:
                bst_id = int(bst_id)
            except ValueError:
                bst_id = None
        else:
            bst_id = None
        stations.append(Station(name=name, bst_id=bst_id))
    return stations


def fetch_access_id(session: requests.Session, config_url: str = DEFAULT_CONFIG_URL) -> str:
    resp = session.get(config_url, timeout=30)
    resp.raise_for_status()
    match = re.search(r'aid:"([A-Za-z0-9]+)"', resp.text)
    if match:
        aid = match.group(1)
        log.debug("Discovered access ID %s from webapp config", aid)
        return aid
    raise RuntimeError("Could not extract VOR access ID from config")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("ß", "ss")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.casefold())
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()
    return normalized


def _score_candidate(station_name: str, candidate_name: str, ext_id: str | None) -> float:
    station_norm = _normalize(station_name)
    candidate_norm = _normalize(candidate_name)
    if not candidate_norm:
        return -1.0
    ratio = difflib.SequenceMatcher(None, station_norm, candidate_norm).ratio()
    score = ratio * 100.0
    if candidate_norm == station_norm:
        score += 40.0
    if candidate_norm.startswith(station_norm + " "):
        score += 25.0
    if station_norm.startswith(candidate_norm + " "):
        score += 20.0
    if "bahnhof" in candidate_norm and "bahnhof" not in station_norm:
        score += 25.0
    if any(token in candidate_norm for token in ("hbf", "hauptbahnhof")):
        score += 20.0
    ext_id_str = str(ext_id or "")
    if ext_id_str.startswith("4"):
        score += 40.0
    if ext_id_str.startswith("9"):
        score -= 20.0
    return score


def _build_request_payload(access_id: str, name: str) -> dict[str, object]:
    return {
        "id": "station-lookup",
        "ver": "1.59",
        "lang": "deu",
        "auth": {"type": "AID", "aid": access_id},
        "client": {"id": "VAO", "type": "WEB", "name": "webapp", "l": "vs_webapp", "v": 10010},
        "formatted": False,
        "ext": "VAO.22",
        "svcReqL": [
            {
                "req": {
                    "input": {
                        "field": "S",
                        "loc": {"type": "S", "name": name},
                        "maxLoc": 8,
                    }
                },
                "meth": "LocMatch",
                "id": "1|1|",
            }
        ],
    }


def fetch_candidates(session: requests.Session, mgate_url: str, access_id: str, name: str) -> list[Mapping[str, object]]:
    payload = _build_request_payload(access_id, name)
    resp = session.post(mgate_url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    svc = data.get("svcResL") or []
    if not isinstance(svc, list) or not svc:
        return []
    first = svc[0]
    if not isinstance(first, Mapping):
        return []
    res = first.get("res")
    if not isinstance(res, Mapping):
        return []
    match = res.get("match")
    if not isinstance(match, Mapping):
        return []
    locs = match.get("locL")
    if not isinstance(locs, list):
        return []
    return [loc for loc in locs if isinstance(loc, Mapping)]


def select_candidate(name: str, candidates: Iterable[Mapping[str, object]]) -> VORCandidate | None:
    best: tuple[float, Mapping[str, object]] | None = None
    for entry in candidates:
        if entry.get("type") != "S":
            continue
        ext_raw = entry.get("extId")
        ext_id = str(ext_raw).strip() if ext_raw is not None else ""
        if not ext_id:
            continue
        score = _score_candidate(name, str(entry.get("name") or ""), ext_id)
        if best is None or score > best[0]:
            best = (score, entry)
    if not best:
        return None
    _, entry = best
    ext_id = str(entry.get("extId") or "").strip()
    candidate_name = str(entry.get("name") or "").strip()
    crd = entry.get("crd")
    lat = lon = None
    if isinstance(crd, Mapping):
        y = crd.get("y")
        x = crd.get("x")
        try:
            lat = float(y) / 1_000_000.0 if y is not None else None
            lon = float(x) / 1_000_000.0 if x is not None else None
        except (TypeError, ValueError):
            lat = lon = None
    return VORCandidate(ext_id=ext_id, name=candidate_name or name, latitude=lat, longitude=lon)


def resolve_station(
    session: requests.Session,
    mgate_url: str,
    access_id: str,
    station: Station,
    delay: float,
) -> VORCandidate | None:
    try:
        candidates = fetch_candidates(session, mgate_url, access_id, station.name)
    except requests.RequestException as exc:
        log.warning("Failed to fetch candidates for %s: %s", station.name, exc)
        return None
    if not candidates:
        log.warning("No candidates returned for %s", station.name)
        return None
    choice = select_candidate(station.name, candidates)
    if choice is None:
        log.warning("No suitable VOR candidate for %s", station.name)
        return None
    log.info("%s -> %s (%s)", station.name, choice.name, choice.ext_id)
    if delay:
        time.sleep(delay)
    return choice


def write_csv(path: Path, candidates: Mapping[str, VORCandidate]) -> None:
    fieldnames = ["StopPointId", "StopPointName", "Latitude", "Longitude"]
    with atomic_write(path, mode="w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for stop in sorted(candidates.values(), key=lambda item: item.ext_id):
            writer.writerow(
                {
                    "StopPointId": stop.ext_id,
                    "StopPointName": stop.name,
                    "Latitude": f"{stop.latitude:.6f}" if stop.latitude is not None else "",
                    "Longitude": f"{stop.longitude:.6f}" if stop.longitude is not None else "",
                }
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if not args.stations.exists():
        log.error("Stations file %s not found", args.stations)
        return 1

    stations = load_stations(args.stations)
    if not stations:
        log.error("No stations loaded from %s", args.stations)
        return 1

    resolved_unique: dict[str, VORCandidate] = {}
    resolved_pairs: list[tuple[Station, VORCandidate]] = []

    # Use secure session with retries and default timeout
    with session_with_retries(user_agent="wien-oepnv fetch_vor_haltestellen") as session:
        try:
            access_id = fetch_access_id(session)
        except Exception as exc:
            log.error("Could not determine VOR access ID: %s", exc)
            return 1

        for station in stations:
            candidate = resolve_station(session, DEFAULT_MGATE_URL, access_id, station, args.sleep)
            if not candidate:
                continue
            resolved_pairs.append((station, candidate))
            if candidate.ext_id not in resolved_unique:
                resolved_unique[candidate.ext_id] = candidate

    if not resolved_unique:
        log.error("No VOR stops resolved – aborting")
        return 1

    write_csv(args.output, resolved_unique)
    log.info("Wrote %d VOR stops to %s", len(resolved_unique), args.output)

    mapping_path = args.output.with_suffix(".mapping.json")
    mapping_payload = [
        {
            "station_name": pair[0].name,
            "bst_id": pair[0].bst_id,
            "vor_id": pair[1].ext_id,
            "resolved_name": pair[1].name,
            "latitude": pair[1].latitude,
            "longitude": pair[1].longitude,
        }
        for pair in resolved_pairs
    ]

    with atomic_write(mapping_path, mode="w", encoding="utf-8") as handle:
        handle.write(json.dumps(mapping_payload, ensure_ascii=False, indent=2) + "\n")

    log.info("Wrote station mapping to %s", mapping_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
