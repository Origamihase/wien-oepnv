"""Sentinel guard for the SEO-critical ``<head>`` links in ``docs/site.html``.

A Lighthouse audit nulled the whole SEO category because the English-feed
``<link rel="alternate">`` carried ``hreflang="en"`` on a *relative* href
(``feed.en.xml``). Lighthouse's hreflang audit rejects that with the reason
"Relative href value" -- a relative hreflang href is invalid, so the audit
scores 0 and the category cannot resolve to a passing score.

The dashboard ships a single, JS-localised page: ``site.html`` serves both
German and English from the *same* URL (the language toggle rewrites the DOM
in place), so there is no distinct per-language URL for hreflang to point at
and the page therefore carries no hreflang alternates at all. If one is ever
(re)introduced it MUST be fully qualified. This guard encodes exactly that
rule -- plus an absolute, single canonical -- so the SEO regression that
slipped past the ``seo-guard`` workflow (which only validates the generated
sitemap/llms/feed/robots artefacts, never ``site.html``'s head) can never
ship silently again.
"""

from __future__ import annotations

import re
from pathlib import Path

SITE_HTML = Path(__file__).resolve().parents[1] / "docs" / "site.html"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z][\w:-]*)\s*=\s*"([^"]*)"')


def _link_tags() -> list[dict[str, str]]:
    # Strip comments first so prose inside the head's explanatory comment
    # blocks can never be mistaken for a real <link> tag.
    html = _COMMENT_RE.sub("", SITE_HTML.read_text(encoding="utf-8"))
    tags: list[dict[str, str]] = []
    for raw in _LINK_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(raw)}
        attrs["__raw__"] = raw
        tags.append(attrs)
    return tags


def _is_absolute(url: str) -> bool:
    return url.startswith("https://") or url.startswith("http://")


def test_every_hreflang_link_uses_an_absolute_href() -> None:
    offenders = [
        tag["__raw__"]
        for tag in _link_tags()
        if "hreflang" in tag and not _is_absolute(tag.get("href", ""))
    ]
    assert not offenders, (
        "docs/site.html: every <link hreflang=...> must use a fully-qualified "
        "(absolute) href. Lighthouse rejects a relative hreflang href with "
        '"Relative href value", which zeroes the hreflang audit and nulls the '
        f"SEO category. Offending tag(s): {offenders}"
    )


def test_canonical_link_is_present_and_absolute() -> None:
    canonicals = [tag for tag in _link_tags() if tag.get("rel") == "canonical"]
    assert len(canonicals) == 1, (
        "docs/site.html must declare exactly one <link rel='canonical'> so "
        f"search engines have a single, unambiguous self-reference; found "
        f"{len(canonicals)}"
    )
    href = canonicals[0].get("href", "")
    assert _is_absolute(href), (
        f"docs/site.html canonical href must be absolute, got: {href!r}"
    )
