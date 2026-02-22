
from unittest.mock import patch

def test_thread_pool_cleanup():
    # Import build_feed here to ensure we get the current module from sys.modules
    # This guards against other tests (like test_collect_items_timeout) reloading the module
    from src import build_feed

    # Use a callable object to strictly control attributes
    class MockLoader:
        def __call__(self, timeout=None):
            return []

    mock_loader = MockLoader()
    # Verify it doesn't have the cache attribute
    assert getattr(mock_loader, "_provider_cache_name", None) is None

    # Patch PROVIDERS list in build_feed
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
