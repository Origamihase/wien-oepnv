
import pytest
from unittest.mock import patch, MagicMock
from src.providers.vor import fetch_vor_disruptions, VOR_MAX_WORKERS

@patch("src.providers.vor.as_completed", return_value=[]) # Mock as_completed to return empty list (no results to process)
@patch("src.providers.vor.ThreadPoolExecutor")
@patch("src.providers.vor.refresh_access_credentials", return_value="dummy_token")
@patch("src.providers.vor.load_request_count", return_value=("2025-01-01", 0))
@patch("src.providers.vor.get_configured_stations", return_value=["id_" + str(i) for i in range(50)])
@patch("src.providers.vor.select_stations_for_run", side_effect=lambda x: x) # Return all
@patch("src.providers.vor.session_with_retries")
def test_vor_concurrency_limit(mock_session, mock_select, mock_get_stations, mock_load, mock_refresh, mock_executor, mock_as_completed):
    """Verify that VOR fetch limits the number of threads even with many stations."""

    # Mock context manager for executor
    mock_executor_instance = MagicMock()
    mock_executor.return_value.__enter__.return_value = mock_executor_instance

    # Run fetch with 50 stations
    # It will raise RequestException because successes == 0, but we catch it or expect it
    try:
        fetch_vor_disruptions()
    except Exception:
        pass # We only care about executor init

    # Verify ThreadPoolExecutor was initialized with capped workers
    mock_executor.assert_called_once()
    _, kwargs = mock_executor.call_args
    assert kwargs['max_workers'] == VOR_MAX_WORKERS
    assert VOR_MAX_WORKERS == 10

@patch("src.providers.vor.as_completed", return_value=[])
@patch("src.providers.vor.ThreadPoolExecutor")
@patch("src.providers.vor.refresh_access_credentials", return_value="dummy_token")
@patch("src.providers.vor.load_request_count", return_value=("2025-01-01", 0))
@patch("src.providers.vor.get_configured_stations", return_value=["id_1", "id_2"])
@patch("src.providers.vor.select_stations_for_run", side_effect=lambda x: x)
@patch("src.providers.vor.session_with_retries")
def test_vor_concurrency_small_list(mock_session, mock_select, mock_get_stations, mock_load, mock_refresh, mock_executor, mock_as_completed):
    """Verify that VOR fetch uses fewer threads for small station lists."""

    mock_executor_instance = MagicMock()
    mock_executor.return_value.__enter__.return_value = mock_executor_instance

    try:
        fetch_vor_disruptions()
    except Exception:
        pass

    mock_executor.assert_called_once()
    _, kwargs = mock_executor.call_args
    assert kwargs['max_workers'] == 2
