import re
import pathlib
from src.utils.files import atomic_write

def rewrite_canonicals(sitemap_xml: str, site_base: str) -> str:
    """Reproduces the 8 sed patterns from the legacy workflow."""
    base = site_base.rstrip('/')

    replacements = [
        (r'<loc>https://wien-oepnv\.github\.io/?</loc>', f'<loc>{base}/</loc>'),
        (r'<loc>https://origamihase\.github\.io/wien-oepnv/?</loc>', f'<loc>{base}/</loc>'),
        (r'<loc>https://wien-oepnv\.github\.io/feed\.xml</loc>', f'<loc>{base}/feed.xml</loc>'),
        (r'<loc>https://origamihase\.github\.io/wien-oepnv/feed\.xml</loc>', f'<loc>{base}/feed.xml</loc>'),
        (r'<loc>https://wien-oepnv\.github\.io/docs/how-to/?</loc>', f'<loc>{base}/docs/how-to/</loc>'),
        (r'<loc>https://origamihase\.github\.io/wien-oepnv/docs/how-to/?</loc>', f'<loc>{base}/docs/how-to/</loc>'),
        (r'<loc>https://wien-oepnv\.github\.io/docs/reference/?</loc>', f'<loc>{base}/docs/reference/</loc>'),
        (r'<loc>https://origamihase\.github\.io/wien-oepnv/docs/reference/?</loc>', f'<loc>{base}/docs/reference/</loc>'),
    ]

    result = sitemap_xml
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result)

    return result

def apply_to_path(path: pathlib.Path, site_base: str) -> bool:
    """Applies rewrite_canonicals to a file if it exists."""
    if not path.exists() or not path.is_file():
        return False

    content = path.read_text(encoding="utf-8")
    new_content = rewrite_canonicals(content, site_base)

    if content != new_content:
        atomic_write(path, new_content)

    return True
