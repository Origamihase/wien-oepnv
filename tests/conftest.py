import json
import sys
import zipfile
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def _ensure_streckendaten_dataset() -> tuple[Path, Path, bool, bool]:
    """Create a minimal Streckendaten archive for tests if needed."""

    dataset_dir = root / "data" / "streckendaten"
    dataset_dir.mkdir(parents=True, exist_ok=True)

    archive_path = dataset_dir / "streckendaten_notfallmanagement.zip"
    geojson_path = dataset_dir / "streckendaten_notfallmanagement.geojson"

    archive_existed = archive_path.exists()
    geojson_existed = geojson_path.exists()

    if not archive_path.exists():
        features = [
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
                        [16.520, 48.234],
                        [16.444, 48.208],
                        [16.310, 48.196],
                    ],
                },
            },
        ]

        sample = {
            "type": "FeatureCollection",
            "name": "streckendaten_notfallmanagement",
            "features": features,
        }

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "streckendaten_notfallmanagement.geojson",
                json.dumps(sample, ensure_ascii=False),
            )

    if not geojson_path.exists():
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dataset_dir)

    return archive_path, geojson_path, archive_existed, geojson_existed


@pytest.fixture(autouse=True, scope="session")
def streckendaten_dataset():
    archive_path, geojson_path, archive_existed, geojson_existed = _ensure_streckendaten_dataset()
    try:
        yield
    finally:
        if not archive_existed and archive_path.exists():
            archive_path.unlink()
        if not geojson_existed and geojson_path.exists():
            geojson_path.unlink()
        dataset_dir = archive_path.parent
        if dataset_dir.exists():
            iterator = dataset_dir.iterdir()
            try:
                next(iterator)
            except StopIteration:
                dataset_dir.rmdir()


@pytest.fixture(autouse=True)
def reset_vor_request_count(tmp_path, monkeypatch):
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)
    yield
    if path.exists():
        path.unlink()
