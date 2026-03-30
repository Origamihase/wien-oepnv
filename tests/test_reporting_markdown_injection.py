
from src.feed.reporting import RunReport, FeedHealthMetrics, render_feed_health_markdown, DuplicateSummary

def test_markdown_injection():
    # Setup
    report = RunReport(statuses=[("test_provider", True), ("Malicious|Provider", True)])
    # Inject various markdown characters
    report.provider_error("test_provider", "Error | with pipe")
    report.provider_success("Malicious|Provider", items=0)
    report.add_error_message("Bad [link](http://evil.com)")
    report.add_error_message("Bold **text**")

    metrics = FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=1,
        duplicates=(DuplicateSummary(dedupe_key="key", count=1, titles=("Title `with backtick`",)),)
    )

    # Execution
    markdown = render_feed_health_markdown(report, metrics)

    # Assertions
    # 1. Verify pipe is escaped in table cell (already works)
    assert r"Error \| with pipe" in markdown, "Pipe character should be escaped in table cell"

    # 1b. Verify pipe is escaped in provider name
    assert r"Malicious\|Provider" in markdown, "Pipe character should be escaped in provider name"

    # 2. Verify backtick is replaced in duplicates list (already works)
    assert "Title 'with backtick'" in markdown, "Backticks should be replaced with single quotes in duplicates list"

    # 3. Verify Markdown control characters are escaped in error messages
    # We now expect backslash escaping for parens too
    assert r"Bad \[link\]\(http://evil.com\)" in markdown, "Markdown links should be escaped"
    assert r"Bold \*\*text\*\*" in markdown, "Markdown bold should be escaped"
