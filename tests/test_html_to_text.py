import pytest

from src.utils.text import html_to_text


@pytest.mark.parametrize("html,expected", [
    ("Line1<br>Line2", "Line1 • Line2"),
    ("<div>foo</div><p>bar</p>baz", "foo • bar • baz"),
    ("<ul><li>foo</li><li>bar</li></ul>baz", "foo • bar • baz"),
    ("<ul><li>Parent<br><ul><li>Child</li></ul></li></ul>End", "Parent • Child • End"),
    (
        "<ul><li>A<ul><li>B</li><li>C</li></ul></li><li>D</li></ul>",
        "A • B • C • D",
    ),
    ("<th>Head1</th><th>Head2</th>End", "Head1 • Head2 • End"),
])
def test_html_to_text_examples(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("", ""),
    ("   ", ""),
    ("Tom &amp; Jerry", "Tom & Jerry"),
    ("<div>&nbsp; A &nbsp; &amp; B  </div>End", "A & B • End"),
    ("• foo • • bar •", "foo • bar"),
])
def test_html_to_text_edge_cases(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("bei<br>Station", "bei Station"),
    ("An<br>der Haltestelle", "An der Haltestelle"),
    ("bei<br>• foo", "bei foo"),
    ("In<br>• der Station", "In der Station"),
    ("Am<br>Bahnhof", "Am Bahnhof"),
    ("Vom<br>• Bahnsteig", "Vom Bahnsteig"),
    ("Zur<br>• Station", "Zur Station"),
    ("Zum<br>• Ausgang", "Zum Ausgang"),
    ("Nach<br>• Wien", "Nach Wien"),
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
