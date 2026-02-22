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
            destination = target_dir / info.filename
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
        if download_url:
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
        if keep_created:
            return
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
def reset_vor_request_count(tmp_path, monkeypatch):
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)
    yield
    if path.exists():
        path.unlink()
