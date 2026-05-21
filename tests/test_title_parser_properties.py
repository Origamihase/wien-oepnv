"""Property-based tests for the WL / merge / ÖBB title parsers.

These parsers absorb adversarial upstream input (provider API titles,
operator-edited cache entries, regression vectors from past bugs) and
need to satisfy three invariants under every possible input shape:

1. **No-crash robustness** — every code path must return cleanly
   (no ``AttributeError`` / ``IndexError`` / ``re`` runaway) even
   when handed pathological inputs (empty strings, lone separators,
   pure-whitespace, control characters, very long strings, weird
   Unicode combining marks).
2. **Idempotency** — running the parser twice on its own output must
   produce the same result. The parsers are used downstream as
   normalisation steps (``_post_filter_wl`` re-runs ``_extract_prefix_
   lines`` on the rebuilt title for example) and would otherwise drift.
3. **Length bound** — the explicit 500-char input cap in each parser
   must hold for every input. Without this bound, a planted overlong
   upstream title would inflate downstream regex work and propagate
   into committed cache files.

Hypothesis is the right tool here because the parsers' regex grammar
is complex enough that hand-curated test cases routinely miss edge
positions in the input space (a missing whitespace, a borderline
boundary, a Unicode normalisation drift). Property tests sample
broadly across the space and have already caught two real regressions
during development (the ``17:30 …`` and ``Achtung:`` false-positives
fixed in PR #1608).

Each test uses ``deadline=None`` because the Hugging Face translation
pipeline is occasionally cold-loaded by some imports, which can trip
the default 200 ms per-example deadline. The ``max_examples`` cap
keeps each test under one second on CI.
"""
from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from src.feed.merge import _parse_title
from src.providers.wl_lines import _extract_prefix_lines, _ensure_line_prefix


# Strategy for plausible WL/ÖBB title input. Mix of structured prefixes
# (line codes + colon + body) and adversarial fragments (no prefix,
# stacked prefixes, control characters, very long inputs).
_TITLE_CHARS = st.characters(
    # Mypy --strict cannot prove the literal-string tuple matches Hypothesis's
    # ``Literal[…]`` enumeration of Unicode category codes. ``# type: ignore[arg-type]``
    # mirrors the canonical fix shape in ``tests/test_fuzzing.py`` for the
    # same ``("Cs",)`` surrogate-exclusion pattern.
    blacklist_categories=("Cs",),  # type: ignore[arg-type]  # surrogates — invalid in str
)
_titles = st.text(
    alphabet=_TITLE_CHARS,
    min_size=0,
    max_size=600,  # exceed the 500-char internal cap to exercise truncation
)


@given(title=_titles)
@settings(max_examples=300, deadline=None)
def test_extract_prefix_lines_never_crashes(title: str) -> None:
    """``_extract_prefix_lines`` must return cleanly on any input."""
    body, lines = _extract_prefix_lines(title)
    assert isinstance(body, str)
    assert isinstance(lines, list)
    for line in lines:
        assert isinstance(line, str)


@given(title=_titles)
@settings(max_examples=300, deadline=None)
def test_extract_prefix_lines_body_bounded(title: str) -> None:
    """The body cannot be longer than the 500-char truncation cap.

    ``_extract_prefix_lines`` truncates inputs longer than 500 chars
    upfront. The returned body is the post-prefix-strip remainder of
    that truncated input, so its length is bounded by the same cap.
    """
    body, _ = _extract_prefix_lines(title)
    assert len(body) <= 500


@given(title=_titles)
@settings(max_examples=300, deadline=None)
def test_extract_prefix_lines_idempotent(title: str) -> None:
    """Running the parser on its rebuilt canonical form is a fixed point.

    The ``_post_filter_wl`` call in ``src/build_feed.py`` rebuilds the
    canonical ``L1/L2: body`` form from the parser's output and feeds
    it back to other parsers downstream. If the parser were not
    idempotent on its own canonical output, the next round would
    extract different lines / body and the title would drift across
    cache re-reads. This test pins the fixed-point property by running
    the parser twice on the canonical rebuild and asserting the second
    pass produces an identical result.
    """
    body, lines = _extract_prefix_lines(title)
    if not lines or not body:
        # No rebuild happens in ``_post_filter_wl`` when either side is
        # empty — the original title is kept verbatim. Idempotency is
        # trivially satisfied because no canonical form is emitted.
        return
    rebuilt = f"{'/'.join(lines)}: {body}"
    body2, lines2 = _extract_prefix_lines(rebuilt)
    assert body2 == body, (
        f"body drift on idempotent re-parse: {body!r} -> {body2!r} "
        f"(rebuilt={rebuilt!r})"
    )
    assert lines2 == lines, (
        f"lines drift on idempotent re-parse: {lines} -> {lines2} "
        f"(rebuilt={rebuilt!r})"
    )


@given(title=_titles, supplied=st.lists(
    st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7e),
            min_size=1, max_size=10),
    min_size=0, max_size=5,
))
@settings(max_examples=200, deadline=None)
def test_ensure_line_prefix_never_crashes(title: str, supplied: list[str]) -> None:
    """``_ensure_line_prefix`` is the canonical title-rebuild entry point.

    It must handle every combination of input title shape and supplied
    line list without crashing. Pre-fix the substring-vs-token bugs
    closed in PR #1608, this function could mangle a generic-word
    prefix into ``ACHTUNG: …`` form; the property test below asserts at
    minimum that the return type is a string regardless of input.
    """
    result = _ensure_line_prefix(title, supplied)
    assert isinstance(result, str)


@given(title=_titles)
@settings(max_examples=300, deadline=None)
def test_parse_title_never_crashes(title: str) -> None:
    """``feed/merge._parse_title`` runs on every item title during dedup.

    The function returns ``(lines_set, event_name)``. Both must always
    be of the documented type regardless of the input shape.
    """
    lines, name = _parse_title(title)
    assert isinstance(lines, set)
    assert isinstance(name, str)
    for line in lines:
        assert isinstance(line, str)


@given(title=_titles)
@settings(max_examples=300, deadline=None)
def test_parse_title_event_name_bounded(title: str) -> None:
    """The event-name return value of ``_parse_title`` is bounded.

    The event-name segment is at most as long as the original title
    (after stripping the matched prefix block). Without an explicit
    upper bound check at the parser, a planted overlong title would
    inflate downstream dedup-overlap computations.
    """
    _, name = _parse_title(title)
    assert len(name) <= len(title)


@given(title=_titles)
@settings(max_examples=200, deadline=None)
def test_parse_title_lines_subset_of_alphanumeric_uppercase(title: str) -> None:
    """Each extracted line token must match the canonical line-token shape.

    ``_parse_title`` filters extracted tokens through ``_LINE_TOKEN_RE``
    (``^(?:\\d{1,3}[A-Z]?|[A-Z]{1,4}\\d{0,3}[A-Z]?)$``) so the lines
    set never contains arbitrary strings. The property pins that
    contract so a future widening of the prefix-regex doesn't
    accidentally leak non-line tokens into the dedup signal.
    """
    import re
    line_token_re = re.compile(r"^(?:\d{1,3}[A-Z]?|[A-Z]{1,4}\d{0,3}[A-Z]?)$")
    lines, _ = _parse_title(title)
    for line in lines:
        assert line_token_re.fullmatch(line), (
            f"_parse_title returned token {line!r} that doesn't match "
            f"the canonical line-token shape"
        )


# Regression vectors: explicit cases from past bug reports / fixes that
# we want to keep pinned alongside the property tests. Hypothesis would
# eventually find these but seeding them keeps the test deterministic.
_REGRESSION_TITLES = [
    "",
    "   ",
    "17:30 Verspätung",
    "Achtung: Sperre",
    "U1: Sperre wegen Fahrzeug",
    "40+41: Betrieb ab Gersthof",
    "40: 40+41: Stacked title",
    "REX 7: Verspätung",
    "S-Bahn 50: Bauarbeiten",
    "41E/10A: Ersatzbus",
    "Rufbus N20: Hinweis",
    "9, 40, 41: Umleitung",
    "5:",  # empty-body shape used by ``_ensure_line_prefix``
    "U1:U2:U3: stacked u-bahn",
    ":only colon",
    "/multiple/slashes/no/prefix",
    "A" * 600,  # overlong input — exercises the 500-char cap
    "\x00\x01\x02control chars",
]


@given(title=st.sampled_from(_REGRESSION_TITLES))
def test_extract_prefix_lines_handles_regression_vectors(title: str) -> None:
    body, lines = _extract_prefix_lines(title)
    assert isinstance(body, str)
    assert isinstance(lines, list)
    assert len(body) <= 500


@given(title=st.sampled_from(_REGRESSION_TITLES))
def test_parse_title_handles_regression_vectors(title: str) -> None:
    lines, name = _parse_title(title)
    assert isinstance(lines, set)
    assert isinstance(name, str)
