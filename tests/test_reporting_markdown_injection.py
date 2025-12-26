
from src.feed.reporting import RunReport, FeedHealthMetrics, render_feed_health_markdown, DuplicateSummary

def test_markdown_injection():
    # Setup
    report = RunReport(statuses=[("test_provider", True)])
    report.provider_error("test_provider", "Error | with pipe")

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
    # 1. Verify pipe is escaped in table cell
    assert r"Error \| with pipe" in markdown, "Pipe character should be escaped in table cell"

    # 2. Verify backtick is replaced in duplicates list
    assert "Title 'with backtick'" in markdown, "Backticks should be replaced with single quotes in duplicates list"

    # 3. Verify general structure
    assert "| test_provider | error |" in markdown
