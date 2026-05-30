"""Regression tests for the line-by-line audit round (2026-05-30, follow-up).

Each test pins one defect fixed in the same change set so a future refactor
cannot silently reintroduce it:

* ``src/utils/secret_scanner.py``       — residual ReDoS in ``_SENSITIVE_ASSIGN_RE``
* ``src/utils/logging.py``              — ``accessId=`` mask must keep JSON valid;
                                           ``webhook``/``jwt``/``dsn`` redaction
* ``src/feed/logging_safe.py``          — ``SafeJSONFormatter`` emits valid JSON
* ``src/utils/http.py``                 — NFKC re-check after hostname normalisation
* ``src/feed/config.py``                — ``validate_path`` NUL byte -> InvalidPathError
* ``src/utils/stats.py``                — naive ``now`` must not raise
* ``scripts/update_wl_stations.py``     — ``_coerce_float`` rejects NaN/Inf
* ``scripts/gtfs.py``                   — ``_coerce_float`` rejects NaN/Inf
* ``scripts/update_station_directory`` — ``_coerce_bst_id`` survives NaN/Inf
* ``scripts/generate_markdown_stats``  — non-finite delay row dropped, no crash
* ``scripts/run_static_checks.py``      — signal-killed subprocess is a failure
"""

from __future__ import annotations

import json
import logging as pylog
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# secret_scanner — _SENSITIVE_ASSIGN_RE must scan a hostile long line in linear
# time (the bounded affixes were still retried per-position before the fix).
# ---------------------------------------------------------------------------
def test_sensitive_assign_regex_is_not_redos() -> None:
    from src.utils.secret_scanner import _SENSITIVE_ASSIGN_RE

    # A 1 MiB run of [a-z0-9_.-] embedding a keyword but with NO ``[:=]`` is the
    # worst case. Pre-fix this took ~34 s; post-fix it is a few tens of ms. A
    # 5 s ceiling leaves a ~100x margin while still catching a real regression.
    hostile = "token" + ("a" * (1024 * 1024))
    start = time.perf_counter()
    list(_SENSITIVE_ASSIGN_RE.finditer(hostile))
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize(
    "line",
    [
        'api_key = "AKIAIOSFODNN7EXAMPLEZZ"',
        "client_secret=verylongsecretvalue1234567890",
        'my_api_key: "s3cr3tValue-longenough-123"',
        "password = hunter2hunter2hunter2value",
    ],
)
def test_sensitive_assign_regex_still_detects_real_assignments(line: str) -> None:
    # The ReDoS fix (boundary lookbehind) must not weaken detection of realistic
    # ``key = value`` secret assignments.
    from src.utils.secret_scanner import _scan_content

    assert _scan_content(line), f"expected a finding for: {line!r}"


# ---------------------------------------------------------------------------
# logging — accessId masking must not corrupt JSON; webhook/jwt/dsn redaction.
# ---------------------------------------------------------------------------
def test_safe_json_formatter_stays_valid_with_access_id() -> None:
    from src.feed.logging_safe import SafeJSONFormatter

    formatter = SafeJSONFormatter()
    record = pylog.LogRecord(
        "t", pylog.INFO, "f.py", 1, "auth accessId=SECRETTOKENVALUE", None, None
    )
    rendered = formatter.format(record)
    parsed = json.loads(rendered)  # must not raise
    assert "SECRETTOKENVALUE" not in parsed["message"]
    assert "***" in parsed["message"]


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("webhook_url=https://hooks.slack.com/services/T0/B0/XXXXSECRETtoken", "XXXXSECRETtoken"),
        ("jwt=opaqueJwtValue1234567890", "opaqueJwtValue1234567890"),
        ("dsn=https-sentry-secret-value", "sentry-secret-value"),
        ("cfduid=abc123def456ghi", "abc123def456ghi"),
    ],
)
def test_sanitize_log_message_redacts_sibling_keys(text: str, secret: str) -> None:
    from src.utils.logging import sanitize_log_message

    assert secret not in sanitize_log_message(text)


# ---------------------------------------------------------------------------
# http — validate_http_url must re-run the unsafe-char gate after NFKC folds a
# fullwidth structural char into an ASCII one.
# ---------------------------------------------------------------------------
def test_validate_http_url_rejects_fullwidth_structural_char() -> None:
    from src.utils.http import validate_http_url

    # U+FF1C FULLWIDTH LESS-THAN normalises to '<' under NFKC.
    assert validate_http_url("http://exam＜ple.com/p", check_dns=False) is None


# ---------------------------------------------------------------------------
# feed/config — the "never raises" companion must not propagate a bare
# ValueError for a NUL-byte path.
# ---------------------------------------------------------------------------
def test_is_within_allowed_roots_handles_nul_byte() -> None:
    from src.feed.config import is_within_allowed_roots

    assert is_within_allowed_roots(Path("docs/foo\x00bar.txt")) is False


# ---------------------------------------------------------------------------
# stats — a naive ``now`` must degrade to "no observations", not raise.
# ---------------------------------------------------------------------------
def test_read_recent_observations_tolerates_naive_now(tmp_path: Path) -> None:
    from src.utils.stats import read_recent_stammstrecke_observations

    result = read_recent_stammstrecke_observations(
        now=datetime(2026, 5, 30, 12, 0, 0),  # naive
        window=timedelta(hours=1),
        stats_dir=tmp_path,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Non-finite coordinate / id parsers must reject NaN/Inf instead of poisoning
# data or crashing.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "1e400", "Infinity", "NaN"])
def test_wl_coerce_float_rejects_non_finite(token: str) -> None:
    from scripts.update_wl_stations import _coerce_float

    assert _coerce_float(token) is None


@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "1e400", "Infinity"])
def test_gtfs_coerce_float_rejects_non_finite(token: str) -> None:
    from scripts.gtfs import _coerce_float

    assert _coerce_float(token) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_coerce_bst_id_survives_non_finite(value: float) -> None:
    from scripts.update_station_directory import _coerce_bst_id

    # Must return None rather than raising ValueError/OverflowError from int().
    assert _coerce_bst_id(value) is None


def test_generate_markdown_stats_drops_non_finite_delay() -> None:
    from scripts.generate_markdown_stats import _parse_stammstrecke_rows

    rows = [
        {"timestamp": "2026-05-30T12:00:00+02:00", "delay_minutes": "nan", "direction": "S1"},
        {"timestamp": "2026-05-30T12:05:00+02:00", "delay_minutes": "3.0", "direction": "S1"},
    ]
    parsed = _parse_stammstrecke_rows(rows)
    assert [r.delay_minutes for r in parsed] == [3.0]


# ---------------------------------------------------------------------------
# run_static_checks — a signal-killed subprocess (negative returncode) must be
# reported as a failure even when every other check passed.
# ---------------------------------------------------------------------------
def test_run_static_checks_reports_signal_kill_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.run_static_checks as rsc

    monkeypatch.setattr(sys, "argv", ["run_static_checks"])
    # Simulate mypy OOM-killed (-9) while ruff/bandit/scan/i18n/pip-audit pass.
    monkeypatch.setattr(rsc, "_run", lambda command: -9 if "mypy" in command else 0)
    assert rsc.main() == 1
