import requests
import responses

import src.providers.vor as vor


@responses.activate
def test_location_name_contains_stoplocation():
    url = f"{vor.VOR_BASE}/{vor.VOR_VERSION}/location.name"
    payload = {"StopLocation": [{"id": "1", "name": "Wien"}]}
    responses.add(responses.GET, url, json=payload, status=200)

    resp = requests.get(url)
    data = resp.json()

    assert isinstance(data.get("StopLocation"), list)
    assert len(data["StopLocation"]) >= 1
