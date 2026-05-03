from src.build_feed import _sanitize_text


def test_sanitize_text_removes_control_characters() -> None:
    assert _sanitize_text("\x07test\x1F") == "test"
