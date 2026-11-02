"""Der Datensatz Streckendaten Notfallmanagement für die Tests: einmal je Lauf
bereitstellen (echte Datei, Download oder Beispiel) und wieder aufräumen.

Früher in ``tests/conftest.py``.
"""


import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

root = Path(__file__).resolve().parents[2]


_STRECKENDATEN_DIR = root / "data" / "streckendaten"
_STRECKENDATEN_ARCHIVE = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.zip"
_STRECKENDATEN_GEOJSON = _STRECKENDATEN_DIR / "streckendaten_notfallmanagement.geojson"



def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


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


# The dataset sits at a fixed path the code reads, so pytest-xdist workers
# share it. Each worker runs this session fixture: unguarded, one worker read
# the sample archive while another was still writing it (``BadZipFile``) or
# deleted it at its teardown while the others were still testing; every test
# on those workers then failed in setup (4,190 errors in one
# ``-n 4 -p randomly`` run, 2026-10-04). A file lock serialises setup and
# teardown, and a count of the workers using the dataset leaves the cleanup
# to the last one. CI runs four workers (``-n auto``, since 2026-11-01); a
# serial run takes the same path with a count of one.
_STRECKENDATEN_LOCK = Path(tempfile.gettempdir()) / f"wien-oepnv-streckendaten-{_path_digest(_STRECKENDATEN_DIR)}"


@contextlib.contextmanager
def _streckendaten_lock() -> Iterator[dict[str, Any]]:
    """Hold the cross-process lock; yield the shared bookkeeping, saved on exit."""
    lock_path = _STRECKENDATEN_LOCK.with_suffix(".lock")
    state_path = _STRECKENDATEN_LOCK.with_suffix(".json")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            try:
                shared: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                shared = {"users": 0}
            yield shared
            if shared["users"] > 0:
                state_path.write_text(json.dumps(shared), encoding="utf-8")
            else:
                with contextlib.suppress(OSError):
                    state_path.unlink()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def streckendaten_dataset() -> Iterator[Path]:
    with _streckendaten_lock() as shared:
        if shared["users"] == 0:
            (
                _archive_path,
                archive_existed,
                geojson_existed,
                dir_existed,
                extracted_paths,
            ) = _ensure_streckendaten_dataset()
            shared["created"] = {
                "archive_existed": archive_existed,
                "geojson_existed": geojson_existed,
                "dir_existed": dir_existed,
                "extracted": [str(path) for path in extracted_paths],
            }
        shared["users"] += 1

    try:
        yield _STRECKENDATEN_ARCHIVE
    finally:
        with _streckendaten_lock() as shared:
            shared["users"] -= 1
            created = shared.get("created", {})
            keep_flag = os.getenv("KEEP_STRECKENDATEN_DATASET", "").strip().lower()
            if shared["users"] <= 0 and keep_flag not in {"1", "true", "yes"}:
                _remove_created_streckendaten(created)


def _remove_created_streckendaten(created: dict[str, Any]) -> None:
    archive_path = _STRECKENDATEN_ARCHIVE
    if not created.get("archive_existed", True) and archive_path.exists():
        archive_path.unlink()
    if not created.get("geojson_existed", True):
        _cleanup_created_paths([Path(path) for path in created.get("extracted", [])])
    if not created.get("dir_existed", True) and _STRECKENDATEN_DIR.exists():
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
