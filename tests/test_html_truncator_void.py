from src.utils.text import truncate_html

def test_truncate_html_void_elements():
    """Verify that void elements like <br> are not closed."""
    # <br> should stay <br> and NOT become <br></br>
    html = "Line 1<br>Line 2"
    result = truncate_html(html, 100)
    assert result == "Line 1<br>Line 2"
    assert "</br>" not in result

def test_truncate_html_img_tag():
    """Verify that <img> tags are not closed."""
    html = '<img src="test.jpg">Caption'
    result = truncate_html(html, 100)
    assert result == '<img src="test.jpg">Caption'
    assert "</img>" not in result

def test_truncate_html_nested_void():
    """Verify void elements inside other tags."""
    html = "<div><hr></div>"
    result = truncate_html(html, 100)
    assert result == "<div><hr></div>"
    assert "</hr>" not in result

def test_truncate_html_ignores_case():
    """Verify case insensitivity for void elements."""
    html = "Line 1<BR>Line 2"
    result = truncate_html(html, 100)
    assert "Line 1<BR>Line 2" in result
    assert "</BR>" not in result
    assert "</br>" not in result
