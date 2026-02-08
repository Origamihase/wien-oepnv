import hashlib
import pytest
from src.utils.ids import make_guid

def test_guid_collision_vulnerability():
    """Verify that current implementation PREVENTS GUID collisions via pipe injection."""
    guid1 = make_guid("a|b", "c")
    guid2 = make_guid("a", "b|c")

    # After fix, these should be different
    assert guid1 != guid2, "Collision detected! Pipe injection vulnerability persists."

def test_guid_backward_compatibility():
    """Verify that safe inputs produce the expected hash (compatibility check)."""
    # "foo|bar" -> sha256
    # If inputs contain no special chars, the behavior should be the same as simple join
    # because replace() won't change anything.
    expected = hashlib.sha256("foo|bar".encode("utf-8")).hexdigest()
    actual = make_guid("foo", "bar")
    assert actual == expected, "Safe input should match simple join behavior"
