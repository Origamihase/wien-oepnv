"""Regression tests for station metadata enrichments."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Expected kilometre readings for the curated VZG sections included in the
# repository metadata snapshot. The values stem from ÖBB's official
# "Verzeichnis der Verkehrsstationen" reference and should only change when
# the upstream data set is refreshed.
EXPECTED_VZG_SECTION_KILOMETRES: dict[str, float] = {
    "11801": 4.05,
    "11802": 11.72,
    "11803": 26.48,
    "11804": 47.82,
}


def _load_metadata() -> dict[str, object]:
    """Return the parsed stations metadata file shipped with the repository."""

    metadata_path = Path(__file__).resolve().parents[1] / "data" / "stations_metadata.json"
    with metadata_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_vzg_sections_have_expected_kilometres() -> None:
    """The extracted VZG sections expose stable kilometre annotations."""

    metadata = _load_metadata()
    vzg_sections_raw = metadata.get("vzg_sections")
    assert isinstance(vzg_sections_raw, dict), "vzg_sections must be a mapping"

    # Guard against accidental additions or removals before we validate details.
    assert len(vzg_sections_raw) == len(EXPECTED_VZG_SECTION_KILOMETRES)

    for section_id, expected_kilometre in EXPECTED_VZG_SECTION_KILOMETRES.items():
        assert section_id in vzg_sections_raw, f"{section_id} missing from vzg_sections"
        entry = vzg_sections_raw[section_id]
        assert isinstance(entry, dict), f"{section_id} must map to an object"
        kilometre = entry.get("kilometre")
        assert kilometre is not None, f"{section_id} is missing the kilometre attribute"
        assert isinstance(kilometre, (int, float)), f"{section_id} kilometre must be numeric"
        assert kilometre == pytest.approx(expected_kilometre)
