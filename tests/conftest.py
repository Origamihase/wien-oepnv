import contextlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Iterator

from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

_STRECKENDATEN_DIR = root / "data" / "streckendaten"
_STRECKENDATEN_ARCHIVE = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.zip"
_STRECKENDATEN_GEOJSON_NAME = "streckendaten_notfallmanagement.geojson"

_SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "name": "streckendaten_notfallmanagement_sample",
    "crs": {
        "type": "name",
        "properties": {"name": "urn:ogc:def:crs:EPSG::4326"},
    },
    "features": [
        {
            "type": "Feature",
            "properties": {
                "strecke_id": "S1",
                "linie": "S1",
                "von": "Wien Floridsdorf",
                "bis": "Gänserndorf",
                "betriebsstelle_von": "Wien Floridsdorf",
                "betriebsstelle_bis": "Gänserndorf",
                "km_von": 0.0,
                "km_bis": 15.7,
                "betreiber": "ÖBB",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [16.4023, 48.2575],
                    [16.4178, 48.2891],
                    [16.5502, 48.3415],
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "strecke_id": "S45",
                "linie": "S45",
                "von": "Wien Heiligenstadt",
                "bis": "Wien Hütteldorf",
                "betriebsstelle_von": "Wien Heiligenstadt",
                "betriebsstelle_bis": "Wien Hütteldorf",
                "km_von": 0.0,
                "km_bis": 17.0,
                "betreiber": "ÖBB",
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [
                        [16.3599, 48.2488],
                        [16.3308, 48.2421],
                    ],
                    [
                        [16.3308, 48.2421],
                        [16.3047, 48.2332],
                        [16.2710, 48.1965],
                    ],
                ],
            },
        },
    ],
}

_SAMPLE_METADATA = {
    "title": "ÖBB Streckendaten Personenverkehr – Testsamples",
    "description": "Synthetic subset for automated tests; not official data.",
    "license": "CC BY 4.0",
    "source": "Generated during test setup",
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
    geojson_bytes = json.dumps(
        _SAMPLE_GEOJSON, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    metadata_bytes = json.dumps(
        _SAMPLE_METADATA, ensure_ascii=False, indent=2
    ).encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_STRECKENDATEN_GEOJSON_NAME, geojson_bytes)
        archive.writestr("metadata.json", metadata_bytes)


@pytest.fixture(scope="session")
def streckendaten_dataset() -> Iterator[Path]:
    if _STRECKENDATEN_ARCHIVE.exists():
        yield _STRECKENDATEN_ARCHIVE
        return

    created = False
    download_url = os.getenv("STRECKENDATEN_DOWNLOAD_URL")
    if download_url:
        created = _download_streckendaten(download_url, _STRECKENDATEN_ARCHIVE)

    if not created:
        _write_sample_streckendaten(_STRECKENDATEN_ARCHIVE)
        created = True

    try:
        yield _STRECKENDATEN_ARCHIVE
    finally:
        if not created:
            return
        keep_flag = os.getenv("KEEP_STRECKENDATEN_DATASET", "").strip().lower()
        if keep_flag in {"1", "true", "yes"}:
            return
        with contextlib.suppress(FileNotFoundError):
            _STRECKENDATEN_ARCHIVE.unlink()
        with contextlib.suppress(OSError):
            _STRECKENDATEN_DIR.rmdir()


@pytest.fixture(scope="session", autouse=True)
def _ensure_streckendaten(streckendaten_dataset: Path) -> None:
    yield


@pytest.fixture(autouse=True)
def reset_vor_request_count(tmp_path, monkeypatch):
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)
    yield
    if path.exists():
        path.unlink()
