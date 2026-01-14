import pytest
from src.feed.merge import deduplicate_fuzzy

def test_fuzzy_merge_provider_priority_vor_wins_over_oebb():
    """
    Test that a VOR event overrides an ÖBB event when they are duplicates.
    The VOR metadata (start time, etc.) should be preserved.
    """
    items = [
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Details from ÖBB. This text has extra info.",
            "guid": "oebb_guid_1",
            "provider": "oebb",
            "starts_at": "2025-01-01T10:00:00Z", # Older/Different
            "source": "oebb" # Typically used by build_feed
        },
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Short VOR text.",
            "guid": "vor_guid_1",
            "provider": "vor",
            "starts_at": "2025-01-01T10:05:00Z", # Correct/Official
            "source": "vor"
        }
    ]

    # Expected behavior:
    # - Merged into one item
    # - Provider/Source is 'vor'
    # - starts_at is 10:05
    # - Description combines both (optional, but requested)

    merged = deduplicate_fuzzy(items)

    assert len(merged) == 1
    item = merged[0]

    # Verify VOR priority
    assert item["guid"] == "vor_guid_1"
    assert item["provider"] == "vor"
    assert item["starts_at"] == "2025-01-01T10:05:00Z"

    # Verify description merging (ÖBB info should be appended if unique)
    assert "Short VOR text." in item["description"]
    assert "Details from ÖBB" in item["description"]

def test_fuzzy_merge_provider_priority_vor_wins_reverse_order():
    """
    Same as above, but items are processed in reverse order (VOR exists, ÖBB comes later).
    """
    items = [
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Short VOR text.",
            "guid": "vor_guid_1",
            "provider": "vor",
            "source": "vor"
        },
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Details from ÖBB.",
            "guid": "oebb_guid_1",
            "provider": "oebb",
            "source": "oebb"
        }
    ]

    merged = deduplicate_fuzzy(items)

    assert len(merged) == 1
    item = merged[0]

    assert item["guid"] == "vor_guid_1"
    assert item["provider"] == "vor"
    assert "Short VOR text." in item["description"]
    assert "Details from ÖBB" in item["description"]

def test_fuzzy_merge_provider_priority_no_provider_field():
    """
    Ensure no crash if 'provider' field is missing, falls back to normal merge.
    """
    items = [
        {
            "title": "S1: Delay",
            "description": "Desc 1",
            "guid": "g1",
            "source": "oebb"
        },
        {
            "title": "S1: Delay",
            "description": "Desc 2",
            "guid": "g2",
            # No provider field
        }
    ]

    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    # Should use normal logic (merge descriptions, generate new GUID)
    assert merged[0]["guid"] != "g1"
    assert merged[0]["guid"] != "g2"
    assert "Desc 1" in merged[0]["description"]
    assert "Desc 2" in merged[0]["description"]
