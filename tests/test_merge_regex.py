from src.feed.merge import _parse_title

def test_parse_title_whitespace_tolerance():
    title = "U1 / U2 : Störung am Schottentor"
    lines, name = _parse_title(title)

    assert lines == {"U1", "U2"}
    assert name == "Störung am Schottentor"
