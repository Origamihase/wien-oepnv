from src.utils.serialize import serialize_for_cache

import pytest

def test_serialize_deep_nesting_fails_gracefully():
    # Construct a deeply nested structure exceeding the proposed limit (e.g., 200)
    # but within Python's recursion limit (1000) to ensure we hit our check first.
    deep_structure = {"val": 1}
    for _ in range(250):
        deep_structure = {"nested": deep_structure}

    with pytest.raises(RecursionError, match="Maximum recursion depth 50 exceeded"):
        serialize_for_cache(deep_structure)

def test_serialize_reasonable_depth_works():
    # Construct a moderately nested structure (e.g., 40 levels)
    # This should pass without error (limit is 50, but testing 40 to be safe with counting)
    deep_structure = {"val": 1}
    for _ in range(40):
        deep_structure = {"nested": deep_structure}

    result = serialize_for_cache(deep_structure)
    assert result is not None
