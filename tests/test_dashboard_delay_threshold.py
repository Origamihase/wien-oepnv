"""The dashboard must count what the backend counts.

``src/feed/stammstrecke.py`` defines a severe delay as **strictly greater
than** :data:`DELAY_THRESHOLD_MINUTES` (``obs.delay_minutes >
DELAY_THRESHOLD_MINUTES`` — every consumer, incl.
``scripts/generate_markdown_stats.py``, uses that form and labels it
``Kritische Verspätungen (> 9 min)``).

``docs/assets/site.js`` re-implements the same statistic client-side and had
drifted on BOTH halves: it counted ``r.delay >= 9`` and labelled the tile
``≥ 9 Minuten`` / ``≥ 9 minutes``. An observation of exactly 9.0 minutes was
therefore counted on the website but not by the feed, the markdown dashboard
or the README snapshot — the same data rendered two different numbers
depending on where you looked.

These tests pin the JavaScript back to the Python source of truth. They read
the shipped asset from disk (like ``tests/test_i18n_coverage_gate.py``) rather
than executing it, so no JS runtime is required.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES

_SITE_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"


def _site_js() -> str:
    return _SITE_JS.read_text(encoding="utf-8")


def test_dashboard_threshold_constant_matches_the_backend() -> None:
    match = re.search(r"const DELAY_THRESHOLD_MIN = (\d+(?:\.\d+)?);", _site_js())
    assert match is not None, "site.js must declare a DELAY_THRESHOLD_MIN constant"
    assert float(match.group(1)) == DELAY_THRESHOLD_MINUTES


def test_dashboard_counts_strictly_greater_than_the_threshold() -> None:
    # ``>=`` here would count a 9.0-minute observation the feed does not.
    assert (
        "valid.filter((r) => r.delay > DELAY_THRESHOLD_MIN)" in _site_js()
    ), "the severe-delay tile must filter with a STRICT > against the constant"


def test_dashboard_label_is_derived_from_the_constant() -> None:
    # The visible label is built from the same constant, so it cannot drift
    # from the filter again.
    assert "sub: `> ${DELAY_THRESHOLD_MIN} min`" in _site_js()


def test_no_inclusive_threshold_wording_survives_in_code() -> None:
    # Guards against a hardcoded label (e.g. a reintroduced translation
    # entry) claiming "≥ 9" again. Line comments are stripped first: this
    # file's own history notes quote the old wording, and an unrelated
    # comment names browser versions as "chrome ≥ 86".
    code_only = re.sub(r"//.*", "", _site_js())
    assert "≥ 9" not in code_only
