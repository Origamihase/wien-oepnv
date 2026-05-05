"""Validate data/stations.json against docs/schema/stations.schema.json.

Pins the contract between the canonical station entries and the published
JSON Schema so that every committed change to stations.json (manual or
via update-stations.yml) is checked structurally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "schema" / "stations.schema.json"
STATIONS_PATH = REPO_ROOT / "data" / "stations.json"


def test_stations_json_matches_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")

    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    with STATIONS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda exc: exc.path)
    assert not errors, "\n".join(
        f"{'.'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in errors
    )
