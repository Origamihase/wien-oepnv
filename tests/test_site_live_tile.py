"""The live tile "Ø Verspätung Stammstrecke" on ``docs/site.html``.

Runs ``renderStammstreckeLiveTile`` from ``docs/assets/site.js`` in Node
(skipped when Node is not installed). An empty 60-minute window reads
"Keine Fahrten" only while the last successful fetch lies inside it;
otherwise nothing was measured and the tile keeps "N/A". Operator
decision 2026-10-30, mirrored by ``_live_fetch_covers`` for the README.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_site_feed_tiles import _block

SITE_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")

NOW = "2026-10-30T15:01:30Z"


def _tile(live: object, poll: object, lang: str = "de") -> str:
    js = SITE_JS.read_text(encoding="utf-8")
    parts = [
        f"let currentLang = {json.dumps(lang)};",
        _block(js, "const CHART_TEXT_DE = {"),
        _block(js, "const CHART_TEXT_EN = {"),
        _block(js, "function ct(key) {"),
        _block(js, "function numberOr(value, fallback) {"),
        _block(js, "function renderStammstreckeLiveTile(live, poll) {"),
    ]
    program = (
        f"Date.now = () => Date.parse({json.dumps(NOW)});\n"
        "const node = { textContent: '' };\n"
        "const $ = () => node;\n"
        "const nf1 = new Intl.NumberFormat('de-AT', { minimumFractionDigits: 1, maximumFractionDigits: 1 });\n"
        + "\n".join(parts)
        + f"\nrenderStammstreckeLiveTile({json.dumps(live)}, {json.dumps(poll)});"
        "\nprocess.stdout.write(JSON.stringify(node.textContent));"
    )
    assert NODE is not None
    # The program is assembled from site.js of this checkout, no outside input.
    argv = [NODE, "-e", program]
    out = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)  # noqa: S603
    return json.loads(out.stdout)


EMPTY = {"avg_delay_minutes": None, "observations": 0, "window_minutes": 60}


def test_value_wins_over_wording() -> None:
    live = {"avg_delay_minutes": 3.5, "observations": 2, "window_minutes": 60}
    assert _tile(live, {"last_success": "2026-10-30T16:00:52+01:00"}) == "3,5 min"


def test_empty_window_behind_working_fetch_reads_keine_fahrten() -> None:
    # 30.10.2026 16:01: every train of the hour cancelled, fetch at 16:00 ok.
    poll = {"last_success": "2026-10-30T16:00:52+01:00", "stale_hours": 3.0}
    assert _tile(EMPTY, poll) == "Keine Fahrten"
    assert _tile(EMPTY, poll, lang="en") == "No trains"


def test_empty_window_without_fetch_in_it_stays_na() -> None:
    old = {"last_success": "2026-10-30T14:30:00+01:00", "stale_hours": 3.0}
    assert _tile(EMPTY, old) == "N/A"
    assert _tile(EMPTY, {"last_success": None}) == "N/A"
    assert _tile(EMPTY, None) == "N/A"
    assert _tile(None, None) == "N/A"
