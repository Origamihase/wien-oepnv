import src.providers.vor as vor


def test_iter_messages_handles_nested_containers() -> None:
    payload = {
        "DepartureBoard": {
            "Messages": {
                "message": [{"id": "1", "act": "true"}],
            }
        }
    }
    messages = list(vor._iter_messages(payload))
    assert len(messages) == 1
