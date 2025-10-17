#!/usr/bin/env python3
"""Ensure that a VOR access token is available before running API tools.

The script can be used in CI/CD pipelines or deployment hooks to fail early
when the access token is missing.  It attempts to populate environment
variables from the default secret files (``.env``, ``data/secrets.env``,
``config/secrets.env``) before performing the check.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover
    from utils.env import load_default_env_files
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.env import load_default_env_files  # type: ignore


REQUIRED_VARS: tuple[str, ...] = ("VOR_ACCESS_ID",)


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def ensure_required_secrets(
    *,
    environ: MutableMapping[str, str] | None = None,
    auto_load: bool = True,
) -> tuple[bool, Mapping[str, str]]:
    """Return whether the required secrets are present.

    Parameters
    ----------
    environ:
        Environment mapping to inspect.  Defaults to ``os.environ``.
    auto_load:
        When ``True`` the helper loads the default secret files before
        checking the variables.
    """

    env: MutableMapping[str, str]
    env = environ if environ is not None else os.environ

    loaded_files: Mapping[Path, Mapping[str, str]]
    loaded_files = {}
    if auto_load:
        loaded_files = load_default_env_files(environ=env)

    missing: list[str] = []
    present: dict[str, str] = {}
    for key in REQUIRED_VARS:
        value = env.get(key, "").strip()
        if value:
            present[key] = value
        else:
            missing.append(key)

    success = not missing

    if success:
        message_lines = [
            "Alle benötigten Secrets sind gesetzt:",
            *[f"  - {name}={_mask(value)}" for name, value in present.items()],
        ]
        print("\n".join(message_lines))
    else:
        message_lines = [
            "Fehlende Secrets erkannt:",
            *[f"  - {name}" for name in missing],
            "",
            "Bitte stelle sicher, dass die Secrets als Umgebungsvariablen verfügbar sind",
            "oder trage sie in eine der Standarddateien (.env, data/secrets.env,",
            "config/secrets.env) ein.",
        ]
        print("\n".join(message_lines), file=sys.stderr)

    return success, {str(path): dict(values) for path, values in loaded_files.items()}


def main(argv: Iterable[str] | None = None) -> int:
    success, _ = ensure_required_secrets()
    return 0 if success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
