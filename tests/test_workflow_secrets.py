"""Repository secrets, the workflows that pass them and the code that reads them.

Operator decision 2026-10-16 (Michael): a secret that no longer reaches its
consumer must not go unnoticed. Until then the Google Places key had reached
no workflow since 2026-05-11, and two workflows passed ``VOR_VERSION`` /
``VOR_BASE``, which never existed as secrets; every missing value fell back
silently. These tests keep three lists in step:

* ``WORKFLOW_SECRETS`` in ``scripts/health_check.py``,
* the ``secrets.*`` references under ``.github/workflows``,
* the environment names the feed code reads.

The Health check (``check_secrets``) then turns red at run time when a secret
the feed pipeline needs arrives empty.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import health_check

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
# ``secrets.NAME`` but not ``steps.secrets.outputs`` (a step id called
# "secrets" in test-vor-api.yml).
_SECRET_REF = re.compile(r"(?<![\w.])secrets\.([A-Za-z_][A-Za-z0-9_]*)")
_FEED_KINDS = {"required", "optional"}


def _workflow_refs() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name in _SECRET_REF.findall(path.read_text(encoding="utf-8")):
            refs.setdefault(name, set()).add(path.name)
    return refs


def _registry() -> dict[str, health_check.WorkflowSecret]:
    return {s.name: s for s in health_check.WORKFLOW_SECRETS}


def test_registry_kinds_are_known_and_names_unique() -> None:
    names = [s.name for s in health_check.WORKFLOW_SECRETS]
    assert len(names) == len(set(names))
    for secret in health_check.WORKFLOW_SECRETS:
        assert secret.kind in {"required", "optional", "tooling"}, secret
        assert secret.purpose.strip(), secret


def test_every_workflow_secret_is_registered() -> None:
    """A workflow may only pass a secret that the registry knows.

    ``VOR_VERSION`` and ``VOR_BASE`` were passed for months although the
    repository never held them; this test fails on such a name.
    """
    unknown = {
        name: sorted(files)
        for name, files in _workflow_refs().items()
        if name not in _registry()
    }
    assert not unknown, (
        "Workflow references secrets missing from WORKFLOW_SECRETS in "
        f"scripts/health_check.py: {unknown}"
    )


def test_every_registered_secret_reaches_a_workflow() -> None:
    """A registered secret that no workflow passes is a dead entry.

    This is the Google Places case: the key was stored, the workflow that
    passed it was retired, and nothing noticed.
    """
    refs = _workflow_refs()
    dead = [name for name in _registry() if name not in refs]
    assert not dead, f"Registered secrets no workflow passes: {dead}"


def test_health_check_receives_exactly_the_feed_secrets() -> None:
    text = (WORKFLOWS / "health-check.yml").read_text(encoding="utf-8")
    passed = set(_SECRET_REF.findall(text))
    expected = {s.name for s in health_check.WORKFLOW_SECRETS if s.kind in _FEED_KINDS}
    assert passed == expected
    for name in expected:
        # Passed under its own name, so ``check_secrets`` finds it.
        assert f"{name}: ${{{{ secrets.{name} }}}}" in text


@pytest.mark.parametrize(
    "name",
    [s.name for s in health_check.WORKFLOW_SECRETS if s.kind in _FEED_KINDS],
)
def test_feed_secret_is_read_by_the_code_under_its_own_name(name: str) -> None:
    """The env name a workflow sets must be the one the code reads."""
    readers = [
        path
        for root in (REPO_ROOT / "src", REPO_ROOT / "scripts")
        for path in root.rglob("*.py")
        if path.name != "health_check.py"
        and f'"{name}"' in path.read_text(encoding="utf-8")
    ]
    assert readers, f"No code under src/ or scripts/ reads {name}"


def test_feed_workflows_pass_feed_secrets_under_their_own_name() -> None:
    pattern = re.compile(r"^\s*([A-Z_][A-Z0-9_]*):\s*\$\{\{\s*secrets\.([A-Z_][A-Z0-9_]*)\s*\}\}")
    feed = {s.name for s in health_check.WORKFLOW_SECRETS if s.kind in _FEED_KINDS}
    mismatched: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match and match.group(2) in feed and match.group(1) != match.group(2):
                mismatched.append(f"{path.name}: {line.strip()}")
    assert not mismatched, mismatched


def test_check_is_red_and_names_a_missing_required_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "   ")
    monkeypatch.setenv("VOR_BASE_URL", "")
    check = health_check.check_secrets()
    assert not check.ok
    assert "VOR_ACCESS_ID" in check.summary
    assert "Stammstrecken-Monitor" in check.detail


def test_check_is_green_and_never_prints_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "geheim-1234567890"
    monkeypatch.setenv("VOR_ACCESS_ID", value)
    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    check = health_check.check_secrets()
    assert check.ok
    assert not check.note
    assert check.summary == "OK — VOR_ACCESS_ID gesetzt"
    assert "VOR_BASE_URL leer" in check.detail
    assert value not in check.summary + check.detail


def test_optional_secret_set_leaves_no_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "x")
    monkeypatch.setenv("VOR_BASE_URL", "https://example.invalid/")
    check = health_check.check_secrets()
    assert check.ok
    assert check.summary == "OK — VOR_ACCESS_ID, VOR_BASE_URL gesetzt"
    assert check.detail == ""


def test_no_workflow_requires_an_optional_secret() -> None:
    """A secret ``WORKFLOW_SECRETS`` calls optional must not gate a workflow.

    ``VOR_BASE_URL`` falls back to the VAO host in ``src/providers/vor.py``;
    ``manual-full-refresh.yml`` failed and ``test-vor-api.yml`` skipped its
    smoke test when it was empty, although the Health check reports it as
    optional.
    """
    optional = {s.name for s in health_check.WORKFLOW_SECRETS if s.kind == "optional"}
    gate = re.compile(r'-z "\$\{([A-Z_]+):-\}"')
    for path in sorted(WORKFLOWS.glob("*.yml")):
        required_here = set(gate.findall(path.read_text(encoding="utf-8")))
        assert not (required_here & optional), (path.name, sorted(required_here & optional))
