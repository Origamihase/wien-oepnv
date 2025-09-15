import xml.etree.ElementTree as ET

import src.providers.vor as vor


def test_title_has_line_prefix():
    xml = (
        "<Root><Messages>"
        "<Message id='1' act='true' head='Baustelle …'>"
        "<products><Product catOutS='S' displayNumber='1'/></products>"
        "</Message></Messages></Root>"
    )
    root = ET.fromstring(xml)
    items = vor._collect_from_board("123", root)
    assert len(items) == 1
    assert items[0]["title"] == "S1: Baustelle …"
