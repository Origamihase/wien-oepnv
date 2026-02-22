from src.providers.oebb import _clean_description
from src.utils.text import html_to_text

def test_clean_description_arrow_brackets():
    # Input simulating what comes from _get_text (unescaped)
    # Case 1: Brackets around arrow
    desc = "Wien < ↔ > Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

    # Case 2: Brackets with spaces
    desc = "Wien  < ↔ >  Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

    # Case 3: Mixed entities (simulated)
    # If the input to _clean_description is already unescaped by ElementTree
    desc = "Wien < ↔ > Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

    # Let's test what happens if we use html_to_text afterwards
    final = html_to_text(cleaned)
    assert final == "Wien ↔ Salzburg"

def test_reproduce_issue_if_input_is_different():
    # Maybe the input is actually like this?
    desc = "Wien &lt; ↔ &gt; Salzburg"
    # _clean_description should handle entities in the regex
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

    # Maybe the arrow is not ↔?
    desc = "Wien <-> Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

    desc = "Wien <=> Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

def test_html_tag_arrow():
    # What if it's like a tag?
    desc = "Wien <-> Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"

def test_double_escaped_entities():
    # This currently FAILS with the existing regex
    desc = "Wien &amp;lt; ↔ &amp;gt; Salzburg"
    cleaned = _clean_description(desc)
    assert cleaned == "Wien ↔ Salzburg"
