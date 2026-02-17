
from src.feed.reporting import RunReport, FeedHealthMetrics, render_feed_health_markdown, DuplicateSummary
import re

def test_markdown_injection_in_table_cells():
    # Setup
    report = RunReport(statuses=[])
    report.register_provider("Malicious[Link]", True, "test")
    report.provider_error("Malicious[Link]", "Error with [Link](http://evil.com)")

    metrics = FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=()
    )

    # Execution
    markdown = render_feed_health_markdown(report, metrics)

    # Assertions
    lines = markdown.splitlines()
    table_row = None
    for line in lines:
        if line.startswith("|") and "Malicious" in line:
            table_row = line
            break

    assert table_row is not None, "Could not find table row for Malicious provider"

    print(f"Table Row: {table_row}")

    # Check that provider name is escaped in the table row
    # Should be: | Malicious\[Link\] | ...
    assert r"Malicious\[Link\]" in table_row, "Provider name should be escaped in table row"

    # Check that detail is escaped in the table row
    # Should be: ... | Error with \[Link\]\(http://evil.com\) |
    assert r"Error with \[Link\]\(http://evil.com\)" in table_row, "Provider detail should be escaped in table row"
