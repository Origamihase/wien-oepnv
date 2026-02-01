import pytest
from src.providers.vor import _build_guid

def test_vor_guid_bounded_length():
    """
    Verify that the fallback GUID generation produces fixed-length IDs
    even with large input content (using SHA256).
    """
    station_id = "12345"
    large_text = "A" * 10000
    message = {"head": "Some Title", "text": large_text}

    guid = _build_guid(station_id, message)

    # SHA256 hex digest is 64 chars.
    # Format is vor:{station_id}:{hash}
    # vor:12345: = 10 chars.
    # Total ~ 74 chars.
    assert len(guid) < 100

    print(f"GUID length: {len(guid)}")
    print(f"GUID: {guid}")
