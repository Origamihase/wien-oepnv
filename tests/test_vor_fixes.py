from unittest.mock import MagicMock, patch, ANY, call
import requests
from datetime import datetime
from src.providers.vor import (
    VorAuth,
    apply_authentication,
    _fetch_departure_board_for_station,
)

class TestVorAuth:
    def test_vor_auth_init_and_call(self):
        """
        Test that VorAuth:
        1. Accepts access_id, auth_header, base_url.
        2. Injects Authorization header if missing.
        3. Injects accessId query param if missing.
        4. Checks base_url scope.
        """
        access_id = "test_id"
        auth_header = "Bearer test_token"
        base_url = "https://example.com/api/"

        auth = VorAuth(access_id=access_id, auth_header=auth_header, base_url=base_url)

        # Case 1: Matching Base URL, missing header, missing query param
        req = requests.PreparedRequest()
        req.url = "https://example.com/api/endpoint"
        req.headers = {}

        req = auth(req)

        assert req.headers.get("Authorization") == auth_header
        assert "accessId=test_id" in req.url

        # Case 2: Non-matching Base URL
        req = requests.PreparedRequest()
        req.url = "https://other.com/api/endpoint"
        req.headers = {}

        req = auth(req)

        assert "Authorization" not in req.headers
        assert "accessId" not in req.url

        # Case 3: Header already present
        req = requests.PreparedRequest()
        req.url = "https://example.com/api/endpoint"
        req.headers = {"Authorization": "Existing"}

        req = auth(req)

        assert req.headers["Authorization"] == "Existing"
        # Ensure accessId is still injected if missing
        assert "accessId=test_id" in req.url

        # Case 4: Query param already present
        req = requests.PreparedRequest()
        req.url = "https://example.com/api/endpoint?accessId=existing"
        req.headers = {}

        req = auth(req)

        assert req.headers.get("Authorization") == auth_header
        assert req.url == "https://example.com/api/endpoint?accessId=existing"

class TestApplyAuthentication:
    @patch("src.providers.vor.VorAuth")
    @patch("src.providers.vor._VOR_AUTHORIZATION_HEADER", "Bearer global_token")
    @patch("src.providers.vor.VOR_ACCESS_ID", "global_id")
    @patch("src.providers.vor.VOR_BASE_URL", "https://global.com/")
    @patch("src.providers.vor.refresh_access_credentials")
    def test_apply_authentication(self, mock_refresh, mock_vor_auth_cls):
        """
        Test that apply_authentication:
        1. Does NOT set Authorization in session.headers.
        2. Sets session.auth to VorAuth instance.
        """
        session = requests.Session()
        # Ensure clean state
        if "Authorization" in session.headers:
            del session.headers["Authorization"]

        apply_authentication(session)

        # Verification 1: Header NOT in session.headers
        assert "Authorization" not in session.headers

        # Verification 2: session.auth is set correctly
        assert session.auth is not None
        # It should be the return value of VorAuth(...)
        assert session.auth == mock_vor_auth_cls.return_value

        mock_vor_auth_cls.assert_called_with(
            "global_id",
            "Bearer global_token",
            "https://global.com/"
        )

class TestFetchDepartureBoard:
    @patch("src.providers.vor.fetch_content_safe")
    @patch("src.providers.vor.save_request_count")
    @patch("src.providers.vor.load_request_count")
    @patch("src.providers.vor._QUOTA_LOCK")
    def test_fetch_departure_board_calls(self, mock_lock, mock_load, mock_save, mock_fetch):
        """
        Test _fetch_departure_board_for_station:
        1. Calls save_request_count exactly once.
        2. Calls save_request_count BEFORE fetch_content_safe.
        3. Calls fetch_content_safe exactly once (no loop).
        """
        # Setup mocks
        mock_load.return_value = (None, 0) # Not limit reached
        mock_fetch.return_value = '{"test": "data"}'

        # Track order of calls
        manager = MagicMock()
        manager.attach_mock(mock_save, 'save_request_count')
        manager.attach_mock(mock_fetch, 'fetch_content_safe')

        now = datetime.now()

        _fetch_departure_board_for_station("station_id", now)

        # Check call counts
        assert mock_save.call_count == 1
        assert mock_fetch.call_count == 1

        # Check order
        # We expect save_request_count to be called before fetch_content_safe
        expected_calls = [
            call.save_request_count(now),
            call.fetch_content_safe(ANY, ANY, params=ANY, timeout=ANY, allowed_content_types=ANY)
        ]

        manager.assert_has_calls(expected_calls)
