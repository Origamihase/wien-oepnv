"""Every external action is pinned to a commit and names its full version.

Dependabot (``.github/dependabot.yml``) bumps a pinned action by replacing
the commit SHA and, in the comment behind it, the old version string. A
comment that names only the major version is not that string and stays as
it is: on 2026-11-01 #2105 moved ``actions/setup-python`` from 6.2.0 to
7.1.0 and left ``# v6`` on all 13 pins, and ``# v7`` on
``actions/upload-artifact`` 7.0.2 named no release. A full ``# vX.Y.Z``
is rewritten with the SHA (#2102: ``# v4.36.2`` became ``# v4.39.1``).

The same day showed the second gap: the Dependabot PRs were based on a
commit before ``ki-waechter.yml`` existed, so after their merge that one
workflow would have kept the old pins. One action therefore has one pin
across all workflows and composite actions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github"

_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
_PINNED_RE = re.compile(r"^(?P<action>[\w.-]+/[\w./-]+)@(?P<sha>[0-9a-f]{40})$")
_VERSION_COMMENT_RE = re.compile(r"^\s+#\s+(?P<version>v\d+\.\d+\.\d+)\s*$")


def _workflow_files() -> list[Path]:
    files = [*GITHUB.glob("workflows/*.yml"), *GITHUB.glob("workflows/*.yaml")]
    files += [*GITHUB.glob("actions/**/action.yml"), *GITHUB.glob("actions/**/action.yaml")]
    return sorted(files)


def _external_uses() -> list[tuple[str, str, str]]:
    """(location, ref, rest of line) for every ``uses:`` of a foreign action."""
    found: list[tuple[str, str, str]] = []
    for path in _workflow_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES_RE.match(line)
            if not match:
                continue
            ref = match.group("ref").strip("'\"")
            if ref.startswith(("./", "docker://")):
                continue
            found.append((f"{path.relative_to(ROOT)}:{number}", ref, match.group("rest")))
    return found


def test_scan_sees_the_workflows() -> None:
    locations = [location for location, _, _ in _external_uses()]
    assert any(loc.startswith(".github/workflows/update-cycle.yml:") for loc in locations)
    assert any(loc.startswith(".github/workflows/ki-waechter.yml:") for loc in locations)


def test_every_external_action_is_pinned_with_its_full_version() -> None:
    bad: list[str] = []
    for location, ref, rest in _external_uses():
        if not _PINNED_RE.match(ref):
            bad.append(f"{location}: {ref} (not pinned to a 40-character commit SHA)")
        elif not _VERSION_COMMENT_RE.match(rest):
            bad.append(f"{location}: {ref}{rest} (comment must be '# vX.Y.Z' of that commit)")
    assert not bad, "\n".join(bad)


def test_one_pin_per_action() -> None:
    pins: dict[str, set[str]] = defaultdict(set)
    for _location, ref, rest in _external_uses():
        match = _PINNED_RE.match(ref)
        if match:
            pins[match.group("action")].add(f"{match.group('sha')}{rest.rstrip()}")
    split = {action: sorted(found) for action, found in pins.items() if len(found) > 1}
    assert not split, f"actions pinned to more than one commit or version comment: {split}"
