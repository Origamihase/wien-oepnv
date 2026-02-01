
import xml.etree.ElementTree as ET

def test_reparse():
    NS_EXT = "https://wien-oepnv.example/schema"
    ET.register_namespace('ext', NS_EXT)

    # Simulate _emit_item
    item = ET.Element("item")
    ext_elem = ET.SubElement(item, f"{{{NS_EXT}}}starts_at")
    ext_elem.text = "2023-01-01"

    # Serialize item
    xml_str = ET.tostring(item, encoding='unicode')
    print(f"Item XML:\n{xml_str}")

    # Simulate _make_rss
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    # Parse back and append
    parsed_item = ET.fromstring(xml_str)
    channel.append(parsed_item)

    # Serialize full feed
    full_xml = ET.tostring(rss, encoding='unicode')
    print(f"\nFull XML:\n{full_xml}")

if __name__ == "__main__":
    test_reparse()
