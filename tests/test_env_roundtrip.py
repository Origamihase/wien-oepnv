
import pytest
from src.utils.env import _parse_value
from src.utils.configuration_wizard import _escape_env_value

def test_env_roundtrip_multiline():
    """Verify that multiline values survive the roundtrip via .env escaping."""
    original = "first line\nsecond line"

    # Simulate writing to .env
    escaped = _escape_env_value(original)
    # _escape_env_value returns a quoted string like "first line\\nsecond line"

    # Simulate reading from .env
    # We pass the quoted string (which _parse_env_file would extract) to _parse_value
    parsed = _parse_value(escaped)

    assert parsed == original, f"Expected {repr(original)}, got {repr(parsed)}"

def test_env_roundtrip_control_chars():
    """Verify that control characters survive the roundtrip."""
    original = "tab\tcr\r"
    escaped = _escape_env_value(original)
    parsed = _parse_value(escaped)

    # Note: _escape_env_value does NOT escape \t currently, it leaves it literal.
    # But it escapes \r to \\r.
    assert parsed == original, f"Expected {repr(original)}, got {repr(parsed)}"

def test_env_unescape_standard():
    """Verify standard escape sequences in double quotes."""
    # "foo\nbar" -> foo followed by newline followed by bar
    input_str = '"foo\\nbar"'
    expected = "foo\nbar"
    assert _parse_value(input_str) == expected

def test_env_unescape_cr():
    input_str = '"foo\\rbar"'
    expected = "foo\rbar"
    assert _parse_value(input_str) == expected

def test_env_unescape_tab():
    input_str = '"foo\\tbar"'
    expected = "foo\tbar"
    assert _parse_value(input_str) == expected
