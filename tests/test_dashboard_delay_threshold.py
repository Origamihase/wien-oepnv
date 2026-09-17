"""The dashboard must count what the backend counts.

``src/feed/stammstrecke.py`` defines a severe delay as **strictly greater
than** :data:`DELAY_THRESHOLD_MINUTES` (``obs.delay_minutes >
DELAY_THRESHOLD_MINUTES`` — every consumer, incl.
``scripts/generate_markdown_stats.py``, uses that form and labels it
``Kritische Verspätungen (> 9 min)``).

``docs/assets/site.js`` used to re-implement the same statistic
client-side and had drifted on BOTH halves: it counted ``r.delay >= 9``
and labelled the tile ``≥ 9 Minuten`` / ``≥ 9 minutes``. An observation
of exactly 9.0 minutes was therefore counted on the website but not by
the feed, the markdown dashboard or the README snapshot — the same data
rendered two different numbers depending on where you looked.

Audit E.3 removed the second implementation entirely: the browser reads
``threshold_exceedances`` out of ``docs/stats-summary.json``, so the
comparison exists in exactly one place. What is left to pin is that the
one comparison is strict, and that the visible label still describes the
threshold the count was made with.

These tests read the shipped asset from disk (like
``tests/test_i18n_coverage_gate.py``) rather than executing it, so no JS
runtime is required.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from scripts.generate_markdown_stats import aggregate_stammstrecke
from src.feed.stammstrecke import DELAY_THRESHOLD_MINUTES

_SITE_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "site.js"


def _site_js() -> str:
    return _SITE_JS.read_text(encoding="utf-8")


def test_dashboard_threshold_constant_matches_the_backend() -> None:
    match = re.search(r"const DELAY_THRESHOLD_MIN = (\d+(?:\.\d+)?);", _site_js())
    assert match is not None, "site.js must declare a DELAY_THRESHOLD_MIN constant"
    assert float(match.group(1)) == DELAY_THRESHOLD_MINUTES


def test_dashboard_counts_strictly_greater_than_the_threshold() -> None:
    """The count is made once, in Python, with a strict ``>``.

    The browser used to re-derive it from the raw ledger, which is how
    the two halves drifted apart in the first place (``>=`` on the site,
    ``>`` in the backend, a 9.0-minute observation counted in one place
    and not the other). Audit E.3 moved the roll-up into
    ``aggregate_stammstrecke``; the tile now shows the number the
    backend computed, so there is only one comparison left to get right.
    """
    source = inspect.getsource(aggregate_stammstrecke)
    assert (
        "row.delay_minutes > threshold_minutes" in source
    ), "the aggregate must count with a STRICT > against the threshold"
    assert (
        "nfInt.format(numberOr(stats.threshold_exceedances, 0))" in _site_js()
    ), "the severe-delay tile must render the backend's count, not its own"


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
