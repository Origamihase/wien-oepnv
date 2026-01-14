
import xml.etree.ElementTree as ET

def test_et_generation():
    NS_EXT = "https://wien-oepnv.example/schema"
    NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"

    ET.register_namespace('ext', NS_EXT)
    ET.register_namespace('content', NS_CONTENT)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    title = ET.SubElement(channel, "title")
    title.text = "Test Feed & More"

    item = ET.SubElement(channel, "item")

    # Custom namespace element
    starts_at = ET.SubElement(item, f"{{{NS_EXT}}}starts_at")
    starts_at.text = "2023-01-01T12:00:00Z"

    # Content encoded
    content = ET.SubElement(item, f"{{{NS_CONTENT}}}encoded")
    content.text = "<p>Some HTML content with <br/> tags.</p>"

    # GUID with attribute
    guid = ET.SubElement(item, "guid")
    guid.text = "unique-id-123"
    guid.set("isPermaLink", "false")

    # Generate bytes
    xml_bytes = ET.tostring(rss, encoding='utf-8', xml_declaration=True)
    print(xml_bytes.decode('utf-8'))

if __name__ == "__main__":
    test_et_generation()
