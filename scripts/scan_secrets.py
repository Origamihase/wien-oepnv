#!/usr/bin/env python3
"""Scan the repository for accidentally committed secrets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover - executed both as script and module
    from utils.secret_scanner import load_ignore_file, scan_repository
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.secret_scanner import (  # type: ignore
        load_ignore_file,
        scan_repository,
    )


def _parse_paths(values: list[str], base_dir: Path) -> list[Path]:
    if not values:
        return []
    parsed: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        parsed.append(path)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optionale Pfade oder Dateien, die geprüft werden sollen.",
    )
    parser.add_argument(
        "--base-dir",
        default=str(BASE_DIR),
        help="Projektwurzel (Standard: Speicherort des Skripts).",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Setzt den Exit-Code immer auf 0, selbst wenn Treffer gefunden werden.",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Zusätzliche Glob-Pattern, die von der Prüfung ausgeschlossen werden.",
    )
    args = parser.parse_args(argv)

    base_dir = Path(args.base_dir).resolve()
    include_paths = _parse_paths(args.paths, base_dir)
    ignore_patterns = load_ignore_file(base_dir)
    if args.ignore:
        ignore_patterns.extend(args.ignore)

    findings = scan_repository(
        base_dir,
        paths=include_paths or None,
        ignore_patterns=ignore_patterns,
    )

    if not findings:
        print("Keine potentiellen Secrets gefunden.")
        return 0

    print(f"{len(findings)} potentiell sensible Einträge gefunden:")
    for finding in findings:
        relative = finding.path.relative_to(base_dir)
        print(
            f"  {relative}:{finding.line_number}: {finding.reason} -> {finding.match}"
        )
    return 0 if args.no_fail else 1


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
