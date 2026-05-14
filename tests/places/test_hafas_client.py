"""Tests for the native HAFAS (ÖBB Scotty) Mgate enrichment client.

Covers the profile loader, the Mgate payload / mac construction, the
LocMatch response parser, the CircuitBreaker integration, and the
public ``enrich_station_with_hafas`` happy / failure paths. The HTTP
layer is mocked at ``request_safe`` so the suite runs with no real
network IO.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.places import hafas_client
from src.places.hafas_client import (
    HafasLocation,
    HafasProfile,
    HafasProfileError,
    enrich_station_with_hafas,
)
from src.utils.circuit_breaker import CircuitBreakerOpen


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def reset_module() -> Iterator[None]:
    """Reset module-level cache + breaker so tests don't leak state."""
    hafas_client._PROFILE_CACHE = None
    hafas_client._PROFILE_LOAD_FAILED = False
    hafas_client._BREAKER.reset()
    yield
    hafas_client._PROFILE_CACHE = None
    hafas_client._PROFILE_LOAD_FAILED = False
    hafas_client._BREAKER.reset()


@pytest.fixture
def profile_no_salt() -> HafasProfile:
    return HafasProfile(
        salt="",
        ver="1.45",
        auth={"type": "AID", "aid": "OWDL4fE4ixNiPBBm"},
        client={"id": "OEBB", "type": "WEB", "name": "webapp", "l": "vs_webapp"},
    )


@pytest.fixture
def profile_with_salt() -> HafasProfile:
    return HafasProfile(
        salt="abcdef0123456789",
        ver="1.45",
        auth={"type": "AID", "aid": "TESTAID"},
        client={"id": "OEBB", "type": "WEB", "name": "webapp", "l": "vs_webapp"},
    )


def _mock_locmatch_response(name: str, ext_id: str, x: int, y: int) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.json.return_value = {
        "ver": "1.45",
        "lang": "de",
        "id": "req",
        "err": "OK",
        "svcResL": [
            {
                "meth": "LocMatch",
                "err": "OK",
                "res": {
                    "match": {
                        "field": "S",
                        "state": "S",
                        "locL": [
                            {
                                "lid": f"A=1@O={name}@X={x}@Y={y}@L={ext_id}",
                                "type": "S",
                                "name": name,
                                "extId": ext_id,
                                "crd": {"x": x, "y": y},
                            }
                        ],
                    }
                },
            }
        ],
    }
    return response


# --------------------------------------------------------------------- profile


def test_load_profile_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(HafasProfileError):
        hafas_client._load_profile(missing)


def test_load_profile_rejects_non_object(tmp_path: Path) -> None:
    bad = tmp_path / "p.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(HafasProfileError):
        hafas_client._load_profile(bad)


def test_load_profile_requires_aid(tmp_path: Path) -> None:
    bad = tmp_path / "p.json"
    bad.write_text(
        json.dumps(
            {
                "salt": "",
                "ver": "1.45",
                "auth": {"type": "AID"},
                "client": {"id": "OEBB", "type": "WEB"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(HafasProfileError):
        hafas_client._load_profile(bad)


def test_load_profile_accepts_well_formed(tmp_path: Path) -> None:
    good = tmp_path / "p.json"
    good.write_text(
        json.dumps(
            {
                "salt": "abcd",
                "ver": "1.45",
                "auth": {"type": "AID", "aid": "TESTAID"},
                "client": {"id": "OEBB", "type": "WEB", "name": "webapp", "l": "vs_webapp"},
            }
        ),
        encoding="utf-8",
    )
    profile = hafas_client._load_profile(good)
    assert profile["salt"] == "abcd"
    assert profile["ver"] == "1.45"
    assert profile["auth"]["aid"] == "TESTAID"


# --------------------------------------------------------------------- mac signing


def test_compute_mac_returns_empty_when_salt_missing() -> None:
    assert hafas_client._compute_mac("body", "") == ""


def test_compute_mac_matches_md5_of_body_plus_salt() -> None:
    body = '{"id":"OEBB"}'
    salt = "abcdef"
    expected = hashlib.md5(  # noqa: S324 - non-security use
        (body + salt).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    assert hafas_client._compute_mac(body, salt) == expected


def test_build_request_url_omits_mac_when_empty() -> None:
    assert hafas_client._build_request_url("") == hafas_client._HAFAS_ENDPOINT


def test_build_request_url_appends_mac_query() -> None:
    url = hafas_client._build_request_url("abcd1234")
    assert url.endswith("?mac=abcd1234")


# --------------------------------------------------------------------- payload shape


def test_loc_match_payload_carries_profile_fields(profile_no_salt: HafasProfile) -> None:
    payload = hafas_client._build_loc_match_payload(profile_no_salt, "Wien Hauptbahnhof")
    assert payload["ver"] == "1.45"
    assert payload["auth"] == {"type": "AID", "aid": "OWDL4fE4ixNiPBBm"}
    assert payload["lang"] == "de"
    svc_req_list = payload["svcReqL"]
    assert isinstance(svc_req_list, list)
    first = svc_req_list[0]
    assert isinstance(first, dict)
    assert first["meth"] == "LocMatch"
    req_field = first["req"]
    assert isinstance(req_field, dict)
    input_field = req_field["input"]
    assert isinstance(input_field, dict)
    loc = input_field["loc"]
    assert isinstance(loc, dict)
    assert loc["name"] == "Wien Hauptbahnhof"
    assert input_field["maxLoc"] == 1


# --------------------------------------------------------------------- parser


def test_extract_first_location_normalises_coordinates() -> None:
    location = hafas_client._extract_first_location(
        _mock_locmatch_response("Wien Hauptbahnhof", "8100353", 16377778, 48185222).json()
    )
    assert location == HafasLocation(
        name="Wien Hauptbahnhof",
        extId="8100353",
        lon=16.377778,
        lat=48.185222,
    )


@pytest.mark.parametrize(
    "broken_payload",
    [
        None,
        {},
        {"svcResL": []},
        {"svcResL": [{"err": "FAIL", "res": {}}]},
        {"svcResL": [{"err": "OK", "res": {"match": {"locL": []}}}]},
        {"svcResL": [{"err": "OK", "res": {"match": {"locL": [{"name": "X"}]}}}]},
        {
            "svcResL": [
                {
                    "err": "OK",
                    "res": {
                        "match": {
                            "locL": [
                                {
                                    "name": "X",
                                    "extId": "1",
                                    "crd": {"x": "not-a-number", "y": 1},
                                }
                            ]
                        }
                    },
                }
            ]
        },
    ],
)
def test_extract_first_location_returns_none_on_broken_payload(
    broken_payload: object,
) -> None:
    assert hafas_client._extract_first_location(broken_payload) is None


# --------------------------------------------------------------------- end-to-end


def test_enrich_returns_none_for_blank_name(reset_module: None) -> None:
    assert enrich_station_with_hafas("   ") is None


def test_enrich_returns_coords_when_profile_loaded(
    reset_module: None, profile_no_salt: HafasProfile
) -> None:
    response = _mock_locmatch_response("Wien Hauptbahnhof", "8100353", 16377778, 48185222)
    with patch.object(hafas_client, "_get_profile", return_value=profile_no_salt):
        with patch.object(hafas_client, "request_safe", return_value=response) as rs:
            result = enrich_station_with_hafas("Wien Hauptbahnhof")
    assert result == HafasLocation(
        name="Wien Hauptbahnhof",
        extId="8100353",
        lon=16.377778,
        lat=48.185222,
    )
    # When salt is empty the URL has no ``?mac=`` query.
    call_args: Any = rs.call_args
    url = call_args.args[1]
    assert "mac=" not in url


def test_enrich_signs_request_when_salt_present(
    reset_module: None, profile_with_salt: HafasProfile
) -> None:
    response = _mock_locmatch_response("Wien Hauptbahnhof", "8100353", 16377778, 48185222)
    with patch.object(hafas_client, "_get_profile", return_value=profile_with_salt):
        with patch.object(hafas_client, "request_safe", return_value=response) as rs:
            enrich_station_with_hafas("Wien Hauptbahnhof")
    call_args: Any = rs.call_args
    url = call_args.args[1]
    body = call_args.kwargs["data"].decode("utf-8")
    expected_mac = hashlib.md5(  # noqa: S324 - non-security use
        (body + profile_with_salt["salt"]).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    assert f"mac={expected_mac}" in url


def test_enrich_returns_none_when_profile_missing(reset_module: None) -> None:
    with patch.object(hafas_client, "_get_profile", return_value=None):
        assert enrich_station_with_hafas("Wien Hauptbahnhof") is None


def test_enrich_returns_none_on_network_failure(
    reset_module: None, profile_no_salt: HafasProfile
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> requests.Response:
        raise requests.ConnectionError("boom")

    with patch.object(hafas_client, "_get_profile", return_value=profile_no_salt):
        with patch.object(hafas_client, "request_safe", side_effect=boom):
            assert enrich_station_with_hafas("Wien Hauptbahnhof") is None


def test_enrich_returns_none_when_breaker_open(
    reset_module: None, profile_no_salt: HafasProfile
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> requests.Response:
        raise requests.ConnectionError("boom")

    with patch.object(hafas_client, "_get_profile", return_value=profile_no_salt):
        with patch.object(hafas_client, "request_safe", side_effect=boom):
            # Trip the breaker by exceeding the failure threshold.
            for _ in range(hafas_client._BREAKER.failure_threshold):
                assert enrich_station_with_hafas("Wien Hauptbahnhof") is None
            assert hafas_client._BREAKER.state.value == "open"

        # While the breaker is open, request_safe is not even called.
        with patch.object(hafas_client, "request_safe") as never_called:
            assert enrich_station_with_hafas("Wien Hauptbahnhof") is None
            never_called.assert_not_called()


def test_enrich_handles_invalid_json(
    reset_module: None, profile_no_salt: HafasProfile
) -> None:
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.json.side_effect = ValueError("bad json")

    with patch.object(hafas_client, "_get_profile", return_value=profile_no_salt):
        with patch.object(hafas_client, "request_safe", return_value=response):
            assert enrich_station_with_hafas("Wien Hauptbahnhof") is None


def test_enrich_propagates_open_breaker_as_none(
    reset_module: None, profile_no_salt: HafasProfile
) -> None:
    with patch.object(
        hafas_client._BREAKER, "call", side_effect=CircuitBreakerOpen("open")
    ):
        assert enrich_station_with_hafas("Wien Hauptbahnhof") is None
