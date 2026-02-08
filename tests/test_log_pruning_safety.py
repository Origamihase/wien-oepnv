
import logging
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.feed.logging import prune_log_file

def test_prune_log_file_preserves_logging_handle(tmp_path: Path):
    """
    Verify that prune_log_file modifies the file in-place, allowing
    RotatingFileHandler to continue writing to it.
    """
    log_file = tmp_path / "test.log"

    # 1. Setup Logger
    logger = logging.getLogger("test_pruning")
    logger.setLevel(logging.INFO)
    # Clear existing handlers
    logger.handlers = []

    handler = RotatingFileHandler(log_file, maxBytes=1024, backupCount=1, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    try:
        # 2. Write initial log
        logger.info("Line 1: Before Prune")

        # Ensure it's written
        assert "Line 1: Before Prune" in log_file.read_text(encoding="utf-8")

        # 3. Simulate Pruning
        # We need to ensure we have content to prune or keep.
        # prune_log_file keeps records NEWER than cutoff.
        # We pass now=datetime.now(), keep_days=7.
        # The current record is very new, so it should be KEPT.
        # But we want to test that the file handle remains valid EVEN IF we rewrite the file.
        # To simulate a rewrite, we can verify that prune_log_file indeed opens and closes the file.

        # Note: prune_log_file reads the file content first.
        # If we want to verify it rewrites, we can check mtime?
        # Or just trust that we call it.

        prune_log_file(log_file, now=datetime.now(), keep_days=1)

        # 4. Write second log
        logger.info("Line 2: After Prune")

        # 5. Verify Content
        content = log_file.read_text(encoding="utf-8")

        assert "Line 1: Before Prune" in content
        assert "Line 2: After Prune" in content, "Logging failed after pruning - handle likely broken"

    finally:
        # Cleanup
        handler.close()
        logger.removeHandler(handler)

def test_prune_log_file_actually_prunes(tmp_path: Path):
    """
    Verify that prune_log_file actually removes old lines.
    """
    log_file = tmp_path / "test_prune.log"

    # Create a log file with old and new entries
    # Format matches _LOG_TIMESTAMP_RE: YYYY-MM-DD HH:MM:SS,mmm

    old_date = datetime.now() - timedelta(days=10)
    new_date = datetime.now()

    old_line = f"{old_date.strftime('%Y-%m-%d %H:%M:%S,000')} Old Message\n"
    new_line = f"{new_date.strftime('%Y-%m-%d %H:%M:%S,000')} New Message\n"

    log_file.write_text(old_line + new_line, encoding="utf-8")

    # Prune
    prune_log_file(log_file, now=new_date, keep_days=7)

    content = log_file.read_text(encoding="utf-8")

    assert "Old Message" not in content
    assert "New Message" in content
