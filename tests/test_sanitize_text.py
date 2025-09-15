from src.build_feed import _sanitize_text


def test_sanitize_text_removes_control_characters():
    assert _sanitize_text("\x07test\x1F") == "test"
