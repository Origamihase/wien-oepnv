import json
import pytest
from unittest.mock import MagicMock

from src.providers import vor

def test_resolve_station_ids_uses_fetch_content_safe(monkeypatch):
    """
    Test that resolve_station_ids uses fetch_content_safe to prevent SSRF.
    This ensures that we don't regress to using session.get() directly.
    """

    # Mock session_with_retries
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = None

    mock_session_constructor = MagicMock(return_value=mock_session)
    monkeypatch.setattr(vor, "session_with_retries", mock_session_constructor)

    # Mock fetch_content_safe
    mock_fetch_safe = MagicMock()
    mock_fetch_safe.return_value = json.dumps({
        "LocationList": {
            "Stop": [
                {"id": "123", "name": "Westbahnhof"}
            ]
        }
    }).encode("utf-8")
    monkeypatch.setattr(vor, "fetch_content_safe", mock_fetch_safe)

    # Call the function
    station_names = ["Westbahnhof"]
    result = vor.resolve_station_ids(station_names)

    # Verification
    assert mock_fetch_safe.called, "fetch_content_safe was not called"
    # Ensure params are passed correctly
    args, kwargs = mock_fetch_safe.call_args
    assert args[0] == mock_session
    assert "location.name" in args[1]
    assert kwargs['params']['input'] == "Westbahnhof"

def test_resolve_station_ids_handles_unsafe_url_error(monkeypatch):
    """
    Test that ValueError (from fetch_content_safe for unsafe URLs) is caught and handled.
    """
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = None
    monkeypatch.setattr(vor, "session_with_retries", MagicMock(return_value=mock_session))

    # Mock fetch_content_safe to raise ValueError (simulating unsafe IP/URL)
    mock_fetch_safe = MagicMock(side_effect=ValueError("Unsafe URL"))
    monkeypatch.setattr(vor, "fetch_content_safe", mock_fetch_safe)

    # Call the function
    result = vor.resolve_station_ids(["Westbahnhof"])

    # Should return empty list (or at least not crash) and log warning
    assert result == []
    assert mock_fetch_safe.called
