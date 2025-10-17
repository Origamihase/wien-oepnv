"""Tests for helpers in scripts.fetch_google_places_stations."""

from scripts.fetch_google_places_stations import _permission_hint


def test_permission_hint_for_blocked_message() -> None:
    message = (
        "PERMISSION_DENIED: Requests to this API places.googleapis.com method "
        "google.maps.places.v1.Places.SearchNearby are blocked."
    )

    hint = _permission_hint(message)

    assert hint
    assert "enable Places API (New)" in hint
    assert "places.googleapis.com" in hint


def test_permission_hint_for_invalid_key() -> None:
    message = "API key is invalid."

    hint = _permission_hint(message)

    assert hint
    assert "GOOGLE_ACCESS_ID" in hint
    assert "AIza" in hint


def test_permission_hint_unknown_message() -> None:
    assert _permission_hint("PERMISSION_DENIED: unknown reason") is None
