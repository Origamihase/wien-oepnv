from src.providers.wl_lines import _tok, _make_line_pairs_from_related


def test_tok_returns_empty_for_none_and_empty():
    assert _tok(None) == ""
    assert _tok("") == ""


def test_make_line_pairs_from_related_ignores_none():
    pairs = _make_line_pairs_from_related(["5", None, "U1", ""])
    assert pairs == [("5", "5"), ("U1", "U1")]
