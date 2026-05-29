import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import patch
from src.utils import cache as cache_module
from src.utils.cache import write_cache, DataDegradationError

def test_cache_degradation_guard_bypass_on_new_cache(tmp_path: Path) -> None:
    """Verify that writing to a non-existent cache works normally."""
    provider = "test_new"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        mock_cache_file.return_value = target_file

        items = [{"id": 1}]
        write_cache(provider, items)

        assert target_file.exists()
        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 1

def test_cache_degradation_guard_bypass_on_empty_existing(tmp_path: Path) -> None:
    """Verify that writing empty items to an empty existing cache works."""
    provider = "test_empty_existing"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([], f)

        mock_cache_file.return_value = target_file

        items: list[Any] = []
        write_cache(provider, items)

        assert target_file.exists()
        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 0

def test_cache_degradation_guard_raises_on_empty_payload(tmp_path: Path) -> None:
    """Verify that writing empty items to a populated cache raises DataDegradationError."""
    provider = "test_empty_payload"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(10)], f)

        mock_cache_file.return_value = target_file

        items: list[Any] = []
        with pytest.raises(DataDegradationError, match="Empty payload rejected"):
            write_cache(provider, items)

def test_cache_degradation_guard_raises_on_drastic_drop(tmp_path: Path) -> None:
    """Verify that writing drastically fewer items raises DataDegradationError."""
    provider = "test_drastic_drop"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(100)], f)

        mock_cache_file.return_value = target_file

        # Drop is > 80%, so anything < 20 items should trigger the error
        items = [{"id": i} for i in range(19)]
        with pytest.raises(DataDegradationError, match="Degraded payload rejected"):
            write_cache(provider, items)

def test_cache_degradation_guard_bypass_on_slight_drop(tmp_path: Path) -> None:
    """Verify that a drop < 80% bypasses the degradation guard."""
    provider = "test_slight_drop"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            json.dump([{"id": i} for i in range(100)], f)

        mock_cache_file.return_value = target_file

        # 50 items is a 50% drop, which is acceptable
        items = [{"id": i} for i in range(50)]
        write_cache(provider, items)

        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 50

def test_cache_degradation_guard_bypass_on_corrupt_cache(tmp_path: Path) -> None:
    """Verify that if the existing cache is corrupt, it's bypassed and overwritten."""
    provider = "test_corrupt"
    cache_dir = tmp_path / "cache"

    with patch("src.utils.cache._cache_file") as mock_cache_file:
        target_file = cache_dir / provider / "events.json"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("w", encoding="utf-8") as f:
            f.write("invalid json")

        mock_cache_file.return_value = target_file

        items = [{"id": 1}]
        write_cache(provider, items)

        with target_file.open("r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert len(saved_data) == 1


def test_scoped_prune_evicts_providers_own_stale_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scoped ``prune_cache(provider=...)`` must actually evict THIS
    provider's own ``events.json`` once it is older than ``max_age_hours``.

    Pre-fix the scoped branch joined the RAW provider name
    (``cache/alpha``) while every read/write path stores the provider under
    ``sanitize_filename(provider)`` (``cache/alpha_<hash>``). The raw
    directory never exists, so ``is_dir()`` failed and the scoped prune was
    a permanent no-op — a provider's own stale cache lived forever (silent
    repo bloat / indefinitely-stale data served past max_age).
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cache_module, "_CACHE_DIR", cache_dir)

    write_cache("alpha", [{"id": i} for i in range(100)])
    alpha_file = cache_module._cache_file("alpha")
    assert alpha_file.exists()
    # Age it past the 48 h cutoff.
    aged = time.time() - 49 * 3600
    os.utime(alpha_file, (aged, aged))

    cache_module.prune_cache(max_age_hours=48, provider="alpha")

    assert not alpha_file.exists(), (
        "scoped prune must evict alpha's own >max_age stale events.json "
        "(pre-fix it looked in the non-existent raw 'cache/alpha' dir)"
    )


def test_scoped_prune_keeps_providers_fresh_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safety guard: the scoped prune must NOT delete a freshly-written
    cache (the immediate post-write case), only one older than max_age."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cache_module, "_CACHE_DIR", cache_dir)

    write_cache("alpha", [{"id": i} for i in range(100)])
    alpha_file = cache_module._cache_file("alpha")
    assert alpha_file.exists()

    # Fresh mtime — the just-written file must survive the post-write prune.
    cache_module.prune_cache(max_age_hours=48, provider="alpha")
    assert alpha_file.exists()


def test_write_cache_does_not_prune_sibling_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful write for one provider must not prune another provider's
    stale cache out from under the data-degradation guard.

    Pre-fix, ``write_cache`` ran an unscoped ``prune_cache()`` that iterated
    every provider directory and deleted any ``events.json`` older than 48 h.
    A repo-wide sibling prune therefore destroyed the on-disk baseline the
    degradation guard depends on (``cache_file.exists()`` short-circuits to
    False), turning a future empty/sparse payload into a silent overwrite.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cache_module, "_CACHE_DIR", cache_dir)

    # Provider A: write a healthy cache, then artificially age its mtime
    # past the 48 h cutoff so a repo-wide prune would target it.
    write_cache("alpha", [{"id": i} for i in range(100)])
    alpha_file = cache_module._cache_file("alpha")
    assert alpha_file.exists()
    aged = time.time() - 49 * 3600
    os.utime(alpha_file, (aged, aged))

    # Provider B writes successfully. Pre-fix this deleted alpha's cache.
    write_cache("beta", [{"id": i} for i in range(50)])
    assert alpha_file.exists(), (
        "beta's write must not delete alpha's stale-but-still-protected cache"
    )

    # Alpha's degradation guard must still fire when its next upstream
    # response is empty. Pre-fix the guard silently accepted [] because
    # ``cache_file.exists()`` returned False after beta's prune.
    with pytest.raises(DataDegradationError):
        write_cache("alpha", [])
    # The guard raised before atomic_write, so alpha's cache stays intact.
    assert alpha_file.exists()
