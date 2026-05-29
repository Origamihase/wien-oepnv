from src.providers.wl_lines import LINE_CODE_RE, _tok, _make_line_pairs_from_related


def test_tok_returns_empty_for_none_and_empty() -> None:
    assert _tok(None) == ""
    assert _tok("") == ""


def test_make_line_pairs_from_related_ignores_none() -> None:
    pairs = _make_line_pairs_from_related(["5", None, "U1", ""])
    assert pairs == [("5", "5"), ("U1", "U1")]


def test_make_line_pairs_from_related_rejects_dict_shaped_entries() -> None:
    """A non-string ``relatedLines`` entry must NOT pollute the bucket.

    Pre-fix ``_tok`` called ``str(dict)`` which produced ``"nameU1"`` for
    ``{"name": "U1"}`` — a garbage token that landed in the bucket key,
    the GUID and the emitted title verbatim. The defensive
    ``isinstance(x, str)`` gate skips the malformed entry so it
    contributes zero tokens.
    """
    pairs = _make_line_pairs_from_related(
        ["U6", {"name": "U1"}, ["U2"], 42, "13A"]
    )
    # Only the two well-formed string entries survive.
    assert pairs == [("U6", "U6"), ("13A", "13A")]


def test_line_code_re_rejects_lowercase_bare_letter() -> None:
    """``LINE_CODE_RE`` must not match a lowercase bare letter.

    Pre-fix the regex carried ``re.IGNORECASE`` and the bare ``[A-Z]``
    alternative therefore matched a sentence-start German particle
    (``"a"`` / ``"e"`` etc.). The matched lowercase letter was then
    upper-cased by ``_clean_line_token`` and accepted by
    ``_STRICT_LINE_TOKEN_RE`` as a bus / tram line letter.
    """
    # "Bauarbeiten a Karlsplatz" — the bare lowercase "a" must NOT
    # be picked up as a line code. The capitalised station tokens
    # ("Bauarbeiten", "Karlsplatz") are multi-letter and never
    # matched the single-letter alternative.
    assert LINE_CODE_RE.findall("Bauarbeiten a Karlsplatz") == []
    # Regression guard: real upper-cased bare letter (WL tram D) is
    # still extracted.
    assert "D" in LINE_CODE_RE.findall("Linie D im Bauarbeiten-Modus")
    # Regression guard: canonical digit-bearing codes still match.
    assert "U6" in LINE_CODE_RE.findall("U6: Sperre")
    assert "S40" in LINE_CODE_RE.findall("S40 verspätet")
    assert "41E" in LINE_CODE_RE.findall("Bus 41E Umleitung")
