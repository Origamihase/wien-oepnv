#!/usr/bin/env python3
"""Interactive helper to create or update the Wien ÖPNV configuration."""

from __future__ import annotations

import argparse
import sys
import textwrap
from getpass import getpass
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover - allow execution via package and script
    from utils.configuration_wizard import (
        ConfigOption,
        CONFIG_OPTIONS,
        ConfigurationError,
        calculate_changes,
        compute_non_interactive_configuration,
        format_env_document,
        mask_value,
        normalize_existing_values,
    )
    from utils.env import load_env_file
except ModuleNotFoundError:  # pragma: no cover - allow python scripts/configure_feed.py
    from src.utils.configuration_wizard import (  # type: ignore
        ConfigOption,
        CONFIG_OPTIONS,
        ConfigurationError,
        calculate_changes,
        compute_non_interactive_configuration,
        format_env_document,
        mask_value,
        normalize_existing_values,
    )
    from src.utils.env import load_env_file  # type: ignore

_OPTION_BY_KEY: Dict[str, ConfigOption] = {option.key: option for option in CONFIG_OPTIONS}


def _parse_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"Ungültiges --set Argument: {item!r}. Erwartet KEY=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit("Schlüssel in --set darf nicht leer sein.")
        overrides[key] = value
    return overrides


def _print_header(path: Path) -> None:
    print("Konfigurations-Assistent für den Wien ÖPNV Feed")
    print("=" * 55)
    print(f"Zieldatei: {path}")


def _render_help(option) -> None:
    wrapped = textwrap.fill(option.help, width=78, initial_indent="  ", subsequent_indent="  ")
    print()
    print(f"{option.label} ({option.key})")
    print(wrapped)


def _prompt_for_option(option, current: str | None) -> str | None:
    default_display = current or option.default_str()
    while True:
        if option.kind == "bool":
            default_bool = current or option.default_str() or "true"
            prompt = "[Y/n]" if default_bool == "true" else "[y/N]"
            raw = input(f"  Auswahl {prompt}: ")
            candidate = raw.strip() or default_bool
        elif option.kind == "secret":
            status = "gesetzt" if current else "nicht gesetzt"
            raw = getpass(f"  Token eingeben (aktuell {status}): ")
            if not raw and current:
                return current
            if not raw:
                return None
            candidate = raw
        else:
            suffix = f" [{default_display}]" if default_display else ""
            raw = input(f"  Wert{suffix}: ")
            candidate = raw.strip()
            if not candidate:
                if current:
                    return current
                default_value = option.default_str()
                if default_value:
                    candidate = default_value
                elif option.required:
                    print("  -> Ein Wert ist erforderlich.")
                    continue
                else:
                    return None
        try:
            normalized = option.normalize(candidate)
        except ConfigurationError as exc:
            print(f"  -> {exc}")
            continue
        if not normalized and not option.required and option.kind == "secret":
            return None
        if not normalized and option.required:
            print("  -> Ein Wert ist erforderlich.")
            continue
        return normalized


def _summarize_changes(changes: dict[str, tuple[str | None, str | None]]) -> None:
    if not changes:
        print("Keine Änderungen notwendig – Konfiguration bleibt unverändert.")
        return
    print("Zusammenfassung der Änderungen:")
    for key in sorted(changes):
        before, after = changes[key]
        if after is None or after == "":
            after_display = "<entfernt>"
        elif key.endswith("_ACCESS_ID"):
            after_display = mask_value(after)
        else:
            after_display = after
        if before is None or before == "":
            before_display = "<leer>"
        elif key.endswith("_ACCESS_ID"):
            before_display = mask_value(before)
        else:
            before_display = before
        print(f"  - {key}: {before_display} -> {after_display}")


def _load_existing(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    load_env_file(path, environ=env)
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(BASE_DIR / ".env"),
        help="Pfad zur .env-Datei (Standard: Projektwurzel/.env).",
    )
    parser.add_argument(
        "--accept-defaults",
        "--non-interactive",
        action="store_true",
        help="Keine Rückfragen stellen; vorhandene Werte oder Defaults übernehmen.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Überschreibt einen Wert, ohne interaktive Abfrage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Schreibt keine Dateien, sondern zeigt nur die berechnete Konfiguration an.",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = (BASE_DIR / env_path).resolve()

    overrides = _parse_overrides(list(args.set))
    existing_env = _load_existing(env_path)

    if args.accept_defaults:
        result = compute_non_interactive_configuration(
            existing_env,
            overrides,
            accept_defaults=True,
        )
        document = format_env_document(result.managed, result.custom)
        _summarize_changes(result.changes)
        for warning in result.warnings:
            print(f"Warnung: {warning}")
        if args.dry_run:
            print()
            print(document)
            return 0
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(document, encoding="utf-8")
        print(f"Konfiguration wurde nach {env_path} geschrieben.")
        return 0

    _print_header(env_path)
    normalized_existing, warnings = normalize_existing_values(existing_env)
    base = compute_non_interactive_configuration(
        existing_env,
        overrides,
        accept_defaults=False,
    )
    managed = dict(base.managed)
    custom = dict(base.custom)

    seen_warnings: set[str] = set()
    for warning in warnings + base.warnings:
        if warning in seen_warnings:
            continue
        seen_warnings.add(warning)
        print(f"Warnung: {warning}")

    for option in CONFIG_OPTIONS:
        current = managed.get(option.key)
        _render_help(option)
        response = _prompt_for_option(option, current)
        if response:
            managed[option.key] = response
        else:
            managed.pop(option.key, None)

    if "VOR_ACCESS_ID" not in managed:
        print(
            "Hinweis: VOR_ACCESS_ID ist nicht gesetzt. Ohne Token kann der VOR-Provider nicht genutzt werden."
        )

    previous_combined = {**normalized_existing, **{k: v for k, v in existing_env.items() if k not in _OPTION_BY_KEY}}
    final_combined = {**managed, **custom}
    changes = calculate_changes(previous_combined, final_combined)

    document = format_env_document(managed, custom)
    print()
    _summarize_changes(changes)

    if args.dry_run:
        print()
        print(document)
        return 0

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(document, encoding="utf-8")
    print(f"Konfiguration wurde nach {env_path} geschrieben.")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
