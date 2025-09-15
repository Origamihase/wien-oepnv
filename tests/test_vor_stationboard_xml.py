from defusedxml import ElementTree as ET

import src.providers.vor as vor


def test_iter_messages_returns_element():
    xml = """
    <DepartureBoard>
        <Messages>
            <Message id="1" />
        </Messages>
    </DepartureBoard>
    """
    root = ET.fromstring(xml)
    messages = list(vor._iter_messages(root))
    assert len(messages) >= 1
    assert messages[0].get("id") == "1"
