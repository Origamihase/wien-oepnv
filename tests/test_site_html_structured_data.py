"""Sentinel guard for the JSON-LD structured data in ``docs/site.html``.

Schema.org markup served as ``application/ld+json`` is what lets search
engines and LLM crawlers ingest the dashboard as a typed entity rather
than guessing from prose. The block already ships in ``docs/site.html``;
this test fails loudly if a future edit drops it or makes it invalid JSON,
so the AI/search visibility can never silently regress.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SITE_HTML = Path(__file__).resolve().parents[1] / "docs" / "site.html"

_LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _ld_json_blocks() -> list[str]:
    return _LD_JSON_RE.findall(SITE_HTML.read_text(encoding="utf-8"))


def test_site_html_carries_a_json_ld_block() -> None:
    assert _ld_json_blocks(), (
        "docs/site.html must keep a <script type='application/ld+json'> block "
        "for AI / search-engine entity recognition"
    )


def test_every_json_ld_block_is_valid_and_typed() -> None:
    for raw in _ld_json_blocks():
        data: Any = json.loads(raw)  # raises if the block is not valid JSON
        assert "schema.org" in str(data.get("@context", "")), (
            "JSON-LD @context must reference schema.org"
        )
        assert data.get("@type"), "JSON-LD block must declare an @type"
