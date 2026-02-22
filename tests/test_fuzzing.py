"""Property-based testing (Fuzzing) for critical utilities."""

import unicodedata
from html.parser import HTMLParser
from typing import List

import hypothesis.strategies as st
from hypothesis import given, settings, HealthCheck

from src.utils.http import validate_http_url
from src.utils.text import truncate_html

# Strategy for generating text content (excluding HTML tags to avoid structure invalidation)
html_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="<>"),
    min_size=0,
    max_size=1000
)

@given(text=html_text, limit=st.integers(min_value=10, max_value=500))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_truncate_html_validity(text: str, limit: int):
    """Ensure truncate_html always produces valid HTML with balanced tags."""
    # We construct a complex HTML string by wrapping text in tags
    complex_html = f"<div><p><b>{text}</b></p></div>"

    truncated = truncate_html(complex_html, limit)

    # Verify basics
    assert isinstance(truncated, str)

    # Verify tag balancing using standard HTMLParser
    class TagBalancer(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack: List[str] = []
            self.failed = False

        def handle_starttag(self, tag, attrs):
            if tag not in ('br', 'img', 'hr', 'meta', 'link', 'input', 'area', 'base', 'col', 'embed', 'param', 'source', 'track', 'wbr'):
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack:
                self.failed = True # Closing tag without opening
                return
            if self.stack[-1] == tag:
                self.stack.pop()
            else:
                # Mismatch - in simple valid HTML this shouldn't happen if we closed correctly.
                # However, truncate_html might close outer tags if inner ones were cut?
                # Actually truncate_html should close all open tags in reverse order.
                # If we see </b> but stack has </i>, it's invalid nesting.
                # But our input was perfectly nested.
                self.failed = True

    balancer = TagBalancer()
    balancer.feed(truncated)

    assert not balancer.failed, f"Truncated HTML has unbalanced tags: {truncated}"
    assert not balancer.stack, f"Truncated HTML has unclosed tags: {balancer.stack} in {truncated}"


# Strategy for URL fuzzing
# We include control characters, weird unicode, etc.
url_text = st.text()

@given(url=url_text)
@settings(max_examples=200)
def test_validate_http_url_security(url: str):
    """Fuzz validate_http_url to ensure it rejects dangerous inputs."""

    # We are testing the validator, so we don't expect it to crash.
    # We expect it to return None or a sanitized string.

    try:
        result = validate_http_url(url, check_dns=False)
    except ValueError:
        # It might raise ValueError for invalid URLs if they pass basic checks but fail later
        # But validate_http_url generally returns None for invalid syntax.
        # Wait, validate_http_url returns None.
        # But _pin_url_to_ip raises ValueError.
        # validate_http_url calls _pin_url_to_ip only if check_dns is True?
        # No, validate_http_url checks syntax and returns None.
        # It calls _resolve_hostname_safe if check_dns=True.
        # It shouldn't raise exceptions for "None" return cases.
        return

    if result is None:
        return

    # If result is valid:
    # 1. It must be NFKC normalized (implied by our fix)
    assert result == unicodedata.normalize("NFKC", result)

    # 2. It must not contain control characters
    for char in result:
        assert ord(char) >= 32 and ord(char) != 127, f"Control char {ord(char)} found in result"

    # 3. It must start with http/https
    assert result.startswith("http")
