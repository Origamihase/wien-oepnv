import xml.etree.ElementTree as ET  # nosec B405
def apply_atom_links(feed_xml: str, site_base: str) -> str:
    """Injects or replaces Atom links and <language> tag in the RSS feed.
    Uses ElementTree to safely parse and reconstruct the headers.
    """
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")

    try:
        root = ET.fromstring(feed_xml)  # noqa: S314 # nosec B314
    except ET.ParseError:
        return feed_xml

    if root.tag != "rss":
        return feed_xml

    channel = root.find("channel")
    if channel is None:
        return feed_xml

    # Remove existing <atom:link> tags
    atom_links = channel.findall("{http://www.w3.org/2005/Atom}link")
    for link in atom_links:
        channel.remove(link)

    # Remove existing <language> tag to reinsert it at the correct position
    lang_tag = channel.find("language")
    if lang_tag is not None:
        channel.remove(lang_tag)

    # Find position to insert (after <description>)
    insert_pos = 0
    for i, child in enumerate(list(channel)):
        insert_pos = i + 1
        if child.tag == "description":
            break

    # Create new elements
    base_url = site_base.rstrip('/')

    alt_link = ET.Element("{http://www.w3.org/2005/Atom}link")
    alt_link.set("rel", "alternate")
    alt_link.set("type", "text/html")
    alt_link.set("href", f"{base_url}/")

    self_link = ET.Element("{http://www.w3.org/2005/Atom}link")
    self_link.set("rel", "self")
    self_link.set("type", "application/rss+xml")
    self_link.set("href", f"{base_url}/feed.xml")

    lang_elem = ET.Element("language")
    lang_elem.text = "de"

    # Insert in correct order
    channel.insert(insert_pos, alt_link)
    channel.insert(insert_pos + 1, self_link)
    channel.insert(insert_pos + 2, lang_elem)

    if hasattr(ET, 'indent'):
        ET.indent(root, space="    ", level=0)

    result_bytes: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    result_str: str = result_bytes.decode("utf-8")

    return result_str + "\n"
