import contextlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from collections.abc import Iterator

from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

_STRECKENDATEN_DIR = root / "data" / "streckendaten"
_STRECKENDATEN_ARCHIVE = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.zip"
_STRECKENDATEN_GEOJSON = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.geojson"

_SAMPLE_STRECKENDATEN = {
    "type": "FeatureCollection",
    "name": "streckendaten_notfallmanagement",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 1,
                "LINIE": "S7",
                "linienname": "S7 Wien Mitte ↔ Flughafen",
                "LINIENNUMMER": "S7",
                "streckennummer": "20101",
                "streckenname": "Wien Mitte – Flughafen Wien",
                "BETRIEBSSTELLE_VON": "WIEN MITTE",
                "BETRIEBSSTELLE_BIS": "FLUGHAFEN WIEN",
                "Richtung": "Richtung Flughafen",
                "Category": "Personenverkehr",
                "KM_VON": 0.0,
                "KM_BIS": 18.3,
                "ANZAHL_GLEISE": 2,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [16.382, 48.206],
                    [16.413, 48.191],
                    [16.473, 48.157],
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 2,
                "LINIE": "S80",
                "linienname": "S80 Aspern Nord ↔ Hütteldorf",
                "LINIENNUMMER": "S80",
                "streckennummer": "20303",
                "streckenname": "Aspern Nord – Hütteldorf",
                "BETRIEBSSTELLE_VON": "ASPERN NORD",
                "BETRIEBSSTELLE_BIS": "WIEN HÜTTELDORF",
                "Richtung": "Richtung Aspern",
                "Category": "Personenverkehr",
                "KM_VON": 0.0,
                "KM_BIS": 26.4,
                "ANZAHL_GLEISE": 2,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [16.52, 48.234],
                    [16.444, 48.208],
                    [16.31, 48.196],
                ],
            },
        },
    ],
}


def _download_streckendaten(url: str, destination: Path) -> bool:
    tmp_path = destination.with_suffix(".tmp")
    try:
        with urllib_request.urlopen(url, timeout=30) as response:
            status = getattr(response, "status", None)
            if status is not None and status >= 400:
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except (OSError, urllib_error.URLError, urllib_error.HTTPError, ValueError):
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        return False
    else:
        tmp_path.replace(destination)
        return True


def _write_sample_streckendaten(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            _STRECKENDATEN_GEOJSON.name,
            json.dumps(_SAMPLE_STRECKENDATEN, ensure_ascii=False),
        )


def _extract_streckendaten(archive: Path, target_dir: Path) -> list[Path]:
    created_paths: list[Path] = []
    with zipfile.ZipFile(archive, "r") as zipped:
        for info in zipped.infolist():
            destination = (target_dir / info.filename).resolve()
            if not destination.is_relative_to(target_dir.resolve()):
                continue
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            zipped.extract(info, path=target_dir)
            created_paths.append(destination)
    return created_paths


def _ensure_streckendaten_dataset() -> tuple[Path, bool, bool, bool, list[Path]]:
    dataset_dir = _STRECKENDATEN_DIR
    dir_existed = dataset_dir.exists()
    dataset_dir.mkdir(parents=True, exist_ok=True)

    archive_path = _STRECKENDATEN_ARCHIVE
    archive_existed = archive_path.exists()
    geojson_existed = _STRECKENDATEN_GEOJSON.exists()

    if not archive_existed:
        download_url = os.getenv("STRECKENDATEN_DOWNLOAD_URL")
        created = False
        if download_url and download_url.startswith("https://"):
            created = _download_streckendaten(download_url, archive_path)
        if not created:
            _write_sample_streckendaten(archive_path)

    extracted_paths: list[Path] = []
    if not geojson_existed:
        extracted_paths = _extract_streckendaten(archive_path, dataset_dir)

    return (
        archive_path,
        archive_existed,
        geojson_existed,
        dir_existed,
        extracted_paths,
    )


def _cleanup_created_paths(paths: list[Path]) -> None:
    directories: set[Path] = set()
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        parent = path.parent
        while True:
            try:
                parent.relative_to(_STRECKENDATEN_DIR)
            except ValueError:
                break
            if parent == _STRECKENDATEN_DIR:
                break
            directories.add(parent)
            parent = parent.parent
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()


@pytest.fixture(scope="session")
def streckendaten_dataset() -> Iterator[Path]:
    (
        archive_path,
        archive_existed,
        geojson_existed,
        dir_existed,
        extracted_paths,
    ) = _ensure_streckendaten_dataset()

    keep_flag = os.getenv("KEEP_STRECKENDATEN_DATASET", "").strip().lower()
    keep_created = keep_flag in {"1", "true", "yes"}

    try:
        yield archive_path
    finally:
        if not keep_created:
            if not archive_existed and archive_path.exists():
                archive_path.unlink()
            if not geojson_existed:
                _cleanup_created_paths(extracted_paths)
            if not dir_existed and _STRECKENDATEN_DIR.exists():
                shutil.rmtree(_STRECKENDATEN_DIR, ignore_errors=True)
            elif _STRECKENDATEN_DIR.exists():
                try:
                    next(_STRECKENDATEN_DIR.iterdir())
                except StopIteration:
                    with contextlib.suppress(OSError):
                        _STRECKENDATEN_DIR.rmdir()


@pytest.fixture(scope="session", autouse=True)
def _ensure_streckendaten(streckendaten_dataset: Path) -> Iterator[None]:  # noqa: PT005
    yield


@pytest.fixture(autouse=True)
def reset_vor_request_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)

    # Also reset the memory cache
    monkeypatch.setitem(vor._QUOTA_CACHE, "date", None)
    monkeypatch.setitem(vor._QUOTA_CACHE, "count", 0)

    yield
    if path.exists():
        path.unlink()


@pytest.fixture(autouse=True)
def reset_build_feed_state() -> None:
    import src.build_feed as build_feed
    from src.feed.providers import reset_registry

    build_feed.reset_module_state()
    reset_registry(with_defaults=True)


@pytest.fixture(autouse=True)
def isolate_stats_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect ``src.utils.stats`` CSV appends to a per-test tmp directory.

    The production writers default to ``data/stats/`` under the repo
    root. Without this guard, any test that exercises a hot-path which
    transitively records stats (build_feed.update_item_state's
    strict-new branch, scripts/update_stammstrecke_status._process_direction)
    would write into the real on-disk ledger and contaminate the
    committed history with synthetic test rows.

    The override is monkeypatched on the module attribute, so call
    sites that did not pass an explicit ``stats_dir`` keyword (which
    is the common case in production) pick up the test path. Tests
    that *do* explicitly target a different ``stats_dir`` (e.g.
    ``test_utils_stats.py``) are unaffected because the explicit
    keyword wins inside ``stats_path``.
    """
    from src.utils import stats as stats_utils

    monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", tmp_path / "stats")
    yield


# ---------------------------------------------------------------------------
# CircuitBreaker test-isolation fixture
#
# Module-level :class:`src.utils.circuit_breaker.CircuitBreaker` instances
# remember failure streaks across calls *and* across tests in the same
# process. A test that intentionally trips a breaker (the OSM Overpass
# breaker is the canonical example — see
# ``tests/places/test_osm_client.py::test_fetch_stations_breaker_opens_after_repeated_failures``)
# leaves the global in OPEN state for the rest of the suite, which then
# fails any later test that exercises the same call site with a
# ``CircuitBreakerOpen`` masquerading as the upstream's real failure.
#
# The autouse fixture below resets every project-owned CircuitBreaker
# back to CLOSED + zero failures before each test runs. Adding a new
# breaker is a single ``_iter_known_breakers`` line — no test boilerplate
# required.
# ---------------------------------------------------------------------------


def _iter_known_breakers() -> Iterator[object]:
    """Yield every module-level CircuitBreaker the project owns.

    Imports are inside the function so a partial test environment (e.g.
    a places-free worktree) never aborts collection. Each entry is
    yielded as ``object`` because the actual type is an implementation
    detail of the producing module.
    """
    from src.places import osm_client as _osm_module

    yield _osm_module._BREAKER


@pytest.fixture(autouse=True)
def reset_circuit_breakers() -> Iterator[None]:
    """Force every known CircuitBreaker back to CLOSED before each test.

    Without this guard, a test that opens the breaker (intentionally or
    by side-effect of a chaos run) would silently fail every later test
    that touches the same call site, masking real upstream regressions
    and creating order-dependent test failures.
    """
    for breaker in _iter_known_breakers():
        reset = getattr(breaker, "reset", None)
        if callable(reset):
            reset()
    yield
    for breaker in _iter_known_breakers():
        reset = getattr(breaker, "reset", None)
        if callable(reset):
            reset()


# ---------------------------------------------------------------------------
# Coordinate-proximity test helper
#
# Replacement for the brittle ``pytest.approx(<lat>) ; pytest.approx(<lon>)``
# pattern in tests that pin station coordinates. ``pytest.approx`` uses a
# default relative tolerance of ~1e-6 which at lat=48° is roughly 5 cm
# east-west — far tighter than any upstream API's coordinate stability,
# so every refresh of ``data/stations.json`` would otherwise break tests
# even when the new coords are operationally equivalent (same building,
# different platform reference).
#
# ``assert_coords_close`` instead measures the great-circle distance
# between the obtained and expected coords and asserts it is within
# ``max_meters``. Same semantics as ``apply_coordinate_inertia`` so
# tests and production agree on what "the same point" means.
# ---------------------------------------------------------------------------

def assert_coords_close(
    lat1: float | None,
    lon1: float | None,
    lat2: float,
    lon2: float,
    max_meters: float = 150.0,
) -> None:
    """Assert two coordinate pairs are within ``max_meters`` of each other.

    The first pair (``lat1``/``lon1``) is the value under test and is
    typed ``float | None`` to match the project's ``StationInfo``
    shape — many station-lookup return types expose coordinates as
    optional. ``None`` is rejected with an explicit assertion failure
    so the caller doesn't have to thread ``assert info.latitude is
    not None`` boilerplate through every test.

    Args:
        lat1: First-pair latitude (typically the value under test).
            ``None`` is treated as a test failure.
        lon1: First-pair longitude. ``None`` → test failure.
        lat2: Second-pair latitude (the expected value).
        lon2: Second-pair longitude.
        max_meters: Maximum allowed great-circle distance, defaults to
            150 m to match :data:`STATION_DRIFT_TOLERANCE_METERS`.

    Raises:
        AssertionError: If either of ``lat1`` / ``lon1`` is ``None``,
            or the two points are further apart than ``max_meters``.
            The error message names both pairs and the measured
            distance for easy diagnosis.
    """
    from src.utils.geo import calculate_distance_meters

    assert lat1 is not None and lon1 is not None, (
        f"obtained coordinates were None (lat={lat1!r}, lon={lon1!r}); "
        f"expected ({lat2}, {lon2})"
    )
    distance = calculate_distance_meters(lat1, lon1, lat2, lon2)
    assert distance <= max_meters, (
        f"coordinates ({lat1}, {lon1}) and ({lat2}, {lon2}) are "
        f"{distance:.1f} m apart (max allowed: {max_meters} m)"
    )
