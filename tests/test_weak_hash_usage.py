
import hashlib
from src.build_feed import _identity_for_item, _dedupe_key_for_item

def test_identity_for_item_uses_strong_hash():
    item = {
        "title": "Test Title",
        "starts_at": "2023-01-01T12:00:00Z",
        "ends_at": "2023-01-01T13:00:00Z",
        "source": "test_source",
        "category": "test_category"
    }

    # This function uses hashing internally if no ID is present
    identity = _identity_for_item(item)

    # We expect the identity to contain a hash.
    # If it uses SHA1, the hex digest length is 40.
    # If it uses SHA256, the hex digest length is 64.

    # The identity format is roughly: "source|category|...|F=<hash>" or similar.
    # We look for the "F=<hash>" or "H=<hash>" part.

    parts = identity.split("|")
    hash_part = next((p for p in parts if p.startswith("F=") or p.startswith("H=")), None)

    assert hash_part is not None, f"Identity {identity} does not contain a hash part"

    hash_val = hash_part.split("=", 1)[1]

    # Assert it is SHA256 (64 chars) and NOT SHA1 (40 chars)
    assert len(hash_val) == 64, f"Hash length is {len(hash_val)}, expected 64 (SHA256). Likely still using SHA1."

def test_dedupe_key_for_item_uses_strong_hash():
    item = {
        "title": "Test Title",
        "description": "Test Description",
        "source": "test_source"
    }

    # This uses fallback hashing
    key, used_fallback = _dedupe_key_for_item(item)

    assert used_fallback is True
    assert len(key) == 64, f"Key length is {len(key)}, expected 64 (SHA256). Likely still using SHA1."
