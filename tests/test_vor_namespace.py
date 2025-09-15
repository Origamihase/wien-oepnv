import xml.etree.ElementTree as ET

import src.providers.vor as vor


def test_iter_messages_handles_namespace():
    xml = (
        "<ns:Root xmlns:ns='urn:test'>"
        "<ns:Messages>"
        "<ns:Message id='1' act='true'/>"
        "</ns:Messages>"
        "</ns:Root>"
    )
    root = ET.fromstring(xml)
    vor._strip_ns(root)
    messages = list(vor._iter_messages(root))
    assert len(messages) == 1
