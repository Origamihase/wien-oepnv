"""Sentinel PoC: Memory-exhaustion via unbounded ``Path.read_text`` — Round 7.

Threat model
------------
Round 6 of the size-bomb family canonicalised ``read_capped_text`` for the
non-JSON ``Path.read_text()`` -> ``MemoryError`` propagation shape across
**six sites in two modules** (``src/providers/vor.py`` x5,
``src/feed/logging.py`` x1), and pinned the auto-discoverable closing
grep::

    git grep -nE 'read_text\\(' src/ | grep -v 'read_capped_text\\|test_'

But Round 6's grep was ``src/``-only — exactly the same ``scripts/`` blind
spot that JSON Size-Bomb Round 3 closed for the ``json.load`` axis after
Round 2's ``src/``-only verdict. Re-running the grep against BOTH
``src/`` and ``scripts/`` returned **nine open sites in six modules** that
read attacker-controlled files via ``Path.read_text(encoding="utf-8")``
(or its ``errors="ignore"`` variant) with NO byte-size cap whatsoever.

Sites covered
-------------
 1. ``src/utils/env.py:read_secret`` (systemd credentials branch,
    line 128 pre-fix) — **CRITICAL**: called at startup of every script
    that imports a provider (``src/providers/vor.py``,
    ``src/places/client.py``, ``src/feed/reporting.py``). The
    ``$CREDENTIALS_DIRECTORY`` path is operator-controlled (systemd
    LoadCredential / web-frontend supplied) and reads the secret file
    unbounded — a planted huge file at the credential path crashes the
    whole pipeline at import time.
 2. ``src/utils/env.py:read_secret`` (docker secrets branch, line 141
    pre-fix) — **CRITICAL**: same blast radius as (1) but reads
    ``/run/secrets/<name>``. A compromised container mount or
    misconfigured operator can plant a huge file at that path.
 3. ``src/utils/env.py:load_env_file`` (line 374 pre-fix) — **CRITICAL**:
    called at startup via ``load_default_env_files`` from
    ``scripts/check_vor_auth.py``, ``scripts/fetch_google_places_stations.py``,
    ``scripts/update_station_directory.py``, ``scripts/verify_google_places_access.py``,
    ``scripts/verify_vor_access_id.py``. The path candidates include
    ``.env`` / ``data/secrets.env`` / ``config/secrets.env`` PLUS any
    extra paths from ``WIEN_OEPNV_ENV_FILES`` (env var). A planted huge
    .env file at any of these locations raises ``MemoryError`` BEFORE
    any provider runs.
 4. ``src/utils/secret_scanner.py:load_ignore_file`` (line 276 pre-fix) —
    HIGH: CI gate. The ``.secret-scan-ignore`` file is repo-local, so a
    compromised PR could plant a huge ignore file and crash the secret
    scanner CI gate — bypassing secret detection on subsequent commits.
 5. ``src/utils/secret_scanner.py:scan_repository`` per-file read
    (line 507 pre-fix) — HIGH: CI gate. The scanner walks every tracked
    file in the repo. A planted huge tracked file (e.g. an
    intentionally-poisoned data dump) crashes the scanner before it can
    flag any planted secrets — bypassing the gate.
 6. ``scripts/check_complexity.py:_parse_baseline`` (line 58 pre-fix) —
    MEDIUM: CI gate. The C901 complexity baseline file is repo-local,
    so a planted huge baseline crashes the CI gate (`MemoryError`
    propagates past the loader and crashes the gate, which would
    otherwise either reject new violations or allow them through).
 7. ``scripts/fetch_google_places_stations.py:_write_if_changed``
    (line 299 pre-fix) — MEDIUM: reads the existing stations.json
    file unbounded before deciding whether to overwrite. A planted
    huge ``stations.json`` raises ``MemoryError`` past the surrounding
    handler and crashes the cron pipeline.
 8. ``scripts/update_vor_cache.py:_seed_station_ids_from_file``
    (line 40 pre-fix) — MEDIUM: reads
    ``data/vor_station_ids_wien.txt`` unbounded at startup of the
    daily VOR cache update. A planted huge file crashes the entire
    update before any network request runs.
 9. ``scripts/update_vor_stations.py:_read_station_ids_from_file``
    (line 397 pre-fix) — MEDIUM: reads operator-supplied station ID
    file unbounded. A planted huge file crashes the station-directory
    update before any state is merged.

Fix shape
---------
Identical to Round 6: replace ``Path.read_text(encoding="utf-8")`` with
``read_capped_text(path, MAX_*_BYTES, label=..., logger=log)`` (returns
``None`` for missing / oversized / decode-error). Each call site
expresses its own per-loader cap constant exposed at module level so the
auto-discoverable inventory test catches any future loader added without
the cap.

Combined with all prior rounds (35 covered parsers in Round 6), the
canonical inventory now stands at **44 covered parsers (38 disk + 3
network + 3 disk-text)** — every one TOCTOU-safe, special-file-safe,
and threat-indexed-helper-routed.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ============================================================================
# Helpers
# ============================================================================


def _write_oversized_text(path: Path, size_bytes: int) -> None:
    """Write a long flat text payload that exceeds the loader's byte cap."""
    path.write_text("a" * size_bytes, encoding="utf-8")


# ============================================================================
# Precondition: per-loader cap constants are exposed at module level
# ============================================================================


def test_precondition_env_size_cap_constants_exist() -> None:
    """Pin the canonical cap constants for ``src/utils/env.py``."""
    from src.utils import env

    assert isinstance(env.MAX_ENV_FILE_BYTES, int)
    assert env.MAX_ENV_FILE_BYTES > 0
    # Sized at ~1000x the largest legitimate .env shape (~1 KiB)
    assert env.MAX_ENV_FILE_BYTES >= 100_000

    assert isinstance(env.MAX_SECRET_FILE_BYTES, int)
    assert env.MAX_SECRET_FILE_BYTES > 0
    # Secrets are typically a single line; cap at 1 MiB (~10000x legit)
    assert env.MAX_SECRET_FILE_BYTES >= 10_000


def test_precondition_secret_scanner_cap_constants_exist() -> None:
    """Pin the canonical cap constants for ``src/utils/secret_scanner.py``."""
    from src.utils import secret_scanner

    assert isinstance(secret_scanner.MAX_IGNORE_FILE_BYTES, int)
    assert secret_scanner.MAX_IGNORE_FILE_BYTES > 0

    assert isinstance(secret_scanner.MAX_SCAN_FILE_BYTES, int)
    assert secret_scanner.MAX_SCAN_FILE_BYTES > 0
    # Must accommodate large checked-in data files but reject GiB-sized
    # planted attacks
    assert secret_scanner.MAX_SCAN_FILE_BYTES >= 1_000_000


# ============================================================================
# src/utils/env.py — read_secret (systemd + docker secrets branches)
# ============================================================================


def test_read_secret_systemd_rejects_oversized_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``read_secret`` -> ``path.read_text(encoding="utf-8").strip()``
    on the systemd credentials branch with NO size cap. A planted huge
    file at ``$CREDENTIALS_DIRECTORY/<name>`` raises ``MemoryError`` past
    the ``except (OSError, ValueError)`` catch.

    Post-fix: ``read_capped_text`` returns ``None`` and the function
    falls through to the next backing store (docker / env)."""
    from src.utils import env

    cred_dir = tmp_path / "creds"
    cred_dir.mkdir()
    poisoned = cred_dir / "MY_TOKEN"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
    monkeypatch.setattr(env, "MAX_SECRET_FILE_BYTES", 1024)
    # Ensure no fallback env value is found
    monkeypatch.delenv("MY_TOKEN", raising=False)

    result = env.read_secret("MY_TOKEN", default="fallback")
    # Oversized file → fall through to env → default
    assert result == "fallback"


def test_read_secret_docker_rejects_oversized_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same as above for the docker-secrets branch."""
    from src.utils import env

    docker_base = tmp_path / "secrets"
    docker_base.mkdir()
    poisoned = docker_base / "DOCKER_TOKEN"
    _write_oversized_text(poisoned, 4096)

    # Make read_secret look at our temp path instead of /run/secrets
    monkeypatch.setattr(env, "DOCKER_SECRETS_DIR", docker_base)
    monkeypatch.setattr(env, "MAX_SECRET_FILE_BYTES", 1024)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("DOCKER_TOKEN", raising=False)

    result = env.read_secret("DOCKER_TOKEN", default="fallback")
    assert result == "fallback"


def test_read_secret_systemd_accepts_normal_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: small credentials still read correctly."""
    from src.utils import env

    cred_dir = tmp_path / "creds"
    cred_dir.mkdir()
    cred_file = cred_dir / "MY_TOKEN"
    cred_file.write_text("my-secret-value\n", encoding="utf-8")

    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))

    result = env.read_secret("MY_TOKEN")
    assert result == "my-secret-value"


# ============================================================================
# src/utils/env.py — load_env_file
# ============================================================================


def test_load_env_file_rejects_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``load_env_file`` -> ``path.read_text(encoding="utf-8")``
    with NO size cap. A planted huge .env file at the project root or
    ``data/secrets.env`` raises ``MemoryError`` at startup of every
    script that calls ``load_default_env_files``.

    Post-fix: ``read_capped_text`` returns ``None``, the loader logs a
    warning, and returns ``{}`` so the rest of the startup proceeds."""
    from src.utils import env

    poisoned = tmp_path / ".env"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(env, "MAX_ENV_FILE_BYTES", 1024)

    fake_environ: dict[str, str] = {}
    result = env.load_env_file(poisoned, environ=fake_environ)

    assert result == {}
    assert "POISONED_KEY" not in fake_environ


def test_load_env_file_accepts_normal_file(tmp_path: Path) -> None:
    """Regression: normal .env files still parse correctly."""
    from src.utils import env

    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")

    fake_environ: dict[str, str] = {}
    result = env.load_env_file(env_file, environ=fake_environ)

    assert result == {"FOO": "bar", "BAZ": "qux"}
    assert fake_environ["FOO"] == "bar"
    assert fake_environ["BAZ"] == "qux"


# ============================================================================
# src/utils/secret_scanner.py — load_ignore_file
# ============================================================================


def test_secret_scanner_load_ignore_file_rejects_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``load_ignore_file`` reads ``.secret-scan-ignore`` via
    ``path.read_text(...).splitlines()`` with NO size cap. A planted
    huge ignore file raises ``MemoryError`` past the secret scanner
    main loop and crashes the CI gate — bypassing detection on
    subsequent commits in the same PR.

    Post-fix: ``read_capped_text`` returns ``None`` and
    ``load_ignore_file`` returns ``[]`` (empty pattern set, strictest
    policy)."""
    from src.utils import secret_scanner

    poisoned = tmp_path / ".secret-scan-ignore"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(secret_scanner, "MAX_IGNORE_FILE_BYTES", 1024)

    result = secret_scanner.load_ignore_file(tmp_path)
    assert result == []


def test_secret_scanner_load_ignore_file_accepts_normal(tmp_path: Path) -> None:
    """Regression: normal ignore files still parse correctly."""
    from src.utils import secret_scanner

    ignore_file = tmp_path / ".secret-scan-ignore"
    ignore_file.write_text(
        "# comment\n"
        "**/*.lock\n"
        "data/test_*.json\n"
        "\n",
        encoding="utf-8",
    )

    result = secret_scanner.load_ignore_file(tmp_path)
    assert result == ["**/*.lock", "data/test_*.json"]


# ============================================================================
# src/utils/secret_scanner.py — per-file scan content read
# ============================================================================


def test_secret_scanner_scan_skips_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: the scanner main loop reads each tracked file via
    ``file_path.read_text(encoding="utf-8", errors="ignore")`` with NO
    size cap. A planted huge file (e.g. an intentionally-corrupt data
    dump) raises ``MemoryError`` past the ``except OSError`` catch and
    crashes the scanner before it can flag any planted secrets.

    Post-fix: ``read_capped_text`` returns ``None`` and the scanner
    skips the oversized file (continues to the next)."""
    from src.utils import secret_scanner

    # Plant one normal file with a generic high-entropy assignment.
    # The literal token doesn't follow any specific issuer's format on
    # purpose (otherwise GitHub's push-protection secret scanner blocks
    # the test commit). The legacy ``_SENSITIVE_ASSIGN_RE`` flags any
    # ``API_KEY = "..."`` shape with sufficient body entropy.
    legit = tmp_path / "config.py"
    legit.write_text(
        'API_KEY = "ZqWxRdYpVbNmKjLhGfDsAeRtYuIoP1234567890"\n',
        encoding="utf-8",
    )
    # Plant an oversized file that would normally crash the scanner
    poisoned = tmp_path / "huge.txt"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(secret_scanner, "MAX_SCAN_FILE_BYTES", 1024)

    findings = secret_scanner.scan_repository(tmp_path, paths=[legit, poisoned])

    # The scanner must NOT crash; it should return findings for the
    # legit file and skip the oversized one
    paths = {f.path for f in findings}
    assert legit in paths
    assert poisoned not in paths


# ============================================================================
# scripts/check_complexity.py — _parse_baseline
# ============================================================================


def test_check_complexity_parse_baseline_rejects_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``_parse_baseline`` reads ``.c901-baseline.txt`` via
    ``path.read_text(...).splitlines()`` with NO size cap. A planted
    huge baseline file raises ``MemoryError`` past the gate and crashes
    the CI complexity check.

    Post-fix: ``read_capped_text`` returns ``None`` and
    ``_parse_baseline`` returns ``{}`` (empty baseline = strictest
    state, exactly what missing-file already does)."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import check_complexity
    finally:
        # Don't pollute sys.path for other tests
        sys.path.pop(0)

    poisoned = tmp_path / "baseline.txt"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(check_complexity, "MAX_BASELINE_FILE_BYTES", 1024)

    result = check_complexity._parse_baseline(poisoned)
    assert result == {}


def test_check_complexity_parse_baseline_accepts_normal(tmp_path: Path) -> None:
    """Regression: normal baseline files still parse correctly."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import check_complexity
    finally:
        sys.path.pop(0)

    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "# header comment\n"
        "fetch_events 51\n"
        "_iter_messages 46\n"
        "\n"
        "_format_item_content 33\n",
        encoding="utf-8",
    )

    result = check_complexity._parse_baseline(baseline)
    assert result == {
        "fetch_events": 51,
        "_iter_messages": 46,
        "_format_item_content": 33,
    }


# ============================================================================
# scripts/update_vor_cache.py — _seed_station_ids_from_file
# ============================================================================


def test_update_vor_cache_seed_skips_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``_seed_station_ids_from_file`` reads
    ``data/vor_station_ids_wien.txt`` via
    ``station_file.read_text(encoding="utf-8")`` with NO size cap. A
    planted huge file at the seed path raises ``MemoryError`` at
    startup of the daily VOR cache update.

    Post-fix: ``read_capped_text`` returns ``None`` and the function
    silently returns (no env var seeded)."""
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import update_vor_cache
    finally:
        sys.path.pop(0)

    poisoned = tmp_path / "vor_station_ids_wien.txt"
    _write_oversized_text(poisoned, 4096)

    # Patch the resolved path to point to our poisoned fixture
    fake_repo_root = tmp_path
    fake_data = fake_repo_root / "data"
    fake_data.mkdir(exist_ok=True)
    seed_file = fake_data / "vor_station_ids_wien.txt"
    _write_oversized_text(seed_file, 4096)

    monkeypatch.setattr(update_vor_cache, "REPO_ROOT", fake_repo_root)
    monkeypatch.setattr(update_vor_cache, "MAX_VOR_STATION_IDS_FILE_BYTES", 1024)
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)

    # Mock ``vor_station_ids`` to return empty so the function falls
    # through to the file read branch
    with patch("src.utils.stations.vor_station_ids", return_value=()):
        update_vor_cache._seed_station_ids_from_file()

    # Oversized file → no env var seeded
    assert "VOR_STATION_IDS" not in os.environ


def test_update_vor_cache_seed_accepts_normal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: normal seed files still populate the env var."""
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import update_vor_cache
    finally:
        sys.path.pop(0)

    fake_repo_root = tmp_path
    fake_data = fake_repo_root / "data"
    fake_data.mkdir(exist_ok=True)
    seed_file = fake_data / "vor_station_ids_wien.txt"
    seed_file.write_text("123\n456\n789\n", encoding="utf-8")

    monkeypatch.setattr(update_vor_cache, "REPO_ROOT", fake_repo_root)
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)

    with patch("src.utils.stations.vor_station_ids", return_value=()):
        update_vor_cache._seed_station_ids_from_file()

    assert os.environ.get("VOR_STATION_IDS") == "123,456,789"
    # Cleanup
    monkeypatch.delenv("VOR_STATION_IDS", raising=False)


# ============================================================================
# scripts/update_vor_stations.py — _read_station_ids_from_file
# ============================================================================


def test_update_vor_stations_read_ids_rejects_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``_read_station_ids_from_file`` reads operator-supplied
    station ID files via ``path.read_text(encoding="utf-8")`` with NO
    size cap.

    Post-fix: ``read_capped_text`` returns ``None`` and the function
    returns ``[]``."""
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import update_vor_stations
    finally:
        sys.path.pop(0)

    poisoned = tmp_path / "ids.csv"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(update_vor_stations, "MAX_VOR_STATION_IDS_FILE_BYTES", 1024)

    result = update_vor_stations._read_station_ids_from_file(poisoned)
    assert result == []


def test_update_vor_stations_read_ids_accepts_normal(tmp_path: Path) -> None:
    """Regression: normal CSV files still parse correctly."""
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import update_vor_stations
    finally:
        sys.path.pop(0)

    ids_file = tmp_path / "ids.csv"
    ids_file.write_text("123,456\n789\n", encoding="utf-8")

    result = update_vor_stations._read_station_ids_from_file(ids_file)
    assert sorted(result) == ["123", "456", "789"]


# ============================================================================
# scripts/fetch_google_places_stations.py — _write_if_changed
# ============================================================================


def test_fetch_google_places_write_if_changed_rejects_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix: ``_write_if_changed`` reads the existing stations.json
    via ``path.read_text(encoding="utf-8")`` with NO size cap before
    deciding whether to skip the write. A planted huge file at the
    target path raises ``MemoryError`` past the surrounding handler
    and crashes the cron pipeline.

    Post-fix: ``read_capped_text`` returns ``None`` and the function
    proceeds to overwrite (treats the oversized file as "different",
    which is the safe default — overwrite poisoned state with the
    freshly-fetched stations)."""
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import fetch_google_places_stations as fgps
    finally:
        sys.path.pop(0)

    poisoned = tmp_path / "stations.json"
    _write_oversized_text(poisoned, 4096)

    monkeypatch.setattr(fgps, "MAX_STATIONS_FILE_BYTES", 1024)

    # Run the function — pre-fix would MemoryError; post-fix should
    # overwrite the poisoned file
    fgps._write_if_changed(poisoned, [{"name": "Test Stop"}])

    # The poisoned file has been overwritten with valid JSON
    import json as _json
    after = _json.loads(poisoned.read_text(encoding="utf-8"))
    assert after == {"stations": [{"name": "Test Stop"}]}


# ============================================================================
# Inventory: every module exposes its expected MAX_* constant
# ============================================================================


def test_round7_inventory_constants() -> None:
    """Pin the per-loader cap constants across all Round 7 modules. A
    future refactor that drops or renames any of these would silently
    pass the regression tests above on unfixed code — so we pin the
    inventory here too."""
    from src.utils import env, secret_scanner

    assert env.MAX_ENV_FILE_BYTES > 0
    assert env.MAX_SECRET_FILE_BYTES > 0
    assert secret_scanner.MAX_IGNORE_FILE_BYTES > 0
    assert secret_scanner.MAX_SCAN_FILE_BYTES > 0

    import sys
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from scripts import (
            check_complexity,
            fetch_google_places_stations,
            update_vor_cache,
            update_vor_stations,
        )

        assert check_complexity.MAX_BASELINE_FILE_BYTES > 0
        assert fetch_google_places_stations.MAX_STATIONS_FILE_BYTES > 0
        assert update_vor_cache.MAX_VOR_STATION_IDS_FILE_BYTES > 0
        assert update_vor_stations.MAX_VOR_STATION_IDS_FILE_BYTES > 0
    finally:
        sys.path.pop(0)
