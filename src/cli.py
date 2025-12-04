"""Unified command-line entry point for project maintenance tasks."""
from __future__ import annotations

import argparse
import runpy
import subprocess
import sys
from collections.abc import Mapping, Sequence, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import build_feed as build_feed_module
from .utils.stations_validation import validate_stations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_STATIONS_PATH = DATA_DIR / "stations.json"
DEFAULT_GTFS_PATH = DATA_DIR / "gtfs" / "stops.txt"

_PROVIDER_CACHE_SCRIPTS = {
    "wl": "update_wl_cache.py",
    "oebb": "update_oebb_cache.py",
    "vor": "update_vor_cache.py",
}

_STATION_UPDATE_SCRIPTS = {
    "all": "update_all_stations.py",
    "directory": "update_station_directory.py",
    "vor": "update_vor_stations.py",
    "wl": "update_wl_stations.py",
}

_TOKEN_VERIFY_SCRIPTS = {
    "vor": "verify_vor_access_id.py",
    "google-places": "verify_google_places_access.py",
    "vor-auth": "check_vor_auth.py",
}


class CLIError(RuntimeError):
    """Raised when the CLI cannot execute the requested command."""


@contextmanager
def _patched_argv(script_path: Path, arguments: Sequence[str] | None) -> Iterator[None]:
    original = sys.argv[:]
    sys.argv = [str(script_path)] + list(arguments or [])
    try:
        yield
    finally:
        sys.argv = original


def _run_script(
    script_name: str,
    *,
    python: str | None = None,
    extra_args: Sequence[str] | None = None,
) -> int:
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise CLIError(f"Script not found: {script_path}")

    cleaned_args = _clean_remainder(list(extra_args or []))
    selected_python = python or sys.executable

    if selected_python == sys.executable:
        with _patched_argv(script_path, cleaned_args):
            runpy.run_path(str(script_path), run_name="__main__")
        return 0

    command = [selected_python, str(script_path), *cleaned_args]
    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - exercised via CLI
        raise CLIError(f"Python interpreter not found: {selected_python}") from exc

    return int(result.returncode)


def _unique_preserving_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _resolve_targets(
    candidates: Sequence[str],
    *,
    all_flag: bool,
    available: Mapping[str, str],
    parser: argparse.ArgumentParser,
    default_all: bool,
    subject: str,
) -> list[str]:
    available_keys = list(available.keys())
    if all_flag:
        if candidates:
            parser.error(f"--all darf nicht gleichzeitig mit individuellen {subject}-Angaben verwendet werden.")
        return available_keys

    if not candidates:
        if default_all:
            return available_keys
        parser.error(f"Es wurden keine {subject} angegeben.")

    invalid = [candidate for candidate in candidates if candidate not in available]
    if invalid:
        formatted = ", ".join(sorted(invalid))
        parser.error(f"Unbekannte {subject}: {formatted}")

    return _unique_preserving_order(candidates)


def _add_python_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used to invoke helper scripts (default: current interpreter).",
    )


def _clean_remainder(values: list[str] | None) -> list[str]:
    cleaned = list(values or [])
    while cleaned and cleaned[0] == "--":
        cleaned.pop(0)
    return cleaned


def _configure_cache_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cache_parser = subparsers.add_parser("cache", help="Cache maintenance commands")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)

    update_parser = cache_subparsers.add_parser("update", help="Refresh provider caches")
    update_parser.add_argument(
        "providers",
        nargs="*",
        metavar="PROVIDER",
        help="Provider identifiers (wl, oebb, vor). Ohne Angabe werden alle Caches aktualisiert.",
    )
    update_parser.add_argument(
        "--all",
        action="store_true",
        help="Aktualisiert alle Caches unabhängig von expliziten Providerangaben.",
    )
    update_parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Bricht nach dem ersten fehlgeschlagenen Lauf ab (Default: führt alle Läufe aus).",
    )
    _add_python_argument(update_parser)
    update_parser.set_defaults(func=_handle_cache_update, parser=update_parser)


def _configure_stations_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    stations_parser = subparsers.add_parser("stations", help="Stations directory utilities")
    stations_subparsers = stations_parser.add_subparsers(dest="stations_command", required=True)

    update_parser = stations_subparsers.add_parser("update", help="Run the legacy update scripts")
    update_parser.add_argument("target", choices=sorted(_STATION_UPDATE_SCRIPTS))
    update_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging for the invoked script.",
    )
    _add_python_argument(update_parser)
    update_parser.set_defaults(func=_handle_stations_update)

    validate_parser = stations_subparsers.add_parser("validate", help="Generate a stations quality report")
    validate_parser.add_argument(
        "--stations",
        type=Path,
        default=DEFAULT_STATIONS_PATH,
        help=f"Path to stations.json (default: {DEFAULT_STATIONS_PATH})",
    )
    validate_parser.add_argument(
        "--gtfs",
        type=Path,
        default=DEFAULT_GTFS_PATH,
        help=f"Path to GTFS stops.txt (default: {DEFAULT_GTFS_PATH})",
    )
    validate_parser.add_argument(
        "--decimal-places",
        type=int,
        default=5,
        help="Number of decimal places used when matching coordinates (default: 5).",
    )
    validate_parser.add_argument(
        "--output",
        type=Path,
        help="Optional file path for the Markdown report.",
    )
    validate_parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Exit with a non-zero status code when any issues are found.",
    )
    validate_parser.set_defaults(func=_handle_stations_validate)


def _configure_feed_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    feed_parser = subparsers.add_parser("feed", help="Feed generation helpers")
    feed_subparsers = feed_parser.add_subparsers(dest="feed_command", required=True)

    build_parser = feed_subparsers.add_parser("build", help="Run the feed builder")
    _add_python_argument(build_parser)
    build_parser.set_defaults(func=_handle_feed_build)

    lint_parser = feed_subparsers.add_parser(
        "lint", help="Analyse cached Items auf strukturelle Probleme"
    )
    lint_parser.set_defaults(func=_handle_feed_lint)


def _configure_token_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    token_parser = subparsers.add_parser("tokens", help="Credential diagnostics")
    token_subparsers = token_parser.add_subparsers(dest="tokens_command", required=True)

    verify_parser = token_subparsers.add_parser("verify", help="Validate available tokens or API keys")
    verify_parser.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="Zu prüfende Zugangsdaten (vor, vor-auth, google-places). Ohne Angabe werden alle überprüft.",
    )
    verify_parser.add_argument(
        "--all",
        action="store_true",
        help="Prüft alle bekannten Zugangsdaten.",
    )
    verify_parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Beendet den Lauf nach dem ersten Fehler (Standard: versucht alle Prüfungen).",
    )
    _add_python_argument(verify_parser)
    verify_parser.set_defaults(func=_handle_token_verify, parser=verify_parser)


def _configure_checks_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    checks_parser = subparsers.add_parser("checks", help="Static analysis convenience wrapper")
    checks_parser.add_argument("--fix", action="store_true", help="Allow ruff to apply autofixes before running mypy.")
    checks_parser.add_argument(
        "--ruff-args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to 'ruff check'.",
    )
    _add_python_argument(checks_parser)
    checks_parser.set_defaults(func=_handle_checks)


def _configure_config_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config_parser = subparsers.add_parser("config", help="Configuration utilities")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    wizard_parser = config_subparsers.add_parser("wizard", help="Interactive configuration assistant")
    _add_python_argument(wizard_parser)
    wizard_parser.add_argument(
        "wizard_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to scripts/configure_feed.py (prefix with --).",
    )
    wizard_parser.set_defaults(func=_handle_config_wizard)


def _configure_security_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    security_parser = subparsers.add_parser("security", help="Security and compliance helpers")
    security_subparsers = security_parser.add_subparsers(dest="security_command", required=True)

    scan_parser = security_subparsers.add_parser("scan", help="Scan the repository for leaked secrets")
    _add_python_argument(scan_parser)
    scan_parser.add_argument(
        "scan_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to scripts/scan_secrets.py (prefix with --).",
    )
    scan_parser.set_defaults(func=_handle_security_scan)


def _handle_cache_update(args: argparse.Namespace) -> int:
    providers = _resolve_targets(
        args.providers,
        all_flag=args.all,
        available=_PROVIDER_CACHE_SCRIPTS,
        parser=args.parser,
        default_all=True,
        subject="Provider",
    )

    exit_code = 0
    for provider in providers:
        script_name = _PROVIDER_CACHE_SCRIPTS[provider]
        result = _run_script(script_name, python=args.python)
        if result != 0:
            if args.stop_on_error:
                return result
            if exit_code == 0:
                exit_code = result
    return exit_code


def _handle_stations_update(args: argparse.Namespace) -> int:
    script_name = _STATION_UPDATE_SCRIPTS[args.target]
    extra: list[str] = []
    if args.verbose:
        extra.append("--verbose")
    return _run_script(script_name, python=args.python, extra_args=extra)


def _handle_stations_validate(args: argparse.Namespace) -> int:
    report = validate_stations(
        args.stations,
        gtfs_stops_path=args.gtfs,
        decimal_places=args.decimal_places,
    )

    markdown = report.to_markdown()
    print(markdown, end="")

    if args.output:
        output_path: Path = args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"Report written to {output_path}")

    if args.fail_on_issues and report.has_issues:
        return 1
    return 0


def _handle_feed_build(args: argparse.Namespace) -> int:
    del args.python  # Execution happens in-process.
    return int(build_feed_module.main())


def _handle_feed_lint(_args: argparse.Namespace) -> int:
    return int(build_feed_module.lint())


def _handle_token_verify(args: argparse.Namespace) -> int:
    targets = _resolve_targets(
        args.targets,
        all_flag=args.all,
        available=_TOKEN_VERIFY_SCRIPTS,
        parser=args.parser,
        default_all=True,
        subject="Token-Prüfziele",
    )

    exit_code = 0
    for target in targets:
        script_name = _TOKEN_VERIFY_SCRIPTS[target]
        result = _run_script(script_name, python=args.python)
        if result != 0:
            if args.stop_on_error:
                return result
            if exit_code == 0:
                exit_code = result
    return exit_code


def _handle_checks(args: argparse.Namespace) -> int:
    extra: list[str] = []
    if args.fix:
        extra.append("--fix")
    if args.ruff_args:
        extra.append("--ruff-args")
        extra.extend(args.ruff_args)
    return _run_script("run_static_checks.py", python=args.python, extra_args=extra)


def _handle_config_wizard(args: argparse.Namespace) -> int:
    extra = _clean_remainder(args.wizard_args)
    return _run_script("configure_feed.py", python=args.python, extra_args=extra)


def _handle_security_scan(args: argparse.Namespace) -> int:
    extra = _clean_remainder(args.scan_args)
    return _run_script("scan_secrets.py", python=args.python, extra_args=extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wien-oepnv",
        description="Unified CLI for feed, cache and station maintenance tasks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _configure_cache_commands(subparsers)
    _configure_stations_commands(subparsers)
    _configure_feed_commands(subparsers)
    _configure_token_commands(subparsers)
    _configure_checks_commands(subparsers)
    _configure_config_commands(subparsers)
    _configure_security_commands(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return handler(args)
    except CLIError as exc:
        parser.error(str(exc))


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
