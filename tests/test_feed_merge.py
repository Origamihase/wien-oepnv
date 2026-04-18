from src.feed.merge import deduplicate_fuzzy

def test_fuzzy_merge_silvester():
    items = [
        {
            "title": "1/2/71/74A/D: Wiener Silvesterlauf Event",
            "description": "Details about Lauf.",
            "guid": "guid1",
            "lines": ["1", "2", "71", "74A", "D"] # Note: keys usually don't have 'lines', parsing happens in function
        },
        {
            "title": "1/2/71/D/U1/U3: Wiener Silvesterpfad Event",
            "description": "Details about Pfad.",
            "guid": "guid2"
        }
    ]

    merged = deduplicate_fuzzy(items)

    assert len(merged) == 1
    item = merged[0]

    # Check Title: All lines combined
    # Lines: 1, 2, 71, 74A, D, U1, U3
    # Sorted natural: 1, 2, 71, 74A, D, U1, U3
    assert "1/2/71/74A/D/U1/U3" in item["title"]
    assert "Silvesterlauf" in item["title"]
    assert "Silvesterpfad" in item["title"]

    # Check Description
    assert "Details about Lauf." in item["description"]
    assert "Details about Pfad." in item["description"]

    # Check GUID updated
    assert item["guid"] != "guid1"
    assert item["guid"] != "guid2"

def test_fuzzy_merge_pubdate_update():
    items = [
        {
            "title": "U1: Störung",
            "description": "Erste Meldung.",
            "guid": "guid1",
            "pubdate": "2023-01-01T10:00:00Z"
        },
        {
            "title": "U1: Störung",
            "description": "Zweite Meldung.",
            "guid": "guid2",
            "pubdate": "2023-01-01T10:30:00Z"
        }
    ]

    merged = deduplicate_fuzzy(items)

    assert len(merged) == 1
    item = merged[0]

    assert item["pubdate"] == "2023-01-01T10:30:00Z"
    assert "Erste Meldung." in item["description"]
    assert "Zweite Meldung." in item["description"]


def test_fuzzy_no_merge_different_events():
    items = [
        {
            "title": "1/2: Demo Ring",
            "description": "Demo",
            "guid": "guid1"
        },
        {
            "title": "1/2: Baustelle Gürtel",
            "description": "Baustelle",
            "guid": "guid2"
        }
    ]

    merged = deduplicate_fuzzy(items)
    assert len(merged) == 2

def test_fuzzy_no_merge_lines_disjoint():
    items = [
        {
            "title": "1: Silvesterlauf",
            "description": "...",
            "guid": "g1"
        },
        {
            "title": "2: Silvesterlauf",
            "description": "...",
            "guid": "g2"
        }
    ]
    # Line overlap is 0
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 2

def test_fuzzy_merge_lines_overlap_threshold():
    # Overlap must be > 0.3
    # Items: A={1,2,3,4}, B={4,5,6,7}. Intersection={4}. Union={1..7} (7 items). 1/7 = 0.14. No merge.
    items = [
        {
            "title": "1/2/3/4: Event A",
            "guid": "g1"
        },
        {
            "title": "4/5/6/7: Event A",
            "guid": "g2"
        }
    ]
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 2

    # Items: A={1,2}, B={2,3}. Int={2}. Union={1,2,3}. 1/3 = 0.33. Merge!
    items2 = [
        {
            "title": "1/2: Event A",
            "guid": "g3"
        },
        {
            "title": "2/3: Event A",
            "guid": "g4"
        }
    ]
    merged2 = deduplicate_fuzzy(items2)
    assert len(merged2) == 1
    assert "1/2/3" in merged2[0]["title"]

def test_fuzzy_merge_description_containment():
    items = [
        {
            "title": "1/2: Event",
            "description": "Short desc",
            "guid": "g1"
        },
        {
            "title": "1/2: Event",
            "description": "This is a Long desc containing Short desc inside it.",
            "guid": "g2"
        }
    ]
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    # Should keep longer one, not concatenate
    assert merged[0]["description"] == "This is a Long desc containing Short desc inside it."
    assert "Short desc\n\n" not in merged[0]["description"]

def test_fuzzy_merge_name_combining():
    items = [
        {
            "title": "1/2: Event Special Run",
            "guid": "g1"
        },
        {
            "title": "1/2: Event Special Walk",
            "guid": "g2"
        }
    ]
    # Tokens "Event", "Special" overlap.
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    assert "Event Special Run & Event Special Walk" in merged[0]["title"] or "Event Special Walk & Event Special Run" in merged[0]["title"]

def test_fuzzy_merge_recursive():
    # A merges with B, result merges with C?
    # Current implementation is iterative.
    # If list is [A, B, C].
    # Loop A: merged=[A]
    # Loop B: match A? Yes. merged=[AB]
    # Loop C: match AB?

    items = [
        {"title": "1/2: Event 2025", "guid": "1"},
        {"title": "2/3: Event 2025", "guid": "2"}, # Merges with 1 -> 1/2/3
        {"title": "3/4: Event 2025", "guid": "3"}  # Matches 1/2/3? Lines: {3} vs {1,2,3}. Int=1, Union=4. 1/4 = 0.25 < 0.3 NO.
    ]
    # Wait, 1/2 and 2/3 merge -> {1,2,3}.
    # 3/4 and {1,2,3}. Intersection {3}. Union {1,2,3,4}. 1/4 = 0.25. No merge.

    # Let's try stronger overlap.
    # A: 1/2/3
    # B: 2/3/4 -> Merges with A (2/3 overlap). Result: 1/2/3/4.
    # C: 3/4/5 -> Matches 1/2/3/4? Int {3,4}. Union {1,2,3,4,5}. 2/5 = 0.4. Merge!

    items = [
        {"title": "1/2/3: Event", "guid": "1"},
        {"title": "2/3/4: Event", "guid": "2"},
        {"title": "3/4/5: Event", "guid": "3"}
    ]
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    assert "1/2/3/4/5" in merged[0]["title"]


def test_fuzzy_merge_provider_priority_vor_over_oebb():
    """Verify that VOR data is prioritized over ÖBB data."""
    items = [
        {
            "title": "1/2: Störung",
            "description": "VOR Details.",
            "guid": "vor_guid",
            "source": "VOR",
            "lines": ["1", "2"] # Note: helper parses title if lines missing
        },
        {
            "title": "1/2: Störung",
            "description": "ÖBB Details.",
            "guid": "oebb_guid",
            "source": "ÖBB",
            "lines": ["1", "2"]
        }
    ]

    # Run dedupe
    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    item = merged[0]

    # Should keep VOR as base (source, guid)
    assert item["source"] == "VOR"
    assert item["guid"] == "vor_guid"

    # Description should contain both if unique
    assert "VOR Details." in item["description"]
    assert "ÖBB Details." in item["description"]

def test_fuzzy_merge_provider_priority_oebb_over_vor_order():
    """Verify that even if ÖBB comes first, VOR wins the merge but keeps VOR data."""
    items = [
        {
            "title": "1/2: Störung",
            "description": "ÖBB Details.",
            "guid": "oebb_guid",
            "source": "ÖBB"
        },
        {
            "title": "1/2: Störung",
            "description": "VOR Details.",
            "guid": "vor_guid",
            "source": "VOR"
        }
    ]

    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    item = merged[0]

    # VOR item should replace ÖBB item in-place or be the result
    # The logic says: if is_oebb_existing and is_vor_item -> Replace Existing with Item (VOR)
    assert item["source"] == "VOR"
    assert item["guid"] == "vor_guid"
    assert "VOR Details." in item["description"]
    assert "ÖBB Details." in item["description"]
