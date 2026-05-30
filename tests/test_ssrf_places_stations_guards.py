"""Regression tests for three input-robustness findings in the SSRF / Places
/ stations-validation layer.

1. ``src/utils/http.py`` — SSRF trailing-dot bypass of ``_UNSAFE_DOMAINS``.
   The ``_UNSAFE_DOMAINS`` loop matched against ``lower_host`` (trailing dot
   intact) instead of ``check_host`` (dot-stripped, used by the TLD check
   right above). A hostile / misconfigured URL like ``http://127.0.0.1.nip.io.``
   slipped through with ``check_dns=False`` because
   ``"...nip.io.".endswith(".nip.io")`` is ``False``, and was returned to a
   caller that explicitly bypassed live DNS — reachable via every embedding
   path that publishes URLs into a committed artefact.

2. ``src/places/merge.py`` — out-of-range stored coordinate crashes
   ``merge_places``. ``load_stations`` rejects non-finite literals at parse
   time, but a finite-yet-out-of-range value (``latitude: 999.0`` from a
   hand edit / legacy backup / planted file) slipped through and the
   ``haversine_m`` call inside ``_find_matching_station`` enforced its
   [-90,90] / [-180,180] bounds by raising ``ValueError`` — propagating out
   of ``merge_places`` and crashing the Google Places station-directory
   update. Fix: range-skip mirroring the existing non-numeric skip.

3. ``src/utils/stations_validation.py`` — ``_find_provider_issues`` crashed
   on a non-hashable ``bst_code`` (list/dict). The function tried to
   ``oebb_codes.add(bst_code)`` (line ~1027) and ``bst_code in oebb_codes``
   (line ~1051) without the ``isinstance`` guard every other ``_find_*``
   finder uses, so a malformed entry raised ``TypeError: unhashable type``
   and aborted the validator — even though the per-entry threat model of
   ``stations.json`` (planted-input boundary) explicitly requires producing
   a ``ProviderIssue`` (or skipping), never crashing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.places.client import Place
from src.places.merge import MergeConfig, load_stations, merge_places
from src.utils.http import validate_http_url
from src.utils.stations_validation import validate_stations


# --------------------------------------------------------------------------
# 1. http.py — SSRF trailing-dot bypass
# --------------------------------------------------------------------------


def test_unsafe_domains_block_trailing_dot() -> None:
    """``host.`` with the trailing dot must still be rejected when host is unsafe."""
    for url in (
        "http://127.0.0.1.nip.io.",
        "http://169.254.169.254.sslip.io.",
        "http://localhost.lvh.me.",
        "http://anything.localtest.me.",
        "http://x.xip.io.",
    ):
        assert validate_http_url(url, check_dns=False) is None, (
            f"SSRF trailing-dot bypass: {url} was not rejected"
        )


def test_unsafe_domains_block_dotless_still_rejected() -> None:
    """The fix must not regress the original (no-trailing-dot) rejection."""
    for url in (
        "http://127.0.0.1.nip.io",
        "http://169.254.169.254.sslip.io",
    ):
        assert validate_http_url(url, check_dns=False) is None, url


def test_safe_domain_with_trailing_dot_still_passes() -> None:
    """Fix must not over-block: a legit FQDN with a trailing dot still passes."""
    # The TLD check on the line above already used ``check_host``
    # (dot-stripped), so a legitimate trailing dot was always tolerated; the
    # new ``_UNSAFE_DOMAINS`` loop must keep that tolerance for safe hosts.
    assert validate_http_url("http://example.com.", check_dns=False) is not None
    assert validate_http_url("http://example.com", check_dns=False) is not None


# --------------------------------------------------------------------------
# 2. places/merge.py — out-of-range coordinate
# --------------------------------------------------------------------------


def _write_stations(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"stations": entries}))


def test_merge_places_skips_out_of_range_station_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """A finite-but-out-of-WGS84-range coord must be SKIPPED, not crash."""
    stations_path = tmp_path / "stations.json"
    _write_stations(
        stations_path,
        [
            # Corrupted (out-of-range) entry. Pre-fix: this crashed
            # merge_places via haversine_m -> ValueError.
            {
                "name": "Broken",
                "latitude": 999.0,
                "longitude": 16.37,
                "source": "osm",
                "aliases": [],
            },
            {
                "name": "Good",
                "latitude": 48.21,
                "longitude": 16.37,
                "source": "osm",
                "aliases": [],
            },
        ],
    )
    loaded = load_stations(stations_path)
    place = Place("ChIJabc", "Other", 48.21, 16.37, ["subway_station"], "Wien")
    out = merge_places(
        loaded, [place], MergeConfig(max_distance_m=500.0, bounding_box=None)
    )
    # Merge ran to completion; the corrupt entry contributed no match.
    assert len(out.stations) >= 1


def test_merge_places_does_not_skip_in_range_stations(tmp_path: Path) -> None:
    """The skip path must not over-fire: every legit coord still participates."""
    stations_path = tmp_path / "stations.json"
    _write_stations(
        stations_path,
        [
            {
                "name": "Praterstern",
                "latitude": 48.218,
                "longitude": 16.392,
                "source": "osm",
                "aliases": [],
            },
        ],
    )
    loaded = load_stations(stations_path)
    place = Place(
        "ChIJabc", "Praterstern", 48.218, 16.392, ["subway_station"], "Wien"
    )
    out = merge_places(
        loaded, [place], MergeConfig(max_distance_m=500.0, bounding_box=None)
    )
    # Place matched the existing station (distance ≈ 0), so no new entry.
    assert out.new_entries == []


# --------------------------------------------------------------------------
# 3. stations_validation — non-hashable bst_code
# --------------------------------------------------------------------------


def _validation_path(entries: list[dict[str, object]]) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "stations.json"
    tmp.write_text(json.dumps(entries))
    return tmp


def test_validate_stations_does_not_crash_on_list_bst_code() -> None:
    """A list ``bst_code`` must yield a report, not ``TypeError: unhashable``."""
    path = _validation_path(
        [
            {"name": "V1", "source": "vor", "bst_id": "900100", "bst_code": "900101"},
            {"name": "V2", "source": "vor", "bst_id": "900102", "bst_code": "900103"},
            {"name": "O1", "source": "oebb", "bst_code": ["x"]},  # the trap
        ]
    )
    report = validate_stations(path)
    assert report.total_stations == 3


def test_validate_stations_does_not_crash_on_dict_bst_code() -> None:
    """A dict ``bst_code`` is the same threat shape and must be handled too."""
    path = _validation_path(
        [
            {"name": "V1", "source": "vor", "bst_id": "900100", "bst_code": "900101"},
            {"name": "V2", "source": "vor", "bst_id": "900102", "bst_code": "900103"},
            {"name": "O1", "source": "oebb", "bst_code": {"a": 1}},
        ]
    )
    report = validate_stations(path)
    assert report.total_stations == 3


def test_validate_stations_still_detects_real_oebb_vor_collision() -> None:
    """The type-guard must not over-suppress: a real (str) collision still fires."""
    path = _validation_path(
        [
            {"name": "V1", "source": "vor", "bst_id": "900100", "bst_code": "COLLIDE"},
            {"name": "V2", "source": "vor", "bst_id": "900102", "bst_code": "900103"},
            {"name": "O1", "source": "oebb", "bst_code": "COLLIDE"},
        ]
    )
    report = validate_stations(path)
    # Provider issues must contain the VOR-vs-OEBB bst_code collision.
    reasons = [issue.reason for issue in report.provider_issues]
    assert any("collides with OEBB" in r for r in reasons), reasons
