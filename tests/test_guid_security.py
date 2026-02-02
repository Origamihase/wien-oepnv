import pytest
from src.providers.vor import _build_guid
from src.utils.ids import make_guid

def test_vor_guid_dos_protection():
    """
    Verify that _build_guid bounds the length of external IDs to prevent DoS.
    """
    station_id = "12345"
    # Create a massive ID
    huge_id = "A" * 100000
    message = {"id": huge_id}

    guid = _build_guid(station_id, message)

    # Assert the GUID is reasonably short (e.g. < 200 chars).
    # The expected behavior after fix is a hashed ID or truncated ID.
    assert len(guid) < 200, f"GUID length {len(guid)} is too large! Vulnerable to DoS."

    # Ensure uniqueness is preserved via hashing (if implemented via hash)
    # If the fix uses hashing, we expect 'vor:12345:<hash>'
    if "vor:12345:" in guid:
        suffix = guid.split(":")[-1]
        assert len(suffix) == 64 or len(suffix) < 130 # SHA256 hex digest is 64
