
import os
import responses
from datetime import datetime, timezone

from src.providers import vor

@responses.activate
def test_leak():
    os.environ["VOR_ACCESS_ID"] = "secret_token"
    # Set a valid URL to pass validation
    os.environ["VOR_BASE_URL"] = "https://example.com/"

    vor.refresh_access_credentials()
    vor.refresh_base_configuration()

    # Determine base URL
    base_url = vor.VOR_BASE_URL
    endpoint = f"{base_url}departureBoard"

    responses.add(
        responses.GET,
        endpoint,
        json={},
        status=200
    )

    # We need a timezone aware datetime for the function signature
    now = datetime.now(timezone.utc)

    vor._fetch_departure_board_for_station("12345", now)

    assert len(responses.calls) == 1
    call = responses.calls[0]
    print(f"URL: {call.request.url}")
    print(f"Headers: {call.request.headers}")

    # Requirement: accessId MUST be in URL (as per user request "Fallback-Query-Parameter accessId")
    if "accessId=" not in call.request.url:
        raise AssertionError("accessId NOT FOUND in URL (Feature regression!)")

    # Check Authorization header is still there
    if "Authorization" in call.request.headers:
        assert call.request.headers["Authorization"] == "Bearer secret_token"
    else:
        raise AssertionError("Authorization header MISSING (Auth broken?)")
