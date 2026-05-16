"""Sentinel PoC: default env-file candidate symlink-following bypass.

Sibling drift of :file:`tests/test_sentinel_env_file_symlink_bypass.py` (which
closed the ``WIEN_OEPNV_ENV_FILES`` env-controlled symlink bypass): every
*built-in default* env-file candidate
(``<base_dir>/.env``, ``<base_dir>/data/secrets.env``,
``<base_dir>/config/secrets.env``) was kept as a bare lexical
``base_dir / "..."`` path. The lexical form does NOT follow symlinks, so a
symlink planted at any of the three default paths pointing OUTSIDE
``base_dir`` (e.g. ``<base_dir>/.env -> /etc/environment``,
``<base_dir>/data/secrets.env -> ~/.aws/credentials``) silently passed the
"file inside the project tree" boundary by construction — the path string
``<base_dir>/.env`` is lexically inside ``base_dir`` even when the symlink
target lies far outside.

End-to-end the exploit chain reads as follows:

    1. Attacker plants a symlink at one of the three default candidate
       locations pointing to any readable file outside ``base_dir``
       (e.g. ``/etc/environment``, ``~/.aws/credentials``,
       ``/proc/self/environ``). Git tracks symlinks as files with the
       ``120000`` mode, so a hostile PR can land the symlink in ``main``.
    2. ``load_default_env_files()`` calls ``_default_env_file_candidates``;
       pre-fix the helper appends the literal ``base_dir / "..."`` Path
       object for each default. ``load_env_file(<symlink>)`` opens the
       symlink (``path.exists()`` and ``path.is_file()`` both follow
       symlinks) and ``read_capped_text`` reads the target's bytes via
       ``path.open("rb")``.
    3. ``_parse_env_file`` parses every ``KEY=VALUE`` line in the
       target's content (``/etc/environment`` is exactly that shape).
    4. The loader writes each parsed variable into ``os.environ`` (when
       ``KEY`` is not already set — the ``override=False`` default).

Threat model
============

Difference from the ``WIEN_OEPNV_ENV_FILES`` round: that round needed
TWO preconditions (filesystem-planting **plus** env-var control). This
round needs only **one** precondition — filesystem write to any of the
three documented default candidate paths. The single-precondition shape
is meaningfully different:

  * A hostile PR submitted against the repository can include a symlink
    at any of the default paths. Git tracks symlinks as files with the
    ``120000`` mode; merging the PR lands the symlink in ``main`` and
    every subsequent cron tick re-materialises the link on the runner.
    Code review may not flag a new symlink as suspicious unless reviewers
    specifically inspect the mode bits.
  * A pre-compromised CI runner (or a multi-tenant shared workspace
    where a different tenant's job leaves a symlink at the documented
    default path) lets every later cron job inherit the planted state.
  * The default paths are PUBLIC knowledge — they are documented in
    ``src/utils/env.py`` and referenced in the project README — so an
    attacker does not need any environment-variable side-channel to
    pick where to plant.

Blast radius is identical to the env-controlled round: ``KEY=VALUE``
pairs from any readable file flow into ``os.environ``, where downstream
consumers (proxy variables, provider URL overrides not pinned to a
canonical allowlist, log-level toggles, every future env-driven config
option) treat the injected vars as trusted operator input.

Fix shape
=========

The fix unifies the defence into a single
:func:`src.utils.env._resolve_within_base` helper that applies
``Path.resolve(strict=False)`` and the ``relative_to(base_dir)``
containment check to **every** candidate path — built-in defaults AND
``WIEN_OEPNV_ENV_FILES`` extras alike. The pre-existing
``WIEN_OEPNV_ENV_FILES`` defence is now routed through the same helper
so the two branches cannot drift in the future. Mirrors the canonical
containment shape pinned by :func:`src.utils.env.read_secret` for the
systemd / Docker secret sub-trees and
:func:`src.feed.config.validate_path` for the ``OUT_PATH`` /
``STATE_PATH`` / ``LOG_DIR`` env-controlled boundaries.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.utils import env as env_utils


@pytest.fixture
def tmp_base(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Set up an isolated base_dir with the project's directory shape +
    an outside-base sibling target dir.
    """
    base = (tmp_path / "repo_root").resolve()
    base.mkdir()
    (base / "data").mkdir()
    (base / "config").mkdir()
    outside = (tmp_path / "outside_repo").resolve()
    outside.mkdir()
    yield base, outside


# ----------------------------------------------------------------------
# PoC 1: Each default candidate path with a symlink to outside is rejected
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate_rel",
    [".env", "data/secrets.env", "config/secrets.env"],
)
def test_default_candidate_symlink_to_outside_is_rejected(
    tmp_base: tuple[Path, Path],
    candidate_rel: str,
) -> None:
    """Pre-fix this test FAILS: the symlink at a default candidate path is
    accepted even though its target lies outside ``base_dir``.

    Post-fix every returned candidate must resolve to a path inside
    ``base_dir`` (or be skipped entirely).
    """
    base, outside = tmp_base

    secret_target = outside / "stolen.env"
    secret_target.write_text(
        "SENTINEL_DEFAULT_OUTSIDE_LEAK=stolen_value\n", encoding="utf-8"
    )

    evil_link = base / candidate_rel
    evil_link.parent.mkdir(parents=True, exist_ok=True)
    evil_link.symlink_to(secret_target)

    candidates = list(env_utils._default_env_file_candidates(base))

    for cand in candidates:
        resolved = cand.resolve(strict=False)
        try:
            resolved.relative_to(base)
        except ValueError:
            pytest.fail(
                f"Default-candidate symlink bypass: candidate {cand} "
                f"resolves to {resolved}, which is outside base_dir {base}"
            )


@pytest.mark.parametrize(
    "candidate_rel",
    [".env", "data/secrets.env", "config/secrets.env"],
)
def test_default_candidate_symlink_end_to_end_does_not_leak(
    tmp_base: tuple[Path, Path],
    candidate_rel: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end PoC: ``os.environ`` is NOT poisoned via a default-candidate
    symlink leak. Pre-fix the planted KEY=VALUE lands in ``os.environ``;
    post-fix the candidate is skipped at the containment check.
    """
    base, outside = tmp_base

    secret_target = outside / "stolen.env"
    secret_target.write_text(
        "SENTINEL_DEFAULT_E2E_LEAK=hostile_value\n", encoding="utf-8"
    )

    evil_link = base / candidate_rel
    evil_link.parent.mkdir(parents=True, exist_ok=True)
    evil_link.symlink_to(secret_target)

    # Ensure WIEN_OEPNV_ENV_FILES is NOT set — this PoC must trigger via
    # the default-candidate branch, not the env-controlled branch.
    monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)
    monkeypatch.delenv("SENTINEL_DEFAULT_E2E_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "SENTINEL_DEFAULT_E2E_LEAK" not in os.environ, (
        f"Default-candidate symlink bypass: KEY=VALUE from {secret_target} "
        f"leaked into os.environ via the symlink at {evil_link}. Pre-fix "
        f"the bare lexical Path object opened the symlink and followed it "
        f"to the outside target; post-fix Path.resolve() catches the bypass "
        f"at the containment check inside _resolve_within_base."
    )


# ----------------------------------------------------------------------
# PoC 2: Nested-symlink chain at a default candidate path
# ----------------------------------------------------------------------


def test_default_candidate_nested_symlink_chain_rejected(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-hop symlink chain (default -> intermediate -> outside) must
    be rejected. Validates ``.resolve()`` follows the full chain before
    the containment check runs.
    """
    base, outside = tmp_base

    real_target = outside / "deep_leak.env"
    real_target.write_text(
        "SENTINEL_DEFAULT_CHAIN_LEAK=hostile\n", encoding="utf-8"
    )

    mid_link = base / "mid_link.env"
    mid_link.symlink_to(real_target)

    # ``.env`` -> ``mid_link.env`` -> ``outside/deep_leak.env``
    front_link = base / ".env"
    front_link.symlink_to(mid_link)

    monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)
    monkeypatch.delenv("SENTINEL_DEFAULT_CHAIN_LEAK", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert "SENTINEL_DEFAULT_CHAIN_LEAK" not in os.environ, (
        "Multi-hop symlink chain at the default candidate path leaked — "
        "Path.resolve() must traverse the full chain before the "
        "containment check."
    )


# ----------------------------------------------------------------------
# PoC 3: Symlink loop at a default candidate path fails closed
# ----------------------------------------------------------------------


def test_default_candidate_symlink_loop_fails_closed(
    tmp_base: tuple[Path, Path],
) -> None:
    """A symlink loop at default candidate paths must be skipped, not
    crash the loader. ``Path.resolve(strict=False)`` raises ``OSError``
    (``ELOOP``) or ``RuntimeError`` (CPython infinite-loop guard) on a
    loop; the helper catches both and returns ``None``.
    """
    base, _outside = tmp_base
    loop_a = base / "data" / "secrets.env"
    loop_b = base / "config" / "secrets.env"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    # Must not raise; the loop candidates must be filtered out.
    candidates = list(env_utils._default_env_file_candidates(base))
    for cand in candidates:
        try:
            resolved = cand.resolve(strict=False)
        except (OSError, RuntimeError):
            pytest.fail(
                f"Symlink-loop candidate {cand} retained in candidate list "
                f"and crashed downstream resolve()."
            )
        try:
            resolved.relative_to(base)
        except ValueError:
            pytest.fail(
                f"Symlink-loop candidate {cand} resolved to {resolved}, "
                f"outside base_dir {base}"
            )


# ----------------------------------------------------------------------
# PoC 4: Legitimate symlink staying INSIDE base_dir is accepted
#         (no over-correction regression)
# ----------------------------------------------------------------------


def test_default_candidate_symlink_inside_base_accepted(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink at a default path whose target stays INSIDE ``base_dir``
    should still be accepted. Validates the fix does NOT over-correct
    against legitimate symlink usage (e.g. ``.env`` aliased to a sibling
    file inside the project tree).
    """
    base, _outside = tmp_base

    real = base / "actual_settings.env"
    real.write_text(
        "SENTINEL_DEFAULT_INSIDE_KEY=accepted\n", encoding="utf-8"
    )

    link = base / ".env"
    link.symlink_to(real)

    monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)
    monkeypatch.delenv("SENTINEL_DEFAULT_INSIDE_KEY", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert os.environ.get("SENTINEL_DEFAULT_INSIDE_KEY") == "accepted", (
        "Legitimate inside-base symlink at a default candidate path must "
        "still load: the fix should not over-correct against valid "
        "symlink usage."
    )


# ----------------------------------------------------------------------
# PoC 5: Regular (non-symlink) default file still loads
# ----------------------------------------------------------------------


def test_default_candidate_regular_file_still_loads(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a regular file at a default candidate path still works."""
    base, _outside = tmp_base
    legit = base / ".env"
    legit.write_text(
        "SENTINEL_DEFAULT_REGULAR_KEY=regular_value\n", encoding="utf-8"
    )

    monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)
    monkeypatch.delenv("SENTINEL_DEFAULT_REGULAR_KEY", raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    assert os.environ.get("SENTINEL_DEFAULT_REGULAR_KEY") == "regular_value"


# ----------------------------------------------------------------------
# PoC 6: All three default candidates simultaneously symlinked outside
# ----------------------------------------------------------------------


def test_all_three_defaults_simultaneously_symlinked_outside(
    tmp_base: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three documented default paths planted as outside-base symlinks
    in a single run — no key from any of them lands in ``os.environ``.
    """
    base, outside = tmp_base

    targets = {
        ".env": outside / "leak_env.env",
        "data/secrets.env": outside / "leak_data_secrets.env",
        "config/secrets.env": outside / "leak_config_secrets.env",
    }
    # Each outside target carries a unique sentinel key so the assertion
    # below can identify which planted path leaked.
    sentinel_keys = {
        ".env": "SENTINEL_DEFAULT_TRIPLE_DOT_ENV_LEAK",
        "data/secrets.env": "SENTINEL_DEFAULT_TRIPLE_DATA_SECRETS_LEAK",
        "config/secrets.env": "SENTINEL_DEFAULT_TRIPLE_CONFIG_SECRETS_LEAK",
    }
    for rel, target_path in targets.items():
        target_path.write_text(
            f"{sentinel_keys[rel]}=hostile_{rel.replace('/', '_')}\n",
            encoding="utf-8",
        )
        link_path = base / rel
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(target_path)

    monkeypatch.delenv("WIEN_OEPNV_ENV_FILES", raising=False)
    for key in sentinel_keys.values():
        monkeypatch.delenv(key, raising=False)

    for cand in env_utils._default_env_file_candidates(base):
        env_utils.load_env_file(cand)

    leaked = [key for key in sentinel_keys.values() if key in os.environ]
    assert not leaked, (
        f"Multiple default candidates leaked simultaneously: {leaked}. "
        f"Each of the three default paths must be filtered by "
        f"_resolve_within_base."
    )


# ----------------------------------------------------------------------
# PoC 7: Helper invariants — the canonical resolve() shape is pinned
# ----------------------------------------------------------------------


def test_resolve_within_base_helper_uses_resolve_not_abspath() -> None:
    """Pin the fix structurally: the helper body must call
    ``Path.resolve`` and must NOT call ``os.path.abspath``. Mirrors the
    audit-walker invariants used by other Sentinel rounds — any future
    refactor that re-introduces a lexical-only normalisation step at
    this boundary fails the test on the first ``pytest`` run. Mirrors
    the parallel pin for the env-controlled branch in
    :file:`tests/test_sentinel_env_file_symlink_bypass.py`.
    """
    source = inspect.getsource(env_utils._resolve_within_base)
    assert ".resolve(" in source, (
        "_resolve_within_base must call Path.resolve() to follow "
        "symlinks before the containment check."
    )
    assert "os.path.abspath(" not in source, (
        "_resolve_within_base must NOT use os.path.abspath — that "
        "primitive only normalises paths lexically and does not "
        "follow symlinks."
    )
    assert "relative_to(" in source, (
        "_resolve_within_base must include the relative_to "
        "containment check post-resolve."
    )


def test_default_env_file_candidates_routes_through_resolver() -> None:
    """Pin that the default-candidate branch is not bypassed: the body
    of ``_default_env_file_candidates`` must invoke
    ``_resolve_within_base`` (the canonical helper) rather than keeping
    a parallel inline check that could drift over time.
    """
    source = inspect.getsource(env_utils._default_env_file_candidates)
    assert "_resolve_within_base" in source, (
        "_default_env_file_candidates must route every candidate through "
        "_resolve_within_base so the symlink-aware containment defence "
        "applies uniformly to defaults and env-controlled extras."
    )


def test_resolve_within_base_returns_none_on_outside_target(
    tmp_base: tuple[Path, Path],
) -> None:
    """Direct unit-test of the helper: an outside-base resolved path
    must yield ``None``.
    """
    base, outside = tmp_base
    candidate = outside / "anywhere.env"
    result = env_utils._resolve_within_base(candidate, base)
    assert result is None, (
        f"_resolve_within_base must return None for outside-base targets; "
        f"got {result!r}"
    )


def test_resolve_within_base_returns_path_on_inside_target(
    tmp_base: tuple[Path, Path],
) -> None:
    """Direct unit-test of the helper: an inside-base resolved path
    must round-trip to itself.
    """
    base, _outside = tmp_base
    candidate = base / "settings.env"
    result = env_utils._resolve_within_base(candidate, base)
    assert result is not None, (
        "_resolve_within_base must accept inside-base candidates."
    )
    # Must resolve to inside base.
    result.relative_to(base)


def test_resolve_within_base_on_symlink_loop_returns_none(
    tmp_base: tuple[Path, Path],
) -> None:
    """Direct unit-test of the helper: a symlink loop must return ``None``
    via the ``(OSError, RuntimeError)`` fail-closed branch.
    """
    base, _outside = tmp_base
    loop_a = base / "loop_a.env"
    loop_b = base / "loop_b.env"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    result = env_utils._resolve_within_base(loop_a, base)
    assert result is None, (
        f"_resolve_within_base must return None on symlink loop; got {result!r}"
    )
