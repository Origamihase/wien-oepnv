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


def test_run_error_collector_no_op_when_detached():
    report = RunReport([])
    report.attach_error_collector()

    logger = logging.getLogger("test_collector_detached")

    # Detach immediately
    report.detach_error_collector()

    # Emit an error
    logger.error("Test error message after detach")

    # The error should not be captured
    assert not report.has_errors()
    errors = list(report.iter_error_messages())
    assert not any("Test error message after detach" in msg for msg in errors)


def test_run_report_log_results_concurrent_submission(monkeypatch):
    import threading
    import time
    from src.feed import reporting

    report = RunReport([])
    report.add_error_message("Test Error")

    submission_count = 0

    def mock_submit_github_issue(rep):
        nonlocal submission_count
        # Simulate network delay to encourage race conditions
        time.sleep(0.05)
        submission_count += 1

    monkeypatch.setattr(reporting, "_submit_github_issue", mock_submit_github_issue)

    threads = []
    for _ in range(5):
        t = threading.Thread(target=report.log_results)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert submission_count == 1
    assert report._issue_submitted is True
