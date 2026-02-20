
import pytest
from unittest.mock import MagicMock, patch
from src import build_feed

def test_thread_pool_cleanup():
    # Mock providers to return empty list so we don't do real work
    # We need network fetchers to trigger executor creation

    # Mock a provider loader that has no _provider_cache_name (so it is network)
    mock_loader = MagicMock(return_value=[])
    del mock_loader._provider_cache_name # ensure it's treated as network

    # Patch PROVIDERS list in build_feed
    # Note: We need to patch it on the module object that _collect_items uses
    with patch.object(build_feed, "PROVIDERS", [("TEST_ENV", mock_loader)]):
        with patch("src.build_feed.feed_config.get_bool_env", return_value=True):
            # Mock ThreadPoolExecutor
            with patch("src.build_feed.ThreadPoolExecutor") as MockExecutor:
                mock_instance = MockExecutor.return_value
                mock_instance.__enter__.return_value = mock_instance

                # Mock wait to return immediately
                with patch("src.build_feed.wait", return_value=(set(), set())):
                    build_feed._collect_items()

                # Check if executor was created
                assert MockExecutor.called, "ThreadPoolExecutor was not instantiated"

                # Check if context manager was used
                assert mock_instance.__enter__.called, "__enter__ was not called"
                assert mock_instance.__exit__.called, "__exit__ was not called"
