"""Unified command-line entry point for project maintenance tasks."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

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


def _run(command: Sequence[str]) -> int:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return int(completed.returncode or 0)


def _run_script(script_name: str, *, python: str | None = None, extra_args: Sequence[str] | None = None) -> int:
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise CLIError(f"Script not found: {script_path}")

    interpreter = python or sys.executable
    command = [interpreter, str(script_path)]
    if extra_args:
        command.extend(extra_args)
    return _run(command)


def _add_python_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used to invoke helper scripts (default: current interpreter).",
    )


def _configure_cache_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cache_parser = subparsers.add_parser("cache", help="Cache maintenance commands")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)

    update_parser = cache_subparsers.add_parser("update", help="Refresh provider caches")
    update_parser.add_argument("provider", choices=sorted(_PROVIDER_CACHE_SCRIPTS))
    _add_python_argument(update_parser)
    update_parser.set_defaults(func=_handle_cache_update)


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


def _configure_token_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    token_parser = subparsers.add_parser("tokens", help="Credential diagnostics")
    token_subparsers = token_parser.add_subparsers(dest="tokens_command", required=True)

    verify_parser = token_subparsers.add_parser("verify", help="Validate available tokens or API keys")
    verify_parser.add_argument("target", choices=sorted(_TOKEN_VERIFY_SCRIPTS))
    _add_python_argument(verify_parser)
    verify_parser.set_defaults(func=_handle_token_verify)


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


def _handle_cache_update(args: argparse.Namespace) -> int:
    script_name = _PROVIDER_CACHE_SCRIPTS[args.provider]
    return _run_script(script_name, python=args.python)


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
    command = [args.python, "-m", "src.build_feed"]
    return _run(command)


def _handle_token_verify(args: argparse.Namespace) -> int:
    script_name = _TOKEN_VERIFY_SCRIPTS[args.target]
    return _run_script(script_name, python=args.python)


def _handle_checks(args: argparse.Namespace) -> int:
    extra: list[str] = []
    if args.fix:
        extra.append("--fix")
    if args.ruff_args:
        extra.append("--ruff-args")
        extra.extend(args.ruff_args)
    return _run_script("run_static_checks.py", python=args.python, extra_args=extra)


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
