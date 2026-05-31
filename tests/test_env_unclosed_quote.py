"""bug: env._parse_value returned the raw value INCLUDING the unclosed opening
quote for a value like ``"abc`` (operator opened a quote and forgot to close
it), so the stray quote silently corrupted the token/credential at the use
site. It now returns the decoded content after the opening quote, consistent
with the closed-quote path — closed, unquoted and commented values are
unchanged.
"""
from src.utils.env import _parse_value


def test_unclosed_double_quote_returns_content_without_quote() -> None:
    assert _parse_value('"abc') == "abc"
    # escapes are decoded just like a closed double-quoted value
    assert _parse_value('"a\\tb') == "a\tb"
    # a lone opening quote yields the empty content
    assert _parse_value('"') == ""


def test_unclosed_single_quote_returns_content_without_quote() -> None:
    assert _parse_value("'xyz") == "xyz"


def test_closed_and_unquoted_values_unchanged() -> None:
    assert _parse_value('"abc"') == "abc"
    assert _parse_value("'xyz'") == "xyz"
    assert _parse_value("abc") == "abc"
    assert _parse_value("value # comment") == "value"
    assert _parse_value('"a\\tb"') == "a\tb"
