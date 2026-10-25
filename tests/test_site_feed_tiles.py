"""The feed tiles on ``docs/site.html`` agree with the item they show.

Runs the real functions from ``docs/assets/site.js`` in Node (skipped when
Node is not installed): the tile's source follows the item's RSS
``<source>``, "Beginn" follows ``starts_at`` like the item's time line, and
"Bis" is a date, as on the TV.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SITE_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def _block(js: str, start: str) -> str:
    """The source from ``start`` up to its balanced closing brace."""
    begin = js.index(start)
    depth = 0
    for pos in range(js.index("{", begin), len(js)):
        if js[pos] == "{":
            depth += 1
        elif js[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                while end < len(js) and js[end] in ");":
                    end += 1
                return js[begin:end]
    raise AssertionError(f"unbalanced block {start!r}")


def _run(expr: str) -> object:
    js = SITE_JS.read_text(encoding="utf-8")
    parts = [
        _block(js, "const SOURCE_TAGS = {"),
        _block(js, "const dtfViennaDay = new Intl.DateTimeFormat("),
        _block(js, "const dtfViennaClock = new Intl.DateTimeFormat("),
        _block(js, "function detectSource(item) {"),
        _block(js, "function parseRfc2822(s) {"),
        _block(js, "function itemBegin(item) {"),
        _block(js, "function isViennaMidnight(date) {"),
    ]
    program = "\n".join(parts) + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    assert NODE is not None
    # The program is assembled from site.js of this checkout, no outside input.
    argv = [NODE, "-e", program]
    out = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)  # noqa: S603
    return json.loads(out.stdout)


def test_source_tag_decides_the_tile() -> None:
    oebb_link = "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty"
    result = _run(
        "["
        + ",".join(
            f"detectSource({json.dumps(item)})"
            for item in (
                {"sourceName": "Stadt Wien", "link": "https://www.wien.gv.at/verkehr/baustellen", "title": "x"},
                {"sourceName": "City of Vienna", "link": "https://www.wien.gv.at/verkehr/baustellen", "title": "x"},
                {"sourceName": "VOR/VAO", "link": oebb_link, "title": "S-Bahn Stammstrecke"},
                {"sourceName": "ÖBB", "link": oebb_link, "title": "Wiener Linien Ersatz"},
                {"sourceName": "", "link": "https://www.wien.gv.at/verkehr/baustellen", "title": "x"},
            )
        )
        + "]"
    )
    assert result == ["baustellen", "baustellen", "vor", "oebb", "baustellen"]


@pytest.mark.parametrize(
    ("pub", "start", "begin", "midnight"),
    [
        # Planned WL event published ten days ahead: the begin is the event day.
        ("Fri, 16 Oct 2026 00:00:00 +0200", "Mon, 26 Oct 2026 00:00:00 +0100",
         "2026-10-25T23:00:00.000Z", True),
        # WL entered the incident a minute before the ticker: the earlier minute.
        ("Sun, 25 Oct 2026 20:54:00 +0100", "Sun, 25 Oct 2026 20:55:19 +0100",
         "2026-10-25T19:54:00.000Z", False),
        # A full hour is a pre-entered measure, not the begin.
        ("Sun, 25 Oct 2026 05:00:00 +0100", "Sun, 25 Oct 2026 05:30:00 +0100",
         "2026-10-25T04:30:00.000Z", False),
        # Earlier day: the time line starts at starts_at ("[Seit 22.10.]").
        ("Wed, 21 Oct 2026 10:12:00 +0200", "Thu, 22 Oct 2026 00:00:00 +0200",
         "2026-10-21T22:00:00.000Z", True),
    ],
)
def test_begin_follows_the_time_line(pub: str, start: str, begin: str, midnight: bool) -> None:
    item = json.dumps({"pubDate": pub, "startsAt": start})
    result = _run(
        f"(() => {{ const b = itemBegin({item}); "
        "return [b.toISOString(), isViennaMidnight(b)]; })()"
    )
    assert result == [begin, midnight]


def test_end_is_shown_as_a_date() -> None:
    js = SITE_JS.read_text(encoding="utf-8")
    assert re.search(r"datetime: ends\.toISOString\(\) \}, text: dtfDate\.format\(ends\)", js)
