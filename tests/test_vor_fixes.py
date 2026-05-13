from unittest.mock import MagicMock, patch
import requests
from src.providers.vor import (
    VorAuth,
    apply_authentication,
)

class TestVorAuth:
    def test_vor_auth_init_and_call(self) -> None:
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
    def test_apply_authentication(
        self,
        mock_refresh: MagicMock,
        mock_vor_auth_cls: MagicMock,
    ) -> None:
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

