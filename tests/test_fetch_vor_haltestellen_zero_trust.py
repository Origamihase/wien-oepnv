"""Zero-Trust regression tests for fetch_vor_haltestellen.fetch_candidates.

A 200 response from the VAO mgate endpoint does not guarantee the body is a
JSON object. A list / null / scalar would slip past ``data.get("svcResL")``
and raise ``AttributeError`` — which is **not** a ``requests.RequestException``
and therefore would not be caught by ``resolve_station``'s exception handler.
The error would propagate out of the per-station loop in ``main()``,
terminating the whole batch and skipping every subsequent station.

These tests pin the shape-validation branch so future refactors cannot drop it.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from scripts.fetch_vor_haltestellen import fetch_candidates


def _make_session(payload: object | None, *, raise_decode: bool = False) -> MagicMock:
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status.return_value = None
    if raise_decode:
        response.json.side_effect = json.JSONDecodeError("Expecting value", "x", 0)
    else:
        response.json.return_value = payload
    session.post.return_value = response
    return session


@pytest.mark.parametrize(
    "non_dict_payload",
    [
        [],
        [{"svcResL": []}],
        None,
        "unexpected string body",
        42,
        3.14,
        True,
    ],
)
def test_fetch_candidates_rejects_non_object_payload(non_dict_payload: object) -> None:
    """A non-dict JSON body must not raise — return [] so the batch loop continues."""
    session = _make_session(non_dict_payload)
    # Must not raise; must return an empty list so resolve_station treats it
    # as "no candidates" and the per-station loop in main() continues.
    assert fetch_candidates(session, "https://example.test/gate", "access", "Some Station") == []


def test_fetch_candidates_rejects_invalid_json() -> None:
    """A malformed JSON body must not raise — same fallback as shape mismatch."""
    session = _make_session(None, raise_decode=True)
    assert fetch_candidates(session, "https://example.test/gate", "access", "Some Station") == []


def test_fetch_candidates_propagates_request_exceptions() -> None:
    """Network-level errors are still surfaced for resolve_station's handler."""
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(requests.RequestException):
        fetch_candidates(session, "https://example.test/gate", "access", "Some Station")


def test_fetch_candidates_returns_locations_on_well_formed_payload() -> None:
    """Happy path: a well-formed payload still returns the location list."""
    payload = {
        "svcResL": [
            {
                "res": {
                    "match": {
                        "locL": [
                            {"type": "S", "extId": "1", "name": "A"},
                            {"type": "S", "extId": "2", "name": "B"},
                            "not-a-mapping",
                        ]
                    }
                }
            }
        ]
    }
    session = _make_session(payload)
    result = fetch_candidates(session, "https://example.test/gate", "access", "Some Station")
    assert result == [
        {"type": "S", "extId": "1", "name": "A"},
        {"type": "S", "extId": "2", "name": "B"},
    ]
