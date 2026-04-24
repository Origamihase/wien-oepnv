import pytest

from src.utils.text import html_to_text


@pytest.mark.parametrize("html,expected", [
    ("Line1<br>Line2", "Line1\nLine2"),
    ("<div>foo</div><p>bar</p>baz", "foo\nbar\nbaz"),
    ("<ul><li>foo</li><li>bar</li></ul>baz", "• foo\n• bar\nbaz"),
    ("<ul><li>Parent<br><ul><li>Child</li></ul></li></ul>End", "• Parent\n• Child\nEnd"),
    (
        "<ul><li>A<ul><li>B</li><li>C</li></ul></li><li>D</li></ul>",
        "• A\n• B\n• C\n• D",
    ),
    ("<th>Head1</th><th>Head2</th>End", "Head1\nHead2\nEnd"),
    ("Zeitraum:<br>Ab Montag", "Zeitraum:\nAb Montag"),
])
def test_html_to_text_examples(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("", ""),
    ("   ", ""),
    ("Tom &amp; Jerry", "Tom & Jerry"),
    ("<div>&nbsp; A &nbsp; &amp; B  </div>End", "A & B\nEnd"),
    ("• foo • • bar •", "foo • bar"),
])
def test_html_to_text_edge_cases(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("bei<br>Station", "bei\nStation"),
    ("An<br>der Haltestelle", "An\nder Haltestelle"),
    ("bei<br>• foo", "bei\nfoo"),
    ("In<br>• der Station", "In\nder Station"),
    ("Am<br>Bahnhof", "Am\nBahnhof"),
    ("Vom<br>• Bahnsteig", "Vom\nBahnsteig"),
    ("Zur<br>• Station", "Zur\nStation"),
    ("Zum<br>• Ausgang", "Zum\nAusgang"),
    ("Nach<br>• Wien", "Nach\nWien"),
])
def test_preposition_bullet_stripping(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("10A", "10 A"),
    ("12A", "12 A"),
    ("U6", "U6"),
    ("2m", "2 m"),
])
def test_line_codes_and_units(html, expected):
    assert html_to_text(html) == expected


def test_truncate_html_dos_protection():
    from src.utils.text import truncate_html
    # Generate a string larger than the 500_000 char threshold
    large_payload = "A" * 600_000
    result = truncate_html(large_payload, limit=100)

    # We set max safe size to 500,000 chars, after which it crops.
    # But since the limit is 100, the final output will be 100 chars + ellipsis.
    # The main point is that it runs fast and doesn't crash or hang parsing 600k+ chars.
    assert len(result) == 100 + len("...")
    assert result.endswith("...")
