import pytest
from src.utils.text import html_to_text


@pytest.mark.parametrize("html,expected", [
    ("Line1<br>Line2", "Line1 • Line2"),
    ("<div>foo</div><p>bar</p>baz", "foo • bar • baz"),
    ("<ul><li>foo</li><li>bar</li></ul>baz", "foo • bar • baz"),
    ("<ul><li>Parent<br><ul><li>Child</li></ul></li></ul>End", "Parent • Child • End"),
    ("<th>Head1</th><th>Head2</th>End", "Head1 • Head2 • End"),
])
def test_html_to_text_examples(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("", ""),
    ("   ", ""),
    ("Tom &amp; Jerry", "Tom & Jerry"),
    ("<div>&nbsp; A &nbsp; &amp; B  </div>End", "A & B • End"),
])
def test_html_to_text_edge_cases(html, expected):
    assert html_to_text(html) == expected
