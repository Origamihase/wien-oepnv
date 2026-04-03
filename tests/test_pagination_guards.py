from src.places.client import GooglePlacesClient, GooglePlacesConfig
from src.places.tiling import Tile
import requests
from unittest.mock import MagicMock

def test_infinite_pagination_guard():
    config = GooglePlacesConfig(
        api_key="TEST_KEY",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5,
        max_retries=1
    )

    mock_session = MagicMock(spec=requests.Session)

    class InfiniteResponse:
        status_code = 200
        headers = {}
        _content_consumed = True
        _content = (
            b'{"places": [{"id": "1", "displayName": {"text": "A"}, "location": '
            b'{"latitude": 48.0, "longitude": 16.0}, "types": ["train_station"]}], '
            b'"nextPageToken": "INFINITE_TOKEN"}'
        )

        class RawMock:
            pass
        raw = RawMock()
        raw._connection = MagicMock()
        raw._connection.__class__.__name__ = "MockConnection"

        def json(self):
            import json
            return json.loads(self._content.decode())

        def iter_content(self, chunk_size):
            yield self._content

        def close(self):
            pass

    mock_session.post.return_value.__enter__.return_value = InfiniteResponse()

    client = GooglePlacesClient(config=config, session=mock_session)
    tile = Tile(latitude=48.2, longitude=16.3)

    # Iterate through places, the guard should stop at MAX_PAGES (50)
    # The payload yields 1 place per page.
    places = list(client._iter_tile(tile))

    assert len(places) == 50
    assert mock_session.post.call_count == 50
