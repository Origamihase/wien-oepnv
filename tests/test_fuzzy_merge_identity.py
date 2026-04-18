from src.feed.merge import deduplicate_fuzzy

def test_fuzzy_merge_identity():
    # Two items with significant overlap
    items = [
        {
            "title": "U1: Störung Event",
            "description": "desc1",
            "guid": "guid1",
            "_calculated_identity": "calc_ident1",
            "_identity": "ident1"
        },
        {
            "title": "U1: Störung Event",
            "description": "desc2",
            "guid": "guid2",
            "_calculated_identity": "calc_ident2",
            "_identity": "ident2"
        }
    ]

    merged = deduplicate_fuzzy(items)

    # Should be merged into 1 item
    assert len(merged) == 1

    # Check that identity matches guid and _calculated_identity is gone
    merged_item = merged[0]
    assert "_calculated_identity" not in merged_item
    assert merged_item["guid"] == merged_item["_identity"]

    # guid should be deterministic from new title
    import hashlib
    assert merged_item["guid"] == hashlib.sha256(merged_item["title"].encode("utf-8")).hexdigest()
