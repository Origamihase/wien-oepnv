"""Regression tests for the EN-feed CI workflow dependencies.

The bug behind the partially-untranslated live feed on 2026-05-21 was
NOT in the Python code at all — it was a missing ``torch`` install in
the workflows that actually build the feed on the production cadence
(``update-cycle.yml`` every ~30 minutes; ``manual-full-refresh.yml``
on the operator's "alles neu" button). The transformers package was
installed (it ships in ``requirements.txt``), but without a torch
backend ``pipeline("translation_de_to_en", …)`` raises a
``RuntimeError: Models won't be available`` at construction time, the
audit fix's ``_translate_text_attempt`` returns ``None`` for every
item, and every disruption ends up flagged ``[Partially translated]``
with the German source verbatim.

The original 2026-05 multilingual-feed PR only patched
``build-feed.yml`` (the code-verification workflow). The two other
feed-producing workflows kept running without torch. This module
locks the torch/HF-cache contract into the workflows directly so a
future maintainer cannot remove either step without an explicit test
failure surfacing the regression.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Workflows that build the feed and therefore require both the
# torch CPU-only install AND the Hugging Face Hub cache. The third
# entry (``build-feed.yml``) was already patched by the original
# bilingual-feed PR; including it here pins the contract for ALL
# feed producers in one place.
_FEED_BUILD_WORKFLOWS = (
    "build-feed.yml",
    "update-cycle.yml",
    "manual-full-refresh.yml",
)


def _workflow_yaml(name: str) -> dict[str, object]:
    path = WORKFLOWS / name
    assert path.is_file(), f"workflow {name} missing"
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict), f"{name} did not parse to a dict"
    return loaded


def _iter_steps(workflow: dict[str, object]) -> list[dict[str, object]]:
    """Flatten ``jobs.*.steps`` so a single check can scan them all."""
    out: list[dict[str, object]] = []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return out
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict):
                out.append(step)
    return out


def _step_text(step: dict[str, object]) -> str:
    """Concatenate the searchable text of a step (name + run + uses +
    every value inside ``with:``). The ``with:`` payload carries the
    cache path / key, which the assertions below scan for."""
    parts: list[str] = []
    for key in ("name", "run", "uses"):
        value = step.get(key)
        if isinstance(value, str):
            parts.append(value)
    with_block = step.get("with")
    if isinstance(with_block, dict):
        for value in with_block.values():
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


@pytest.mark.parametrize("workflow_name", _FEED_BUILD_WORKFLOWS)
def test_feed_workflow_installs_torch_cpu_only(workflow_name: str) -> None:
    """Every feed-producing workflow must install the CPU-only PyTorch
    wheel from the official PyTorch index BEFORE the transformers
    pipeline is asked to translate. Without this step the live EN feed
    silently degrades to German under the ``[Partially translated]``
    marker."""
    workflow = _workflow_yaml(workflow_name)
    blob = "\n".join(_step_text(step) for step in _iter_steps(workflow))
    assert "pytorch.org/whl/cpu" in blob, (
        f"{workflow_name} does not install the CPU-only torch wheel; "
        f"the EN feed will degrade to '[Partially translated]' for every "
        f"item produced by this workflow. Add a step that runs "
        f"`pip install torch --index-url "
        f"https://download.pytorch.org/whl/cpu`."
    )
    assert "torch" in blob.lower(), (
        f"{workflow_name} mentions the PyTorch CPU index but the literal "
        f"``torch`` package is missing from the install command."
    )


@pytest.mark.parametrize("workflow_name", _FEED_BUILD_WORKFLOWS)
def test_feed_workflow_caches_huggingface_hub(workflow_name: str) -> None:
    """Every feed-producing workflow must cache ``~/.cache/huggingface``
    with a monthly-rotating key. Without the cache, every ~30-min
    cycle would re-download the ~300 MB Helsinki-NLP model and the
    first item processed before the download completes degrades to
    ``[Partially translated]``."""
    workflow = _workflow_yaml(workflow_name)
    steps = _iter_steps(workflow)
    blob = "\n".join(_step_text(step) for step in steps)
    assert "~/.cache/huggingface" in blob, (
        f"{workflow_name} does not cache ~/.cache/huggingface; the "
        f"Helsinki-NLP/opus-mt-de-en model would have to be downloaded "
        f"on every workflow run, which can race the first feed items "
        f"into the ``[Partially translated]`` fallback."
    )
    # The actions/cache step must be present and its key must include
    # a rotating component (``YYYY-MM``) — i.e. it must depend on a
    # ``date`` output. We check for the literal ``hf-cache-key`` step
    # id used in the three workflows for consistency.
    assert "hf-cache-key" in blob, (
        f"{workflow_name} caches huggingface but is missing the "
        f"monthly-rotating ``hf-cache-key`` step that drives the cache "
        f"key. Without monthly rotation the cache can never auto-pick-up "
        f"an upstream model bump."
    )


@pytest.mark.parametrize("workflow_name", _FEED_BUILD_WORKFLOWS)
def test_feed_workflow_runs_build_step(workflow_name: str) -> None:
    """Sanity guard: the workflow actually invokes ``feed build`` —
    so the previous two assertions are not vacuously true on a
    workflow that no longer builds the feed at all."""
    workflow = _workflow_yaml(workflow_name)
    blob = "\n".join(_step_text(step) for step in _iter_steps(workflow))
    assert "feed build" in blob, (
        f"{workflow_name} does not run ``feed build`` — if this is "
        f"intentional, remove the workflow from "
        f"``_FEED_BUILD_WORKFLOWS`` in this test module."
    )
