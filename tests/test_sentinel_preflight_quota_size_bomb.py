"""Sentinel PoC: preflight quota state — JSON size-bomb on the
``scripts/preflight_quota_check.py:_read_json_file`` loader.

The 2026-05-08 *JSON Size-Bomb Round 3* round (``.jules/sentinel.md``)
canonicalised ``read_capped_json`` for sixteen on-disk JSON loaders
across eight scripts in ``scripts/``. The closing-checklist grep
enumerated every ``json.load(handle)`` / ``Path.read_text()``-then-
``json.loads`` site in ``scripts/`` at that point. But
``preflight_quota_check.py`` was added LATER as a defense-in-depth
pre-flight gate — and it carries the same shape that Round 3 closed::

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    except (OSError, json.JSONDecodeError, RecursionError) as exc:
        return {}

The catch tuple is structurally insufficient against ``MemoryError``
because it (a) does not catch ``MemoryError`` (a ``BaseException``
subclass — not in the tuple); (b) ``json.load(handle)`` buffers the
entire file into memory before parsing, so a planted-huge file at
``data/vor_request_count.json`` or ``data/places_quota.json`` raises
``MemoryError`` (the on-disk shape is the same wide-but-flat
``[1,1,1,…]`` form that ``RecursionError`` does NOT trigger on).

Threat model
------------

The pre-flight is a *gate* for two automated workflows:

  1. ``update-stammstrecke-status.yml`` — runs every ~30 min via
     ``update-cycle.yml`` DAG; fans out the VAO ``/trip`` quota
     check.
  2. ``update-google-places-stations.yml`` — operator-triggered;
     gates the Places API call against the monthly cap.

The state files (``data/vor_request_count.json``,
``data/places_quota.json``) are committed by previous runs of the
cron pipeline and read by every subsequent run. A planted-huge file
at either path (compromised CI runner / corrupted previous run /
partial flush + power loss / malicious PR landing oversized state)
crashes the pre-flight with ``MemoryError`` — propagating past the
catch tuple, exiting the script with a non-zero status, and
zeroing-out the GitHub Actions ``quota_ok`` output. The downstream
conditional ``if: steps.preflight.outputs.quota_ok == 'true'`` is
*accidentally* fail-closed (an empty output is not ``'true'``), so
the API step is skipped — but the operator sees a confusing
``MemoryError`` traceback at the top of the workflow log instead of
the documented ``"quota EXHAUSTED — projected=N, limit=M"`` message,
and the cron pipeline cannot refresh the Stammstrecke status / Places
stations until the poisoned state file is manually deleted.

Severity
--------

LOW-MEDIUM — DoS on the cron pipeline. No credential leak, no
fail-OPEN behaviour (the workflow gates on ``quota_ok == 'true'``
which is fail-closed-by-accident on empty output). But the project
has fixed eight prior rounds of the same drift family at this
severity, and the canonical contract is that EVERY on-disk JSON
loader carries a ``read_capped_json`` cap so a future caller / a
future workflow / a future operator script inheriting this code
path cannot regress to fail-open.

Fix shape
---------

Mirrors Round 3 (``test_sentinel_json_size_bomb_ondisk_round3.py``):
replace the bare ``open + json.load(handle)`` shape with
``read_capped_json(path, MAX_PREFLIGHT_QUOTA_FILE_BYTES, …)``. The
canonical helper (``src/utils/files.py``) combines the byte-size cap
(open-then-fstat the open fd, defeating TOCTOU between stat and
open) with the depth-bomb catch tuple in one place. The new
``MAX_PREFLIGHT_QUOTA_FILE_BYTES`` constant (1 MiB, ~5000x typical
state — ``{"date": "...", "requests": N}`` is a few dozen bytes)
mirrors ``src/places/quota.py:MAX_QUOTA_FILE_BYTES``.

Closing-checklist
-----------------

The auto-discoverable invariant is the journal-pinned grep::

    grep -rn 'json\\.load(\\b' src/ scripts/ | grep -v 'json.loads\\|test_'

After this fix, the only remaining hit is the comment line in
``src/utils/files.py:218`` (the docstring for ``read_capped_json``
itself).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _scripts_path_bootstrap() -> None:
    """Ensure ``scripts/`` is importable for the duration of the test
    module. The script's own ``sys.path`` manipulation runs at script
    invocation time, not at import time, so we mirror it here."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _write_oversized(path: Path, size_bytes: int) -> None:
    """Write a flat-but-huge JSON object that exceeds the loader's
    byte cap. The shape ``{"date": "2026-...", "requests": …, ...}``
    with millions of dummy keys is intentional: it parses to a valid
    dict (so ``json.load`` would succeed if it ran) AND consumes
    memory proportional to file size."""
    keys = []
    # Build a key roster wide enough to exceed size_bytes.
    bucket = size_bytes // 32 + 1
    for i in range(bucket):
        keys.append(f'"k{i:08d}":0')
    payload = "{" + ",".join(keys) + "}"
    path.write_text(payload, encoding="utf-8")


# ============================================================================
# Precondition — the canonical helper and the new cap constant exist.
# ============================================================================


def test_precondition_helper_exists() -> None:
    """``read_capped_json`` must be importable; the inventory test
    below depends on the canonical helper."""
    from src.utils.files import read_capped_json

    assert callable(read_capped_json)


def test_precondition_max_constant_exists() -> None:
    """The new cap constant must exist as a module-level int and
    bound the worst-case allocation well below the runner's cgroup
    cap."""
    from scripts import preflight_quota_check

    assert hasattr(preflight_quota_check, "MAX_PREFLIGHT_QUOTA_FILE_BYTES")
    cap = preflight_quota_check.MAX_PREFLIGHT_QUOTA_FILE_BYTES
    assert isinstance(cap, int)
    # Sized to fit comfortably in any cron runner's 1 GiB cgroup limit.
    assert 0 < cap <= 100 * 1024 * 1024


# ============================================================================
# Behavioural invariants — file size cap is enforced.
# ============================================================================


def test_read_json_file_rejects_oversized_state(tmp_path: Path) -> None:
    """Pre-fix: a 4 KiB poisoned state file at the documented path
    is buffered via ``json.load(handle)`` regardless of size; a
    multi-MiB / multi-GiB shape would raise ``MemoryError``.
    Post-fix: the loader checks the file size (via ``os.fstat`` on
    the open fd) and returns ``{}`` when the cap is exceeded.

    To exercise the cap deterministically without allocating actual
    GB of memory, we tighten ``MAX_PREFLIGHT_QUOTA_FILE_BYTES`` to
    1 KiB for the test run and plant a 4 KiB file."""
    from scripts import preflight_quota_check

    poisoned = tmp_path / "vor_request_count.json"
    _write_oversized(poisoned, 4096)

    with patch.object(preflight_quota_check, "MAX_PREFLIGHT_QUOTA_FILE_BYTES", 1024):
        result = preflight_quota_check._read_json_file(poisoned)

    assert result == {}


def test_read_json_file_accepts_under_cap_state(tmp_path: Path) -> None:
    """Happy path: a small state file (a few dozen bytes — production
    shape) parses to its dict representation under the cap. Pre-
    and post-fix behaviour MUST match for the legitimate state."""
    from scripts import preflight_quota_check

    legit = tmp_path / "vor_request_count.json"
    legit.write_text(
        '{"date": "2026-05-10", "requests": 7}', encoding="utf-8"
    )

    result = preflight_quota_check._read_json_file(legit)
    assert result == {"date": "2026-05-10", "requests": 7}


def test_read_json_file_returns_empty_for_missing_path(tmp_path: Path) -> None:
    """Regression: a missing state file continues to return ``{}``
    (treated as fresh state, count = 0). The fix MUST preserve this
    contract."""
    from scripts import preflight_quota_check

    missing = tmp_path / "does_not_exist.json"
    assert preflight_quota_check._read_json_file(missing) == {}


def test_read_json_file_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    """Regression: an invalid-JSON file continues to return ``{}``
    (the daily-reset logic in the consumer rewrites it on next
    save). The fix MUST preserve this contract."""
    from scripts import preflight_quota_check

    invalid = tmp_path / "broken.json"
    invalid.write_text("{not valid json", encoding="utf-8")

    assert preflight_quota_check._read_json_file(invalid) == {}


def test_read_json_file_returns_empty_for_non_dict(tmp_path: Path) -> None:
    """Regression: a JSON list / scalar / null at the state path
    continues to return ``{}`` (the consumer expects a dict shape).
    The fix MUST preserve this contract."""
    from scripts import preflight_quota_check

    weird = tmp_path / "weird.json"
    weird.write_text("[1, 2, 3]", encoding="utf-8")
    assert preflight_quota_check._read_json_file(weird) == {}

    weird.write_text('"just a string"', encoding="utf-8")
    assert preflight_quota_check._read_json_file(weird) == {}

    weird.write_text("null", encoding="utf-8")
    assert preflight_quota_check._read_json_file(weird) == {}


# ============================================================================
# Closing-checklist invariant — no remaining direct ``json.load(handle)`` in
# ``scripts/`` (the comment in ``src/utils/files.py`` is excluded as it's the
# docstring for the canonical helper itself).
# ============================================================================


def test_no_direct_json_load_in_scripts() -> None:
    """Auto-discoverable invariant: walk every ``*.py`` in
    ``scripts/`` and assert that ``json.load(`` (the bare open-then-
    json.load shape) does not appear outside comments. Round 3 closed
    16 sites; this test pins the post-fix contract so a future
    contributor cannot regress.

    The grep is intentionally scoped to ``scripts/`` only because
    ``src/`` carries the canonical helper (``read_capped_json`` in
    ``src/utils/files.py``) which legitimately uses ``json.loads`` on
    bytes — the helper is the single point of audit and any
    ``src/`` site that bypasses it is caught by the existing
    ``test_sentinel_json_audit_walker`` companion suite.
    """
    import re as _re

    pattern = _re.compile(r"\bjson\.load\(")

    offenders: list[str] = []
    scripts_dir = REPO_ROOT / "scripts"
    for py_path in scripts_dir.glob("*.py"):
        text = py_path.read_text(encoding="utf-8")
        for line_idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Skip pure comment lines and docstring lines.
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{py_path.name}:{line_idx}: {stripped}")

    assert not offenders, (
        "Direct json.load() calls remain in scripts/. Use "
        "src.utils.files.read_capped_json instead.\n"
        + "\n".join(offenders)
    )
