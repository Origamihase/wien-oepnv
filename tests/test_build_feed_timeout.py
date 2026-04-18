from unittest.mock import MagicMock, patch
import src.build_feed as bf
from src.build_feed import _collect_items, RunReport

def test_collect_items_timeout_zero():
    # Mock configuration
    mock_feed_config = MagicMock()
    mock_feed_config.PROVIDER_TIMEOUT = 0
    mock_feed_config.PROVIDER_MAX_WORKERS = 1
    mock_feed_config.MAX_WORKERS = 1
    mock_feed_config.get_bool_env.return_value = True

    # Create a mock network fetch function
    def mock_fetch(timeout=None):
        return []

    mock_fetch.__name__ = "dummy_provider"

    report = MagicMock(spec=RunReport)

    # Patch modules and configuration
    with patch.object(bf, "feed_config", mock_feed_config), \
         patch.object(bf, "PROVIDERS", [("DUMMY_ENABLE", mock_fetch)]), \
         patch.object(bf, "DEFAULT_PROVIDERS", ("DUMMY_ENABLE",)), \
         patch("src.build_feed.ThreadPoolExecutor") as mock_executor_cls:

        mock_executor = mock_executor_cls.return_value.__enter__.return_value

        # Run _collect_items
        items = _collect_items(report=report)

        # The provider should be skipped entirely
        assert len(items) == 0

        # Assert that provider_error was called with "Timeout nach 0s"
        report.provider_error.assert_called()
        args, _ = report.provider_error.call_args
        assert "Timeout nach 0s" in args[1]

        # Assert that _run_fetch is never submitted to the executor
        mock_executor.submit.assert_not_called()
