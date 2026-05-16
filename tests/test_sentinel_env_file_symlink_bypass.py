"""Sentinel PoC: ``WIEN_OEPNV_ENV_FILES`` symlink-following bypass.

Pre-fix shape (``src/utils/env.py:_default_env_file_candidates``)::

    resolved_candidate = Path(os.path.abspath(candidate))
    try:
        resolved_candidate.relative_to(base_dir)
    except ValueError:
        # Disallow bypassing base_dir with absolute paths or ../
        continue

``os.path.abspath`` ONLY normalises a path lexically (``.``/``..`` parts
joined against ``os.getcwd()``); it does NOT follow symlinks. So a symlink
planted inside ``base_dir`` pointing OUTSIDE ``base_dir`` (e.g.
``<base_dir>/decoy.env -> /etc/environment``) silently passed the
``relative_to(base_dir)`` containment check — the lexical path is still
inside ``base_dir`` even though the filesystem destination is not.

End-to-end the exploit chain reads as follows:

    1. Attacker plants ``<base_dir>/decoy.env`` as a symlink to any
       readable file outside ``base_dir`` (e.g. ``/etc/environment``,
       ``~/.aws/credentials``, ``/proc/self/environ``).
    2. Attacker sets ``WIEN_OEPNV_ENV_FILES=decoy.env`` via a leaked
       CI env / compromised secret store / hostile-fork CI run.
    3. ``load_default_env_files()`` calls ``_default_env_file_candidates``;
       the lexical check passes; ``load_env_file(decoy.env)`` opens the
       symlink and follows it to the outside target.
    4. ``read_capped_text`` reads the target's bytes (capped at
       ``MAX_ENV_FILE_BYTES``).
    5. ``_parse_env_file`` parses every ``KEY=VALUE`` line and the loader
       writes each variable into ``os.environ`` (when ``KEY`` is not
       already set, the ``override=False`` default).

Threat model
============

The two preconditions (filesystem planting + env var injection) are
sometimes obtainable independently:

  * A hostile PR submitted against the repository can include a symlink
    in the changeset (Git tracks symlinks as files with the ``120000``
    mode); merging it lands the symlink in ``main``.
  * A leaked CI secret / compromised secret store / misconfigured
    development environment can set ``WIEN_OEPNV_ENV_FILES`` after the
    symlink is already in the tree.
  * Multi-tenant CI: a different tenant's job leaves a symlink in a
    shared workspace; the next run inherits it.

The downstream impact ranges from passive disclosure (env vars from
``/etc/environment`` leaked into log lines that include the loaded set)
to active poisoning of the process state — proxy variables
(``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` if not already set),
provider URL overrides (constrained but still attack-surface widening),
log-level toggles. The ``override=False`` default reduces the worst case
(existing env vars are not clobbered), but the variables an attacker can
inject still gain trust as "operator-provided env" in every downstream
consumer that reads ``os.environ`` after startup.

Fix shape
=========

Replace ``Path(os.path.abspath(candidate))`` with
``candidate.resolve(strict=False)`` so the actual filesystem destination
(post-symlink-resolution) is what gets checked against ``base_dir``
containment. Mirrors the canonical containment shape pinned by:

  * :func:`src.utils.env.read_secret` for the systemd / Docker secret
    sub-trees (already uses ``Path.resolve()``).
  * :func:`src.feed.config.validate_path` for the ``OUT_PATH`` /
    ``STATE_PATH`` / ``LOG_DIR`` env-controlled boundaries (already
    uses ``Path.resolve()``).

The fix also catches ``OSError`` / ``RuntimeError`` so symlink loops
and I/O errors fail closed (skip the candidate) — the secure default.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.utils import env as env_utils


@pytest.fixture
def tmp_base(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Set up an isolated base_dir + an outside-base sibling target dir."""
    base = tmp_path / "repo_root"
    base.mkdir()
    outside = tmp_path / "outside_repo"
    outside.mkdir()
    yield base, outside


# ----------------------------------------------------------------------
# PoC 1: Symlink directly inside base_dir pointing outside
# ----------------------------------------------------------------------


def test_symlink_in_base_pointing_outside_is_rejected(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix this test FAILS: the symlink target appears in candidates.

    Post-fix the resolved path falls outside base_dir → reject.
    """
    base, outside = tmp_base

    secret_target = outside / "stolen.env"
    secret_target.write_text("STOLEN_KEY=stolen_value\n", encoding="utf-8")

    evil_link = base / "decoy.env"
    evil_link.symlink_to(secret_target)

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "decoy.env")

    candidates = list(env_utils._default_env_file_candidates(base))

    for cand in candidates:
        if cand.name == "decoy.env":
            pytest.fail(
                f"Symlink-following bypass: candidate {cand} accepted "
                f"but its resolved target {cand.resolve()} is outside "
                f"base_dir {base}"
            )
        try:
            cand.resolve(strict=False).relative_to(base.resolve())
        except ValueError:
            pytest.fail(
                f"Candidate {cand} resolves outside base_dir "
                f"({cand.resolve()})"
            )


def test_symlink_load_end_to_end_does_not_leak_outside(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end PoC: ``os.environ`` is NOT poisoned via a symlink leak."""
    base, outside = tmp_base

    secret_target = outside / "stolen.env"
    secret_target.write_text(
        "SENTINEL_SYMLINK_BYPASS_LEAK=hostile_value\n", encoding="utf-8"
    )

    evil_link = base / "decoy.env"
    evil_link.symlink_to(secret_target)

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "decoy.env")
    monkeypatch.delenv("SENTINEL_SYMLINK_BYPASS_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "SENTINEL_SYMLINK_BYPASS_LEAK" not in os.environ, (
        "Symlink-bypass leak: outside-base file's KEY=VALUE was loaded "
        "into os.environ via WIEN_OEPNV_ENV_FILES symlink. Pre-fix "
        "os.path.abspath does not follow symlinks; post-fix "
        "Path.resolve() catches the bypass at the containment check."
    )


# ----------------------------------------------------------------------
# PoC 2: Nested-symlink chain (link → link → outside)
# ----------------------------------------------------------------------


def test_nested_symlink_chain_to_outside_is_rejected(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-hop symlink chain (link → link → outside) must be rejected.

    Validates that ``.resolve()`` follows the full chain, not just one
    hop, before the containment check runs.
    """
    base, outside = tmp_base

    real_target = outside / "deep.env"
    real_target.write_text("CHAIN_LEAK=hostile\n", encoding="utf-8")

    mid_link = base / "mid_link.env"
    mid_link.symlink_to(real_target)

    front_link = base / "front_link.env"
    front_link.symlink_to(mid_link)

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "front_link.env")
    monkeypatch.delenv("CHAIN_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "CHAIN_LEAK" not in os.environ, (
        "Multi-hop symlink chain leaked via WIEN_OEPNV_ENV_FILES — "
        "Path.resolve() must traverse the full chain before the "
        "containment check."
    )


# ----------------------------------------------------------------------
# PoC 3: Symlink loop (defense-in-depth — must fail closed)
# ----------------------------------------------------------------------


def test_symlink_loop_fails_closed(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink loops must be rejected, not crash the loader."""
    base, _outside = tmp_base

    loop_a = base / "loop_a.env"
    loop_b = base / "loop_b.env"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "loop_a.env")

    # Must not raise; must skip the loop candidate.
    candidates = list(env_utils._default_env_file_candidates(base))
    assert not any(
        c.name in {"loop_a.env", "loop_b.env"} for c in candidates
    ), f"Symlink loop candidate retained in {candidates}"


# ----------------------------------------------------------------------
# PoC 4: Pathsep-separated list with mix of legitimate + symlink-bypass
# ----------------------------------------------------------------------


def test_pathsep_mix_rejects_only_bypassing_entry(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a mixed list still accepts the legitimate (non-symlink) entry."""
    base, outside = tmp_base

    legit = base / "legit.env"
    legit.write_text("LEGIT_KEY=ok\n", encoding="utf-8")

    target = outside / "stolen.env"
    target.write_text("HOSTILE_KEY=pwned\n", encoding="utf-8")
    evil = base / "decoy.env"
    evil.symlink_to(target)

    raw = f"legit.env{os.pathsep}decoy.env"
    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", raw)
    monkeypatch.delenv("LEGIT_KEY", raising=False)
    monkeypatch.delenv("HOSTILE_KEY", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert os.environ.get("LEGIT_KEY") == "ok", (
        "Legitimate non-symlink env file should still load."
    )
    assert "HOSTILE_KEY" not in os.environ, (
        "Symlink bypass must be rejected even when mixed with legitimate "
        "candidates."
    )


# ----------------------------------------------------------------------
# PoC 5: Path-traversal regressions (must continue to be rejected)
# ----------------------------------------------------------------------


def test_absolute_outside_path_rejected_regression(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: absolute path outside base_dir is still rejected."""
    base, outside = tmp_base
    target = outside / "ext.env"
    target.write_text("ABSOLUTE_LEAK=hostile\n", encoding="utf-8")

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", str(target))
    monkeypatch.delenv("ABSOLUTE_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "ABSOLUTE_LEAK" not in os.environ


def test_parent_traversal_rejected_regression(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``../`` traversal is still rejected."""
    base, outside = tmp_base
    target = outside / "traverse.env"
    target.write_text("TRAVERSE_LEAK=hostile\n", encoding="utf-8")

    rel_traversal = os.path.relpath(target, start=base)
    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", rel_traversal)
    monkeypatch.delenv("TRAVERSE_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "TRAVERSE_LEAK" not in os.environ


# ----------------------------------------------------------------------
# PoC 6: Legitimate path (no symlink) still works (no behaviour regression)
# ----------------------------------------------------------------------


def test_legitimate_inbase_path_accepted(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a regular file inside base_dir is still accepted."""
    base, _outside = tmp_base

    legit = base / "extra.env"
    legit.write_text("INBASE_KEY=accepted\n", encoding="utf-8")

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "extra.env")
    monkeypatch.delenv("INBASE_KEY", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert os.environ.get("INBASE_KEY") == "accepted"


def test_legitimate_symlink_staying_inside_base_accepted(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink whose target stays INSIDE base_dir should still be accepted.

    Validates that the fix doesn't over-correct: legitimate symlinks
    that stay within the project tree (e.g. ``.env`` linked from a
    sibling directory) continue to work.
    """
    base, _outside = tmp_base

    real = base / "real_target.env"
    real.write_text("INSIDE_SYMLINK_KEY=accepted\n", encoding="utf-8")

    link = base / "alias.env"
    link.symlink_to(real)

    monkeypatch.setenv("WIEN_OEPNV_ENV_FILES", "alias.env")
    monkeypatch.delenv("INSIDE_SYMLINK_KEY", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert os.environ.get("INSIDE_SYMLINK_KEY") == "accepted"


# ----------------------------------------------------------------------
# PoC 7: Structural / source invariant — abspath -> resolve replacement
# ----------------------------------------------------------------------


def test_source_uses_resolve_not_abspath() -> None:
    """Pin the fix structurally: the symlink-aware containment helper
    must call ``Path.resolve`` and must NOT call ``os.path.abspath``
    (the canonical pre-fix shape). Mirrors the audit-walker invariants
    used by other Sentinel rounds — any future refactor that
    re-introduces an ``os.path.abspath(...)`` call at this site fails
    the test on the first ``pytest`` run. The check matches the
    function-CALL token (``os.path.abspath(``) so the prose explanation
    in the security comment (which names the pre-fix shape) is not
    counted as a regression.

    The canonical containment defence now lives in
    :func:`src.utils.env._resolve_within_base` (extracted during the
    default-candidate sibling-drift closure so the env-controlled and
    default-candidate branches share a single defence). The structural
    invariant follows the helper:
    """
    import inspect

    helper_source = inspect.getsource(env_utils._resolve_within_base)
    assert "os.path.abspath(" not in helper_source, (
        "_resolve_within_base must use Path.resolve(), not "
        "os.path.abspath(), so symlinks are followed before the "
        "containment check."
    )
    assert ".resolve(" in helper_source, (
        "_resolve_within_base must call Path.resolve() to follow "
        "symlinks at the candidate boundary."
    )

    candidates_source = inspect.getsource(env_utils._default_env_file_candidates)
    assert "os.path.abspath(" not in candidates_source, (
        "_default_env_file_candidates must not re-introduce os.path.abspath; "
        "the defence is supplied via _resolve_within_base."
    )
    assert "_resolve_within_base" in candidates_source, (
        "_default_env_file_candidates must route every candidate through "
        "_resolve_within_base so the symlink-aware containment defence "
        "applies uniformly to defaults and env-controlled extras."
    )
