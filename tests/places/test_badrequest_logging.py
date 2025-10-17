"""Tests for improved error reporting from the Places client."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from src.places.client import FIELD_MASK_NEARBY, GooglePlacesClient, GooglePlacesConfig, GooglePlacesError


class _ErrorResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Dict[str, Any]:
        return self._payload


class _ErrorSession:
    def __init__(self, response: _ErrorResponse):
        self._response = response

    def post(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: float,
    ) -> _ErrorResponse:
        return self._response


def test_bad_request_error_includes_field_violations() -> None:
    payload = {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Bad request",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [
                        {
                            "field": "X-Goog-FieldMask",
                            "description": "invalid path nextPageToken",
                        }
                    ],
                }
            ],
        }
    }
    response = _ErrorResponse(400, payload)
    session = _ErrorSession(response)
    config = GooglePlacesConfig(
        api_key="dummy",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=2500,
        timeout_s=1,
        max_retries=0,
    )
    client = GooglePlacesClient(config, session=session)

    with pytest.raises(GooglePlacesError) as excinfo:
        client._post(
            "places:searchNearby",
            {
                "languageCode": "de",
                "includedTypes": ["train_station"],
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": 0.0, "longitude": 0.0},
                        "radius": 1000,
                    }
                },
            },
            field_mask=FIELD_MASK_NEARBY,
        )

    message = str(excinfo.value)
    assert "Failed to fetch places (400)" in message
    assert "INVALID_ARGUMENT" in message
    assert "X-Goog-FieldMask: invalid path nextPageToken" in message
