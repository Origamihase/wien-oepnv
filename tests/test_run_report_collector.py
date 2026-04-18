import logging
from src.feed.reporting import RunReport

def test_run_error_collector_emits_error():
    report = RunReport([])
    report.attach_error_collector()

    logger = logging.getLogger("test_collector")
    logger.error("Test error message")

    assert report.has_errors()
    errors = list(report.iter_error_messages())
    assert any("Test error message" in msg for msg in errors)

    report.detach_error_collector()
