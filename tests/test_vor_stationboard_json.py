import src.providers.vor as vor


def test_iter_messages_returns_dict():
    payload = {
        "DepartureBoard": {
            "Messages": {
                "Message": {"id": "1"},
            }
        }
    }
    messages = list(vor._iter_messages(payload))
    assert len(messages) >= 1
    assert messages[0]["id"] == "1"
