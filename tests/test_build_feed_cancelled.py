import time
from unittest.mock import MagicMock, patch
import src.build_feed as bf
from src.build_feed import _collect_items, RunReport

def test_collect_items_cancelled_future():
    # Setup configuration
    mock_feed_config = MagicMock()
    mock_feed_config.PROVIDER_TIMEOUT = 0.05  # very short timeout
    mock_feed_config.PROVIDER_MAX_WORKERS = 1
    mock_feed_config.MAX_WORKERS = 1
    mock_feed_config.get_bool_env.return_value = True

    # Define a slow network fetch function
    def mock_fetch(timeout=None):
        time.sleep(0.2)  # sleep longer than timeout
        return []

    mock_fetch.__name__ = "dummy_provider"

    # Needs to be treated as a network fetcher.
    # Having no `_provider_cache_name` does this naturally.

    report = MagicMock(spec=RunReport)

    with patch.object(bf, "feed_config", mock_feed_config), \
         patch.object(bf, "PROVIDERS", [("DUMMY_ENABLE", mock_fetch)]), \
         patch.object(bf, "DEFAULT_PROVIDERS", ("DUMMY_ENABLE",)), \
         patch("src.build_feed.iter_providers") as mock_iter:

        from src.feed.providers import ProviderSpec

        # It's important NOT to provide `cache_key="dummy"` on the loader because that makes it a cache fetcher
        # Network fetchers have `_provider_cache_name` as None.

        # Override the loader explicitly so it acts as a network fetcher
        if hasattr(mock_fetch, "_provider_cache_name"):
            delattr(mock_fetch, "_provider_cache_name")

        mock_iter.return_value = [ProviderSpec(env_var="DUMMY_ENABLE", loader=mock_fetch, cache_key="")]
        bf._PROVIDERS_INITIALIZED = False

        # For mock_fetch to NOT be considered a cache fetcher, it shouldn't have `_provider_cache_name` at all.
        # However, `init_providers()` actually SETS it if we run it!
        # So we need to ensure that `init_providers()` doesn't turn it into a cache fetcher,
        # OR we just let `_PROVIDERS_INITIALIZED` stay True to skip `init_providers`!
        bf._PROVIDERS_INITIALIZED = True

        # Run _collect_items. The fetch should time out and the future will be cancelled.
        # Then, if the wait() returns a cancelled future, it won't be logged as "Fetch abgebrochen".
        _collect_items(report=report)

        # Find all calls to provider_error
        error_calls = [call.args[1] for call in report.provider_error.call_args_list]

        # It should have the timeout error
        assert any("Timeout nach" in arg for arg in error_calls), f"Expected timeout error, got: {error_calls}"

        # It should NOT have the "Fetch abgebrochen" error
        assert not any(
            "Fetch abgebrochen" in arg for arg in error_calls
        ), f"Found 'Fetch abgebrochen' in error calls: {error_calls}"
