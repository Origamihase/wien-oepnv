from datetime import datetime

from src.utils.serialize import serialize_for_cache


def test_serialize_datetime_to_iso_string():
    dt = datetime(2024, 2, 29, 23, 59, 59)

    assert serialize_for_cache(dt) == "2024-02-29T23:59:59"


def test_serialize_nested_collections_are_json_friendly():
    payload = {
        "created": datetime(2023, 12, 31, 0, 0, 0),
        "items": (
            {"id": 1, "tags": {"b", "a", "c"}},
            {"id": 2, "tags": {3, "2", 1}},
        ),
        "metadata": [
            datetime(2020, 1, 1, 8, 30),
            {"values": ({"nested": datetime(2020, 1, 1)},)},
        ],
    }

    result = serialize_for_cache(payload)

    assert result == {
        "created": "2023-12-31T00:00:00",
        "items": [
            {"id": 1, "tags": ["a", "b", "c"]},
            {"id": 2, "tags": [1, "2", 3]},
        ],
        "metadata": [
            "2020-01-01T08:30:00",
            {"values": [{"nested": "2020-01-01T00:00:00"}]},
        ],
    }

    assert isinstance(result["items"], list)
    assert all(isinstance(item["tags"], list) for item in result["items"])
    assert isinstance(result["metadata"], list)
