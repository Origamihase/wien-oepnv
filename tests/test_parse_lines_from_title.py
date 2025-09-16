from src.build_feed import _parse_lines_from_title


def test_parse_lines_from_title_ignores_non_line_prefix():
    assert _parse_lines_from_title("Neubaugasse 69: Sperre") == []


def test_parse_lines_from_title_accepts_line_token():
    assert _parse_lines_from_title("N81: Rohrleitungsarbeiten") == ["N81"]


def test_parse_lines_from_title_accepts_letter_digit_tokens():
    assert _parse_lines_from_title("S45: Baustelle") == ["S45"]


def test_parse_lines_from_title_accepts_multi_letter_tokens():
    assert _parse_lines_from_title("RJX/RJ: Hinweis") == ["RJX", "RJ"]
