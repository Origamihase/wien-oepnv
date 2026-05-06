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
def test_html_to_text_examples(html: str, expected: str) -> None:
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("", ""),
    ("   ", ""),
    ("Tom &amp; Jerry", "Tom & Jerry"),
    ("<div>&nbsp; A &nbsp; &amp; B  </div>End", "A & B\nEnd"),
    ("• foo • • bar •", "foo • bar"),
])
def test_html_to_text_edge_cases(html: str, expected: str) -> None:
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
def test_preposition_bullet_stripping(html: str, expected: str) -> None:
    assert html_to_text(html) == expected


@pytest.mark.parametrize("html,expected", [
    # Wiener-Linien line codes are conventionally written compact —
    # ``11A``, ``5B``, ``27A`` — and must NOT be split. The previous
    # behaviour ``11A → 11 A`` produced visibly wrong feed descriptions
    # like ``Linie 11 A: Unregelmäßige Intervalle …``. (Bug 14A)
    ("10A", "10A"),
    ("12A", "12A"),
    ("11A", "11A"),
    ("5B", "5B"),
    ("U6", "U6"),
    # Multi-character unit words still get split off for readability.
    ("12Uhr", "12 Uhr"),
    ("20kg", "20 kg"),
    ("2m", "2 m"),
])
def test_line_codes_and_units(html: str, expected: str) -> None:
    assert html_to_text(html) == expected
