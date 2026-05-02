def format_robots(content: str, sitemap_url: str) -> str:
    """Strips leading whitespaces per line and replaces all existing Sitemap: directives
    with exactly one normalized line at the end.
    """
    lines = content.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.lstrip()
        if not stripped.lower().startswith("sitemap:"):
            cleaned_lines.append(stripped)

    # Append the canonical sitemap line at the end
    cleaned_lines.append(f"Sitemap: {sitemap_url.rstrip('/')}/sitemap.xml")

    return "\n".join(cleaned_lines) + "\n"
